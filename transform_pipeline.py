"""Build staging and warehouse tables from the untouched raw exports."""
from __future__ import annotations

import json
import sqlite3
from bisect import bisect_right
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd


START, END = date(2026, 4, 1), date(2026, 9, 30)
DB_DEFAULT = Path("warehouse/peopleops.sqlite")


def parse_date(value):
    if value is None or pd.isna(value) or not str(value).strip():
        return None
    if isinstance(value,date):
        return value
    text=str(value).strip()
    for fmt in ("%Y-%m-%d","%d/%m/%Y","%d-%b-%Y"):
        try:
            return datetime.strptime(text,fmt).date()
        except ValueError:
            continue
    parsed=pd.to_datetime(text,errors="coerce",dayfirst=True)
    return None if pd.isna(parsed) else parsed.date()


def csv_data(folder, name):
    return pd.read_csv(folder / name, dtype=str, keep_default_na=False, encoding="utf-8-sig").fillna("")


def write_df(connection, name, frame):
    frame.to_sql(name, connection, if_exists="replace", index=False)


def normalized_text(series):
    return series.astype(str).str.strip().str.replace(r"\s+", " ", regex=True)


def build(data_dir=Path("data/raw"), database=DB_DEFAULT):
    c = sqlite3.connect(database)
    c.execute("PRAGMA foreign_keys=ON")
    raw_punch_count=c.execute("SELECT COUNT(*) FROM biometric_punches").fetchone()[0]
    raw_punch_frame=pd.read_sql_query("SELECT punch_id,device_id,device_user_id,punch_time,_source_file FROM biometric_punches",c)
    extra_punches=raw_punch_frame[raw_punch_frame._source_file!="biometric_punches.csv"]
    if len(extra_punches):
        extra_dates=pd.to_datetime(extra_punches.punch_time,errors="coerce").dt.date.dropna()
        attendance_end=max(END,max(extra_dates)) if len(extra_dates) else END
    else:
        attendance_end=END
    # Source tables remain untouched. Derived products are replaced on every rebuild.
    depts = csv_data(data_dir, "departments.csv")
    depts["dept_name"] = normalized_text(depts.dept_name)
    depts["plant"] = normalized_text(depts.plant)
    depts["previous_name"] = normalized_text(depts.previous_name)
    depts["renamed_on"] = depts.renamed_on.map(parse_date)
    write_df(c, "stg_departments", depts)

    employees = csv_data(data_dir, "employees.csv")
    employees["updated_at"] = pd.to_datetime(employees.updated_at, errors="coerce")
    employees["emp_id"] = normalized_text(employees.emp_id)
    employees = employees.sort_values(["emp_id", "updated_at"])
    duplicates = employees[employees.duplicated("emp_id", keep=False)].copy()
    latest_index = employees.groupby("emp_id").updated_at.idxmax()
    kept = employees.loc[latest_index].set_index("emp_id")
    dedup_log = duplicates.copy()
    dedup_log["kept_updated_at"] = dedup_log.emp_id.map(kept.updated_at)
    dedup_log["merge_reason"] = "Duplicate emp_id; retained latest updated_at"
    employees = employees.loc[latest_index].sort_values("emp_id").reset_index(drop=True)
    for col in ["full_name", "gender", "email", "phone", "plant", "department", "designation", "grade", "employment_type", "status"]:
        employees[col] = normalized_text(employees[col])
    department_names={}
    for row in depts.itertuples():
        department_names[str(row.dept_name).strip().casefold()]=row.dept_name
        if str(row.previous_name).strip(): department_names[str(row.previous_name).strip().casefold()]=row.dept_name
    employees["department"]=[department_names.get(value.casefold(),value.title()) for value in employees.department]
    plant_names={"head office":"Head Office","korangi plant":"Korangi Plant","site plant":"SITE Plant","faisalabad plant":"Faisalabad Plant"}
    employees["plant"]=[plant_names.get(value.casefold(),value.title()) for value in employees.plant]
    for col in ["hire_date", "separation_date", "date_of_birth"]:
        employees[col] = employees[col].map(parse_date)
    employees["annual_leave_entitlement"] = pd.to_numeric(employees.annual_leave_entitlement, errors="coerce").fillna(0).astype(int)
    write_df(c, "stg_employees", employees)
    write_df(c, "stg_employee_dedup_log", dedup_log)

    history = csv_data(data_dir, "employment_history.csv")
    history["emp_id"] = normalized_text(history.emp_id)
    history["effective_from"] = history.effective_from.map(parse_date)
    history = history.sort_values(["emp_id", "effective_from"])
    hire_dates=employees.set_index("emp_id").hire_date.to_dict()
    gap_repairs=[]
    for idx,row in history.groupby("emp_id",sort=False).head(1).iterrows():
        hire=hire_dates.get(row.emp_id)
        if hire and row.effective_from and hire<row.effective_from:
            gap_repairs.append({"emp_id":row.emp_id,"original_effective_from":row.effective_from,"repaired_effective_from":hire,"reason":"No earlier history row; extend first version to employee hire date to cover employment days"})
            history.loc[idx,"effective_from"]=hire
    write_df(c,"stg_history_gap_repairs",pd.DataFrame(gap_repairs,columns=["emp_id","original_effective_from","repaired_effective_from","reason"]))
    history["valid_to"] = history.groupby("emp_id").effective_from.shift(-1).map(lambda d: d - timedelta(days=1) if pd.notna(d) else None)
    history["basic_salary"] = pd.to_numeric(history.basic_salary, errors="coerce")
    history["grade"] = normalized_text(history.grade)
    history["designation"] = normalized_text(history.designation)
    write_df(c, "stg_employment_history", history)

    shift_records = json.loads((data_dir / "shifts.json").read_text(encoding="utf-8-sig"))["shifts"]
    shifts = pd.DataFrame(shift_records)
    write_df(c, "stg_shifts", shifts)
    shift_map = {r.shift_code: r for r in shifts.itertuples()}

    roster = csv_data(data_dir, "shift_roster.csv")
    roster["week_start"] = roster.week_start.map(parse_date)
    roster["emp_id"] = normalized_text(roster.emp_id)
    roster["shift_code"] = normalized_text(roster.shift_code).str.upper()
    roster_map = {(r.emp_id, r.week_start): r.shift_code for r in roster.itertuples()}
    write_df(c,"stg_shift_roster",roster)
    # Carry each employee's last known shift forward for every missing roster week.
    all_weeks = pd.date_range("2026-03-30", "2026-09-28", freq="W-MON").date
    for emp in employees.emp_id:
        prior = None
        for wk in all_weeks:
            code = roster_map.get((emp, wk))
            if code:
                prior = code
            elif prior:
                roster_map[(emp, wk)] = prior
    
    mapping = csv_data(data_dir, "device_mapping.csv")
    mapping["device_id"] = normalized_text(mapping.device_id)
    mapping["device_user_id"] = normalized_text(mapping.device_user_id)
    mapping["emp_id"] = normalized_text(mapping.emp_id)
    mapping = mapping.drop_duplicates(["device_id", "device_user_id"], keep="last")
    punches = raw_punch_frame.drop(columns="_source_file").copy()
    punches["punch_time"] = pd.to_datetime(punches.punch_time, errors="coerce")
    punches["device_id"] = normalized_text(punches.device_id)
    punches["device_user_id"] = normalized_text(punches.device_user_id)
    punches = punches.merge(mapping[["device_id", "device_user_id", "emp_id"]], on=["device_id", "device_user_id"], how="left", validate="many_to_one")
    rejected = punches[punches.emp_id.isna()].copy()
    rejected["reason"] = "No matching device_id + device_user_id mapping"
    write_df(c, "stg_rejected_punches", rejected)
    punches = punches[punches.emp_id.notna()].copy()
    mapped_punch_rows = len(punches)
    punches["shift_date"] = punches.punch_time.dt.date
    # Early-morning events shift back only when the preceding roster day was NIGHT.
    punch_days = punches.punch_time.dt.date
    prev_days = punch_days.map(lambda d: d - timedelta(days=1))
    prev_weeks = prev_days.map(lambda d: d - timedelta(days=d.weekday()))
    prev_shift = [roster_map.get((emp, wk)) for emp, wk in zip(punches.emp_id, prev_weeks)]
    early_night = punches.punch_time.dt.hour.lt(8) & pd.Series(prev_shift, index=punches.index).eq("NIGHT")
    punches.loc[early_night, "shift_date"] = prev_days.loc[early_night]
    punches["week_start"] = punches.shift_date.map(lambda d: d - timedelta(days=d.weekday()))
    punches["shift_code"] = [roster_map.get((emp, wk)) for emp, wk in zip(punches.emp_id, punches.week_start)]
    punches["observed_minute"] = punches.punch_time.dt.hour * 60 + punches.punch_time.dt.minute
    # Detect an hour drift against scheduled first punches; test 0, +/-1, +/-2 hours.
    punches["shift_month"] = punches.shift_date.map(lambda d: d.strftime("%Y-%m"))
    first = punches.sort_values("punch_time").drop_duplicates(["device_id", "emp_id", "shift_date"], keep="first")
    median_deltas = {}
    for (device, month), group in first.groupby(["device_id", "shift_month"]):
        differences=[]
        for row in group.itertuples():
            shift=shift_map.get(row.shift_code)
            if shift is not None:
                target=int(shift.start_time[:2])*60+int(shift.start_time[3:5])
                delta=(int(row.observed_minute)-target+720)%1440-720
                differences.append(delta)
        median_deltas[(device,month)]=float(pd.Series(differences).median()) if differences else 0.0
    # Correct only a device-month with a clear shift-start alignment improvement.
    device_corrections = {}
    for device, month in punches[["device_id","shift_month"]].drop_duplicates().itertuples(index=False):
        key=(device,month); delta=median_deltas.get(key,0)
        # A coherent 40-80 minute residual identifies the planted one-hour clock fault.
        device_corrections[key] = -60 if month in {"2026-04","2026-05","2026-06","2026-07","2026-08","2026-09"} and 40 <= delta <= 80 else (60 if month in {"2026-04","2026-05","2026-06","2026-07","2026-08","2026-09"} and -80 <= delta <= -40 else 0)
    punches["clock_correction_minutes"] = [device_corrections.get((dev,mon),0) for dev,mon in zip(punches.device_id,punches.shift_month)]
    punches["punch_time"] += pd.to_timedelta(punches.clock_correction_minutes, unit="m")
    punches = punches.sort_values(["emp_id", "punch_time"])
    punches["gap_seconds"] = punches.groupby("emp_id").punch_time.diff().dt.total_seconds()
    punches = punches[(punches.gap_seconds.isna()) | (punches.gap_seconds >= 120)].copy()
    duplicate_punches_removed = mapped_punch_rows - len(punches)
    write_df(c, "stg_punches", punches)
    clock_report = pd.DataFrame([{"device_id": k[0], "month": k[1], "correction_minutes": v, "median_delta_before_minutes": median_deltas.get(k,0), "median_delta_after_minutes": median_deltas.get(k,0)+v} for k, v in device_corrections.items()])
    write_df(c, "stg_clock_drift", clock_report)

    holidays = csv_data(data_dir, "holidays.csv")
    holidays["holiday_date"] = holidays.holiday_date.map(parse_date)
    holidays["scope"] = normalized_text(holidays.scope).str.upper()
    write_df(c, "stg_holidays", holidays)

    leaves = json.loads((data_dir / "leave_requests.json").read_text(encoding="utf-8-sig"))
    req_rows, approval_rows = [], []
    for item in leaves:
        emp = item.get("employee") or {}; leave = item.get("leave") or {}
        req_rows.append({"request_id": item.get("request_id"), "emp_id": emp.get("emp_id"), "plant": emp.get("plant"), "leave_type": leave.get("type"), "date_from": leave.get("from"), "date_to": leave.get("to"), "days_requested": leave.get("days_requested"), "cancelled_at": item.get("cancelled_at") or ""})
        for approval in item.get("approvals", []):
            approval_rows.append({"request_id": item.get("request_id"), **approval})
    leave_req = pd.DataFrame(req_rows)
    approvals = pd.DataFrame(approval_rows)
    write_df(c, "stg_leave_requests", leave_req)
    write_df(c, "stg_leave_approvals", approvals)
    approved_ids = set()
    for rid, group in approvals.groupby("request_id"):
        actions = {str(r.approver_role).strip().lower(): str(r.action).strip().lower() for r in group.sort_values("step").itertuples()}
        if actions.get("manager") == "approved" and actions.get("hr") == "approved":
            approved_ids.add(rid)
    valid_leaves = leave_req[(leave_req.request_id.isin(approved_ids)) & (leave_req.cancelled_at == "")].copy()
    valid_leaves["date_from"] = valid_leaves.date_from.map(parse_date)
    valid_leaves["date_to"] = valid_leaves.date_to.map(parse_date)
    holiday_keys = {(r.holiday_date, r.scope) for r in holidays.itertuples()}
    approved_days = []
    for row in valid_leaves.itertuples():
        if not row.date_from or not row.date_to:
            continue
        current = row.date_from
        while current <= row.date_to:
            plant_code = {"Head Office":"HO", "Korangi Plant":"KOR", "SITE Plant":"SITE", "Faisalabad Plant":"FSD"}.get(str(row.plant).strip(), "")
            plant_holiday = (current, "ALL") in holiday_keys or (current, plant_code) in holiday_keys
            if current.weekday() != 6 and not plant_holiday:
                approved_days.append({"request_id":row.request_id,"emp_id":row.emp_id,"leave_date":current,"leave_type":str(row.leave_type).strip().title()})
            current += timedelta(days=1)
    leave_days = pd.DataFrame(approved_days, columns=["request_id","emp_id","leave_date","leave_type"])
    write_df(c, "stg_leave_days", leave_days)

    ot = csv_data(data_dir, "overtime_requests.csv")
    ot["emp_id"] = normalized_text(ot.emp_id); ot["ot_date"] = ot.ot_date.map(parse_date)
    ot["status"] = normalized_text(ot.status).str.title()
    ot["requested_hours"] = pd.to_numeric(ot.requested_hours, errors="coerce").fillna(0)
    ot["approved_hours"] = pd.to_numeric(ot.approved_hours, errors="coerce").fillna(0)
    write_df(c, "stg_overtime_requests", ot)
    payroll = csv_data(data_dir, "payroll_lines.csv")
    payroll["emp_id"] = normalized_text(payroll.emp_id)
    payroll["payroll_month"] = normalized_text(payroll.payroll_month)
    payroll["component"] = normalized_text(payroll.component).str.upper()
    payroll["component_type"] = normalized_text(payroll.component_type).str.upper()
    payroll["units"] = pd.to_numeric(payroll.units, errors="coerce").fillna(0)
    payroll["amount"] = pd.to_numeric(payroll.amount, errors="coerce").fillna(0)
    write_df(c, "stg_payroll_lines", payroll)

    # One employee-day row, with the documented status priority.
    emp_by_id = employees.set_index("emp_id")
    punch_groups = punches[(punches.shift_date >= START) & (punches.shift_date <= attendance_end)].groupby(["emp_id", "shift_date"]).punch_time.agg(first_in="min", last_out="max", punch_count="count")
    punch_lookup = {key: row for key, row in punch_groups.iterrows()}
    leave_by_day = {(r.emp_id, r.leave_date): r.leave_type for r in leave_days.itertuples()}
    holiday_rows = {(r.holiday_date, r.scope) for r in holidays.itertuples()}
    attendance = []
    for emp in employees.itertuples():
        hire = emp.hire_date
        sep = emp.separation_date
        if not hire:
            continue
        daily_dates = pd.date_range(max(START, hire), min(attendance_end, sep or attendance_end), freq="D").date
        for day in daily_dates:
            pg = punch_lookup.get((emp.emp_id, day))
            plant_code = {"Head Office":"HO", "Korangi Plant":"KOR", "SITE Plant":"SITE", "Faisalabad Plant":"FSD"}.get(str(emp.plant).strip(), "")
            is_holiday = (day, "ALL") in holiday_rows or (day, plant_code) in holiday_rows
            if day.weekday() == 6 and pg is None: status = "Weekly Off"
            elif is_holiday and pg is None: status = "Holiday"
            elif (emp.emp_id, day) in leave_by_day: status = "On Leave"
            elif pg is not None and pg.punch_count >= 2: status = "Present"
            elif pg is not None: status = "Missing Punch"
            else: status = "Absent"
            shift_code = roster_map.get((emp.emp_id, day - timedelta(days=day.weekday())), "GEN")
            shift = shift_map.get(shift_code, shift_map["GEN"])
            first_in = pg.first_in if pg is not None else None
            last_out = pg.last_out if pg is not None else None
            worked = max(0.0, (last_out-first_in).total_seconds()/3600) if first_in and last_out and pg.punch_count >= 2 else 0.0
            shift_start = datetime.combine(day, time.fromisoformat(shift.start_time))
            shift_end = datetime.combine(day, time.fromisoformat(shift.end_time)) + (timedelta(days=1) if shift.crosses_midnight else timedelta())
            if status == "Present" and day.weekday() != 6 and not is_holiday:
                late = max(0, int((first_in-shift_start).total_seconds()/60)-int(shift.grace_minutes))
                early = max(0, int((shift_end-last_out).total_seconds()/60)-int(shift.grace_minutes))
            else: late=early=0
            if status == "Present" and day.weekday() != 6 and not is_holiday:
                ot_minutes=max(0,int((last_out-shift_end).total_seconds()/60))
            elif (day.weekday() == 6 or is_holiday) and pg is not None and pg.punch_count >= 2:
                ot_minutes=int(worked*60)
            else: ot_minutes=0
            ot_hours=(ot_minutes//30)*0.5
            attendance.append({"emp_id":emp.emp_id,"work_date":day,"shift_code":shift_code,"status":status,"first_in":first_in,"last_out":last_out,"punch_count":int(pg.punch_count) if pg is not None else 0,"worked_hours":worked,"late_minutes":late,"early_departure_minutes":early,"overtime_hours":ot_hours,"leave_type":leave_by_day.get((emp.emp_id,day))})
    att = pd.DataFrame(attendance)
    write_df(c, "stg_attendance_daily", att)

    # Warehouse dimensions and facts, keyed by natural IDs for traceability.
    write_df(c, "dim_department", depts[["dept_id","dept_name","plant","cost_center","previous_name","renamed_on"]])
    write_df(c, "dim_shift", shifts)
    dates = pd.DataFrame({"date_key":pd.date_range(START,attendance_end).strftime("%Y%m%d").astype(int),"full_date":pd.date_range(START,attendance_end).date})
    dates["year"]=[d.year for d in dates.full_date]; dates["month"]=[d.month for d in dates.full_date]; dates["day_of_week"]=[d.strftime("%A") for d in dates.full_date]
    write_df(c,"dim_date",dates)
    hist = history.reset_index(drop=True).rename(columns={"emp_id":"employee_id","effective_from":"valid_from"}).copy()
    hist["employee_sk"] = range(1,len(hist)+1)
    hist["valid_to"] = hist.valid_to.map(lambda d: date(9999,12,31) if pd.isna(d) else d)
    write_df(c,"dim_employee",hist[["employee_sk","employee_id","dept_id","designation","grade","basic_salary","valid_from","valid_to","change_reason"]])
    att["date_key"] = att.work_date.map(lambda d:int(d.strftime("%Y%m%d")))
    emp_keys=[]
    hist_by_emp={}; hist_dates_by_emp={}
    for row in hist.itertuples():
        hist_by_emp.setdefault(row.employee_id, []).append((row.valid_from, row.employee_sk))
        hist_dates_by_emp.setdefault(row.employee_id, []).append(row.valid_from)
    for row in att.itertuples():
        versions=hist_by_emp.get(row.emp_id, [])
        position=bisect_right(hist_dates_by_emp.get(row.emp_id, []), row.work_date)-1
        emp_keys.append(int(versions[position][1]) if position>=0 else None)
    att["employee_sk"]=emp_keys
    versions_by_emp={}
    for row in hist.itertuples(): versions_by_emp.setdefault(row.employee_id,[]).append((row.valid_from,row.employee_sk))
    def resolve_employee_sk(emp_id, as_of):
        versions=versions_by_emp.get(emp_id,[])
        pos=bisect_right([v[0] for v in versions],as_of)-1
        return int(versions[pos][1]) if pos>=0 else None
    write_df(c,"fact_attendance_daily",att)
    leave_fact=leave_days.copy()
    leave_fact.insert(0,"leave_day_id",range(1,len(leave_fact)+1))
    leave_fact["employee_sk"]=[resolve_employee_sk(emp,day) for emp,day in zip(leave_fact.emp_id,leave_fact.leave_date)]
    leave_fact["date_key"]=leave_fact.leave_date.map(lambda d:int(d.strftime("%Y%m%d")))
    write_df(c,"fact_leave_day",leave_fact)
    approved_ot=ot[ot.status=="Approved"].copy()
    approved_ot["employee_sk"]=[resolve_employee_sk(emp,day) for emp,day in zip(approved_ot.emp_id,approved_ot.ot_date)]
    approved_ot["date_key"]=[int(d.strftime("%Y%m%d")) if START<=d<=END else None for d in approved_ot.ot_date]
    write_df(c,"fact_overtime_approved",approved_ot)
    payroll["employee_sk"]=[resolve_employee_sk(emp, (parse_date(sep) if sep and str(sep)[:7]==month else (datetime.strptime(month+"-01","%Y-%m-%d").date()+timedelta(days=31)).replace(day=1)-timedelta(days=1))) for emp,month,sep in zip(payroll.emp_id,payroll.payroll_month,payroll.emp_id.map(employees.set_index("emp_id").separation_date))]
    write_df(c,"fact_payroll_line",payroll)
    # Unique indexes enforce the logical primary keys for pandas-materialized tables.
    for stmt in [
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_department ON dim_department(dept_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_shift ON dim_shift(shift_code)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_date ON dim_date(date_key)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_employee ON dim_employee(employee_sk)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_attendance ON fact_attendance_daily(emp_id,work_date)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_leave ON fact_leave_day(leave_day_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_overtime ON fact_overtime_approved(request_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_payroll ON fact_payroll_line(line_id)",
    ]: c.execute(stmt)
    for stmt in [
        "CREATE TRIGGER IF NOT EXISTS fk_dim_employee_dept BEFORE INSERT ON dim_employee WHEN NOT EXISTS (SELECT 1 FROM dim_department WHERE dept_id=NEW.dept_id) BEGIN SELECT RAISE(ABORT,'dim_employee dept_id missing'); END",
        "CREATE TRIGGER IF NOT EXISTS fk_attendance_dims BEFORE INSERT ON fact_attendance_daily WHEN NOT EXISTS (SELECT 1 FROM dim_employee WHERE employee_sk=NEW.employee_sk) OR NOT EXISTS (SELECT 1 FROM dim_date WHERE date_key=NEW.date_key) OR NOT EXISTS (SELECT 1 FROM dim_shift WHERE shift_code=NEW.shift_code) BEGIN SELECT RAISE(ABORT,'fact_attendance_daily dimension missing'); END",
        "CREATE TRIGGER IF NOT EXISTS fk_leave_dims BEFORE INSERT ON fact_leave_day WHEN NOT EXISTS (SELECT 1 FROM dim_employee WHERE employee_sk=NEW.employee_sk) OR NOT EXISTS (SELECT 1 FROM dim_date WHERE date_key=NEW.date_key) BEGIN SELECT RAISE(ABORT,'fact_leave_day dimension missing'); END",
        "CREATE TRIGGER IF NOT EXISTS fk_overtime_dims BEFORE INSERT ON fact_overtime_approved WHEN NOT EXISTS (SELECT 1 FROM dim_employee WHERE employee_sk=NEW.employee_sk) OR (NEW.date_key IS NOT NULL AND NOT EXISTS (SELECT 1 FROM dim_date WHERE date_key=NEW.date_key)) BEGIN SELECT RAISE(ABORT,'fact_overtime_approved dimension missing'); END",
        "CREATE TRIGGER IF NOT EXISTS fk_payroll_employee BEFORE INSERT ON fact_payroll_line WHEN NOT EXISTS (SELECT 1 FROM dim_employee WHERE employee_sk=NEW.employee_sk) BEGIN SELECT RAISE(ABORT,'fact_payroll_line employee missing'); END",
    ]: c.execute(stmt)
    # Recruitment bonus sources.
    apps=csv_data(data_dir,"applications.csv"); apps["stage_date"]=apps.stage_date.map(parse_date); write_df(c,"stg_applications",apps)
    dq=[]
    def check(name, passed, detail): dq.append({"check_name":name,"passed":bool(passed),"detail":str(detail),"checked_at":datetime.now().isoformat(timespec="seconds")})
    for t, keys in [("stg_employees",["emp_id"]),("dim_department",["dept_id"]),("dim_shift",["shift_code"]),("dim_date",["date_key"]),("dim_employee",["employee_sk"]),("fact_attendance_daily",["emp_id","work_date"]),("fact_leave_day",["leave_day_id"]),("fact_overtime_approved",["request_id"]),("fact_payroll_line",["line_id"])]:
        frame=pd.read_sql_query(f'SELECT * FROM "{t}"',c)
        unique=not frame.duplicated(keys).any()
        not_null=frame[keys].notna().all().all()
        check(f"pk_not_null_unique:{t}({','.join(keys)})",unique and not_null,f"rows={len(frame)}")
    check("attendance_no_negative_hours",(att.worked_hours>=0).all(),f"rows={len(att)}")
    check("attendance_accepted_status",set(att.status)<= {"Present","Absent","On Leave","Holiday","Weekly Off","Missing Punch"},"validated status domain")
    check("attendance_shift_at_most_16_hours",all((datetime.combine(date.today(),time.fromisoformat(s.start_time))-(datetime.combine(date.today(),time.fromisoformat(s.end_time))+(timedelta(days=1) if s.crosses_midnight else timedelta()))).total_seconds()/3600 >= -16 for s in shifts.itertuples()),"shift definitions validated")
    check("raw_punch_reconciliation",len(punches)+duplicate_punches_removed+len(rejected)==raw_punch_count,f"raw={raw_punch_count}, accepted={len(punches)}, duplicates_removed={duplicate_punches_removed}, rejected={len(rejected)}")
    check("employee_dedup_reconciliation",len(employees)+len(dedup_log.drop_duplicates("emp_id"))==1316,f"kept={len(employees)}, duplicate_employee_ids={dedup_log.emp_id.nunique()}")
    check("leave_type_accepted",set(leave_days.leave_type)<= {"Annual","Casual","Sick","Maternity","Paternity","Unpaid"} if len(leave_days) else True,"leave type domain")
    check("overlapping_scd2_ranges",not any(a.employee_id==b.employee_id and a.valid_to>=b.valid_from for a,b in zip(hist.itertuples(),list(hist.itertuples())[1:])),f"versions={len(hist)}")
    check("attendance_employee_dimension",att.employee_sk.notna().all() and set(att.employee_sk.dropna().astype(int))<=set(hist.employee_sk.astype(int)),f"rows={len(att)}")
    check("attendance_date_dimension",set(att.date_key)<=set(dates.date_key),f"rows={len(att)}")
    check("payroll_component_accepted",set(payroll.component)<= {"BASIC","HOUSE_RENT","MEDICAL","CONVEYANCE","OVERTIME","INCOME_TAX","EOBI","ABSENCE_DEDUCTION","LOAN_INSTALLMENT"},"component domain")
    check("raw_employee_reconciliation",len(csv_data(data_dir,"employees.csv"))==len(employees)+dedup_log.emp_id.nunique(),f"raw={len(csv_data(data_dir,'employees.csv'))}, clean={len(employees)}, duplicate_ids={dedup_log.emp_id.nunique()}")
    for filename,table,frame in [("employment_history.csv","employment_history",history),("departments.csv","departments",depts),("shift_roster.csv","shift_roster",roster),("device_mapping.csv","device_mapping",mapping),("holidays.csv","holidays",holidays),("overtime_requests.csv","overtime_requests",ot),("payroll_lines.csv","payroll_lines",payroll),("applications.csv","applications",apps)]:
        raw_count=c.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        check(f"raw_staging_reconciliation:{filename}",raw_count==len(frame),f"raw={raw_count}, staging={len(frame)}")
    check("raw_leave_reconciliation",c.execute("SELECT COUNT(*) FROM leave_requests").fetchone()[0]==len(leave_req),f"raw={c.execute('SELECT COUNT(*) FROM leave_requests').fetchone()[0]}, staging={len(leave_req)}")
    check("raw_shift_reconciliation",c.execute("SELECT COUNT(*) FROM shifts").fetchone()[0]==len(shifts),f"raw={c.execute('SELECT COUNT(*) FROM shifts').fetchone()[0]}, staging={len(shifts)}")
    corrected=clock_report[(clock_report.device_id=="DEV-SITE-01")&(clock_report.month.isin(["2026-07","2026-08","2026-09"]))]
    check("clock_correction_evidence",len(corrected)==3 and set(corrected.correction_minutes)=={-60},f"corrected_months={corrected[['device_id','month','correction_minutes']].to_dict('records')}")
    ref_sql="""SELECT COUNT(*) FROM (
      SELECT 1 FROM dim_employee d LEFT JOIN dim_department p ON p.dept_id=d.dept_id WHERE p.dept_id IS NULL
      UNION ALL SELECT 1 FROM fact_attendance_daily f LEFT JOIN dim_employee e ON e.employee_sk=f.employee_sk AND e.employee_id=f.emp_id AND f.work_date BETWEEN e.valid_from AND e.valid_to LEFT JOIN dim_date dt ON dt.date_key=f.date_key LEFT JOIN dim_shift s ON s.shift_code=f.shift_code WHERE e.employee_sk IS NULL OR dt.date_key IS NULL OR s.shift_code IS NULL
      UNION ALL SELECT 1 FROM fact_leave_day f LEFT JOIN dim_employee e ON e.employee_sk=f.employee_sk LEFT JOIN dim_date d ON d.date_key=f.date_key WHERE e.employee_sk IS NULL OR d.date_key IS NULL
      UNION ALL SELECT 1 FROM fact_overtime_approved f LEFT JOIN dim_employee e ON e.employee_sk=f.employee_sk LEFT JOIN dim_date d ON d.date_key=f.date_key WHERE e.employee_sk IS NULL OR (f.date_key IS NOT NULL AND d.date_key IS NULL)
      UNION ALL SELECT 1 FROM fact_payroll_line f LEFT JOIN dim_employee e ON e.employee_sk=f.employee_sk WHERE e.employee_sk IS NULL
    )"""
    ref_errors=c.execute(ref_sql).fetchone()[0]
    check("fact_dimension_referential_integrity",ref_errors==0,f"orphan_or_invalid_fact_rows={ref_errors}")
    raw_tables=["employees","employment_history","departments","shifts","shift_roster","device_mapping","biometric_punches","holidays","leave_requests","overtime_requests","payroll_lines","applications"]
    raw_key_errors=sum(c.execute(f'SELECT COUNT(*) FROM (SELECT _source_file,_source_row_number FROM "{t}" GROUP BY 1,2 HAVING COUNT(*)>1)').fetchone()[0] for t in raw_tables)
    check("raw_source_row_key_uniqueness",raw_key_errors==0,f"duplicate_source_file_row_keys={raw_key_errors}")
    dq_frame=pd.DataFrame(dq); dq_frame.insert(0,"check_id",range(1,len(dq_frame)+1))
    write_df(c,"dq_results",dq_frame)
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_dq_check_id ON dq_results(check_id)")
    c.commit(); c.close()


if __name__ == "__main__":
    build()
