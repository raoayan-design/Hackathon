"""Execute the ten case-study SQL questions and persist their CSV evidence."""
import sqlite3
from pathlib import Path
import argparse
import pandas as pd

ROOT = Path(__file__).resolve().parent
DB = ROOT.parent / "warehouse" / "peopleops.sqlite"
OUT = ROOT / "results"
SQL = ROOT / "queries"
PRESENTATION = ROOT.parent / "presentation"
MONTHS = "months(month) AS (VALUES ('2026-04'),('2026-05'),('2026-06'),('2026-07'),('2026-08'),('2026-09'))"
EMP_MONTH = """employee_month AS (
 SELECT m.month,e.emp_id,e.hire_date,e.separation_date,
        CASE WHEN e.separation_date BETWEEN m.month||'-01' AND date(m.month||'-01','+1 month','-1 day') THEN e.separation_date
             ELSE date(m.month||'-01','+1 month','-1 day') END snapshot_date
 FROM months m CROSS JOIN stg_employees e
)"""
DEPT_AT = """dept_at AS (
 SELECT em.*,h.dept_id,ROW_NUMBER() OVER(PARTITION BY em.month,em.emp_id ORDER BY h.effective_from DESC) rn
 FROM employee_month em LEFT JOIN stg_employment_history h
 ON h.emp_id=em.emp_id AND h.effective_from<=em.snapshot_date
)"""

QUERIES = {
"q01_headcount.sql": f"""WITH {MONTHS}, {EMP_MONTH}, {DEPT_AT}
SELECT d.month,dep.plant,CASE WHEN dep.previous_name<>'' AND d.snapshot_date<dep.renamed_on THEN dep.previous_name ELSE dep.dept_name END dept_name,COUNT(*) headcount
FROM dept_at d JOIN dim_department dep ON dep.dept_id=d.dept_id
JOIN stg_employees e ON e.emp_id=d.emp_id
WHERE d.rn=1 AND e.hire_date<=date(d.month||'-01','+1 month','-1 day')
AND (e.separation_date IS NULL OR e.separation_date='' OR e.separation_date>date(d.month||'-01','+1 month','-1 day'))
GROUP BY d.month,dep.plant,dep.dept_id,dept_name ORDER BY d.month,dep.plant,dept_name""",
"q02_attrition.sql": f"""WITH {MONTHS}, {EMP_MONTH}, {DEPT_AT},
month_hc AS (SELECT d.month,d.dept_id,COUNT(*) hc FROM dept_at d JOIN stg_employees e USING(emp_id)
 WHERE d.rn=1 AND e.hire_date<=date(d.month||'-01','+1 month','-1 day') AND (e.separation_date IS NULL OR e.separation_date='' OR e.separation_date>date(d.month||'-01','+1 month','-1 day')) GROUP BY d.month,d.dept_id),
seps AS (SELECT substr(e.separation_date,1,7) month,h.dept_id,COUNT(DISTINCT e.emp_id) separations FROM stg_employees e JOIN stg_employment_history h ON h.emp_id=e.emp_id AND h.effective_from=(SELECT MAX(h2.effective_from) FROM stg_employment_history h2 WHERE h2.emp_id=e.emp_id AND h2.effective_from<=e.separation_date) WHERE e.separation_date BETWEEN '2026-04-01' AND '2026-09-30' GROUP BY 1,2)
SELECT m.month,d.plant,CASE WHEN d.previous_name<>'' AND date(m.month||'-01','+1 month','-1 day')<d.renamed_on THEN d.previous_name ELSE d.dept_name END dept_name,COALESCE(s.separations,0) separations,
 ROUND(100.0*COALESCE(s.separations,0)/NULLIF((COALESCE(cur.hc,0)+COALESCE(prev.hc,cur.hc,0))/2.0,0),2) attrition_rate_pct
FROM months m CROSS JOIN dim_department d LEFT JOIN seps s ON s.month=m.month AND s.dept_id=d.dept_id
LEFT JOIN month_hc cur ON cur.month=m.month AND cur.dept_id=d.dept_id
LEFT JOIN month_hc prev ON prev.month=strftime('%Y-%m',date(m.month||'-01','-1 month')) AND prev.dept_id=d.dept_id
ORDER BY m.month,d.plant,dept_name""",
"q03_absenteeism.sql": """SELECT substr(a.work_date,1,7) month,d.plant,
 SUM(CASE WHEN a.status='Absent' THEN 1 ELSE 0 END) absent_days,
 SUM(CASE WHEN a.status IN ('Present','Absent','Missing Punch') THEN 1 ELSE 0 END) scheduled_workdays_excluding_leave,
 ROUND(100.0*SUM(CASE WHEN a.status='Absent' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN a.status IN ('Present','Absent','Missing Punch') THEN 1 ELSE 0 END),0),2) absenteeism_rate_pct
FROM fact_attendance_daily a JOIN dim_employee e ON e.employee_sk=a.employee_sk AND a.work_date BETWEEN e.valid_from AND e.valid_to
JOIN dim_department d ON d.dept_id=e.dept_id JOIN dim_date cal ON cal.date_key=a.date_key WHERE a.work_date BETWEEN '2026-04-01' AND '2026-09-30' AND cal.day_of_week<>'Sunday'
AND NOT EXISTS (SELECT 1 FROM stg_holidays h WHERE h.holiday_date=a.work_date AND (h.scope='ALL' OR h.scope=CASE d.plant WHEN 'Head Office' THEN 'HO' WHEN 'Korangi Plant' THEN 'KOR' WHEN 'SITE Plant' THEN 'SITE' WHEN 'Faisalabad Plant' THEN 'FSD' END))
GROUP BY 1,d.plant ORDER BY 1,d.plant""",
"q04_late_arrivals.sql": """SELECT a.emp_id,e.full_name,CASE WHEN d.previous_name<>'' AND a.work_date<d.renamed_on THEN d.previous_name ELSE d.dept_name END dept_name,d.plant,COUNT(*) late_days,SUM(a.late_minutes) total_late_minutes
FROM fact_attendance_daily a JOIN stg_employees e ON e.emp_id=a.emp_id
JOIN dim_employee h ON h.employee_sk=a.employee_sk JOIN dim_department d ON d.dept_id=h.dept_id
WHERE a.late_minutes>0 GROUP BY a.emp_id,e.full_name,d.dept_name,d.plant
ORDER BY late_days DESC,total_late_minutes DESC,a.emp_id LIMIT 10""",
"q05_overtime_comparison.sql": """WITH m AS (SELECT '2026-04' month UNION ALL SELECT '2026-05' UNION ALL SELECT '2026-06' UNION ALL SELECT '2026-07' UNION ALL SELECT '2026-08' UNION ALL SELECT '2026-09'),
 actual AS (SELECT substr(a.work_date,1,7) month,a.emp_id,SUM(a.overtime_hours) hours FROM fact_attendance_daily a GROUP BY 1,2),
 approved AS (SELECT substr(ot_date,1,7) month,emp_id,SUM(approved_hours) hours FROM stg_overtime_requests WHERE status='Approved' GROUP BY 1,2),
 paid AS (SELECT payroll_month month,emp_id,SUM(units) hours,SUM(amount) amount FROM stg_payroll_lines WHERE component='OVERTIME' GROUP BY 1,2),
 people AS (SELECT m.month,e.emp_id,CASE WHEN e.separation_date BETWEEN m.month||'-01' AND date(m.month||'-01','+1 month','-1 day') THEN e.separation_date ELSE date(m.month||'-01','+1 month','-1 day') END snap FROM m CROSS JOIN stg_employees e),
 md AS (SELECT p.*,h.dept_id,ROW_NUMBER() OVER(PARTITION BY p.month,p.emp_id ORDER BY h.effective_from DESC) rn FROM people p LEFT JOIN stg_employment_history h ON h.emp_id=p.emp_id AND h.effective_from<=p.snap)
SELECT x.month,d.dept_id,d.plant,CASE WHEN d.previous_name<>'' AND date(x.month||'-01','+1 month','-1 day')<d.renamed_on THEN d.previous_name ELSE d.dept_name END dept_name,ROUND(SUM(COALESCE(a.hours,0)),2) computed_punch_hours,ROUND(SUM(COALESCE(o.hours,0)),2) approved_hours,ROUND(SUM(COALESCE(p.hours,0)),2) paid_hours,ROUND(SUM(COALESCE(p.amount,0)),2) paid_overtime_cost
FROM md x JOIN dim_department d ON d.dept_id=x.dept_id LEFT JOIN actual a ON a.month=x.month AND a.emp_id=x.emp_id LEFT JOIN approved o ON o.month=x.month AND o.emp_id=x.emp_id LEFT JOIN paid p ON p.month=x.month AND p.emp_id=x.emp_id WHERE x.rn=1 GROUP BY x.month,d.plant,d.dept_id,dept_name ORDER BY x.month,d.plant,dept_name""",
"q06_overtime_overpayment.sql": """SELECT month,plant,dept_name,computed_punch_hours computed_hours,paid_hours,ROUND(100.0*(paid_hours-computed_punch_hours)/NULLIF(computed_punch_hours,0),2) excess_pct
FROM q05_ot_comparison WHERE computed_punch_hours>0 AND paid_hours>computed_punch_hours*1.15 ORDER BY excess_pct DESC""",
"q07_paid_zero_attendance.sql": """SELECT p.payroll_month,p.emp_id,e.full_name,SUM(p.amount) earnings
FROM stg_payroll_lines p JOIN stg_employees e ON e.emp_id=p.emp_id
WHERE p.component_type='EARNING' GROUP BY p.payroll_month,p.emp_id,e.full_name
HAVING NOT EXISTS (SELECT 1 FROM fact_attendance_daily a WHERE a.emp_id=p.emp_id AND substr(a.work_date,1,7)=p.payroll_month AND a.status='Present') ORDER BY p.payroll_month,p.emp_id""",
"q08_pay_after_separation.sql": """SELECT DISTINCT p.payroll_month,p.emp_id,e.full_name,e.separation_date,SUM(p.amount) earnings
FROM stg_payroll_lines p JOIN stg_employees e ON e.emp_id=p.emp_id
WHERE e.separation_date IS NOT NULL AND e.separation_date<>'' AND p.payroll_month>substr(e.separation_date,1,7) AND p.component_type='EARNING'
GROUP BY p.payroll_month,p.emp_id,e.full_name,e.separation_date ORDER BY p.payroll_month,p.emp_id""",
"q09_payroll_cost.sql": """WITH cost AS (SELECT p.payroll_month,p.emp_id,h.dept_id,SUM(p.amount) payroll_cost FROM stg_payroll_lines p JOIN stg_employees e ON e.emp_id=p.emp_id JOIN dim_employee h ON h.employee_id=p.emp_id AND (CASE WHEN e.separation_date BETWEEN p.payroll_month||'-01' AND date(p.payroll_month||'-01','+1 month','-1 day') THEN e.separation_date ELSE date(p.payroll_month||'-01','+1 month','-1 day') END) BETWEEN h.valid_from AND h.valid_to WHERE p.component_type='EARNING' GROUP BY p.payroll_month,p.emp_id,h.dept_id),
 dept_cost AS (SELECT c.payroll_month,c.dept_id,COUNT(DISTINCT c.emp_id) employees,SUM(c.payroll_cost) total_cost,AVG(c.payroll_cost) avg_cost_per_employee FROM cost c GROUP BY 1,2)
SELECT d.payroll_month,dep.plant,CASE WHEN dep.previous_name<>'' AND date(d.payroll_month||'-01','+1 month','-1 day')<dep.renamed_on THEN dep.previous_name ELSE dep.dept_name END dept_name,d.employees,ROUND(d.total_cost,2) total_payroll_cost,ROUND(d.avg_cost_per_employee,2) cost_per_employee,ROUND(d.avg_cost_per_employee-LAG(d.avg_cost_per_employee) OVER(PARTITION BY d.dept_id ORDER BY d.payroll_month),2) mom_change
FROM dept_cost d JOIN dim_department dep ON dep.dept_id=d.dept_id ORDER BY d.payroll_month,dep.plant,dept_name""",
"q10_annual_leave_overuse.sql": """SELECT e.emp_id,e.full_name,e.plant,e.annual_leave_entitlement,SUM(CASE WHEN l.leave_type='Annual' THEN 1 ELSE 0 END) approved_annual_leave_days
FROM stg_employees e LEFT JOIN stg_leave_days l ON l.emp_id=e.emp_id AND l.leave_date BETWEEN '2026-04-01' AND '2026-09-30'
GROUP BY e.emp_id,e.full_name,e.plant,e.annual_leave_entitlement
HAVING approved_annual_leave_days>e.annual_leave_entitlement ORDER BY approved_annual_leave_days DESC,e.emp_id""",
"q11_recruitment_funnel.sql": """WITH stage_dates AS (
 SELECT candidate_id,
  MIN(CASE WHEN stage='Applied' THEN stage_date END) applied,
  MIN(CASE WHEN stage='Screened' THEN stage_date END) screened,
  MIN(CASE WHEN stage='Interviewed' THEN stage_date END) interviewed,
  MIN(CASE WHEN stage='Offered' THEN stage_date END) offered,
  MIN(CASE WHEN stage='Hired' THEN stage_date END) hired
 FROM stg_applications GROUP BY candidate_id
), transitions AS (
 SELECT candidate_id,'Applied' stage,1 stage_order,applied event_date,NULL previous_date FROM stage_dates
 UNION ALL SELECT candidate_id,'Screened',2,screened,applied FROM stage_dates
 UNION ALL SELECT candidate_id,'Interviewed',3,interviewed,screened FROM stage_dates
 UNION ALL SELECT candidate_id,'Offered',4,offered,interviewed FROM stage_dates
 UNION ALL SELECT candidate_id,'Hired',5,hired,offered FROM stage_dates
), counts AS (
 SELECT stage,stage_order,COUNT(DISTINCT candidate_id) candidates
 FROM transitions WHERE event_date IS NOT NULL GROUP BY stage,stage_order
), durations AS (
 SELECT stage,stage_order,julianday(event_date)-julianday(previous_date) days,
  ROW_NUMBER() OVER(PARTITION BY stage ORDER BY julianday(event_date)-julianday(previous_date)) rn,
  COUNT(*) OVER(PARTITION BY stage) n
 FROM transitions WHERE event_date IS NOT NULL AND previous_date IS NOT NULL
), medians AS (
 SELECT stage,AVG(days) median_days_from_previous FROM durations
 WHERE rn IN ((n+1)/2,(n+2)/2) GROUP BY stage
)
SELECT c.stage,c.candidates,
 LAG(c.candidates) OVER(ORDER BY c.stage_order) prior_stage_candidates,
 ROUND(100.0*c.candidates/NULLIF(LAG(c.candidates) OVER(ORDER BY c.stage_order),0),1) conversion_pct,
 ROUND(m.median_days_from_previous,1) median_days_from_previous
FROM counts c LEFT JOIN medians m ON m.stage=c.stage ORDER BY c.stage_order""",
}

def write_dashboard(con):
    cost=pd.read_sql_query("SELECT payroll_month,SUM(amount) amount FROM stg_payroll_lines WHERE component='OVERTIME' GROUP BY payroll_month ORDER BY payroll_month",con)
    comparison=pd.read_csv(OUT/"q05_overtime_comparison.csv")
    excess=pd.read_csv(OUT/"q06_overtime_overpayment.csv")
    absenteeism=pd.read_csv(OUT/"q03_absenteeism.csv")
    postsep=pd.read_csv(OUT/"q08_pay_after_separation.csv")
    zero=pd.read_csv(OUT/"q07_paid_zero_attendance.csv")
    overtime=pd.read_csv(OUT/"q05_overtime_comparison.csv")
    monthly=pd.read_sql_query("SELECT payroll_month,SUM(units) hours,SUM(amount) amount FROM stg_payroll_lines WHERE component='OVERTIME' GROUP BY payroll_month ORDER BY payroll_month",con)
    apr=monthly.iloc[0]; aug=monthly.loc[monthly.payroll_month=='2026-08'].iloc[0]
    apr_dep=overtime[overtime.month=='2026-04'].set_index('dept_id').paid_overtime_cost
    aug_dep=overtime[overtime.month=='2026-08'].set_index('dept_id').paid_overtime_cost
    deltas=aug_dep.subtract(apr_dep,fill_value=0).sort_values(ascending=False)
    top=overtime[(overtime.month=='2026-08')&(overtime.dept_id==deltas.index[0])].iloc[0]
    delta=float(aug.amount-apr.amount); top_delta=float(deltas.iloc[0])
    rate_change=(float(aug.amount/aug.hours)/float(apr.amount/apr.hours)-1)*100
    hours_change=(float(aug.hours/apr.hours)-1)*100
    funnel=pd.read_csv(OUT/"q11_recruitment_funnel.csv")
    over_cost=float(cost.amount.iloc[-2]); apr_cost=float(cost.amount.iloc[0])
    monthly=pd.read_sql_query("SELECT payroll_month,SUM(units) hours,SUM(amount) amount FROM stg_payroll_lines WHERE component='OVERTIME' GROUP BY payroll_month ORDER BY payroll_month",con)
    apr=monthly.iloc[0]; aug=monthly.loc[monthly.payroll_month=='2026-08'].iloc[0]
    apr_dep=comparison[comparison.month=='2026-04'].set_index('dept_id').paid_overtime_cost
    aug_dep=comparison[comparison.month=='2026-08'].set_index('dept_id').paid_overtime_cost
    deltas=aug_dep.subtract(apr_dep,fill_value=0).sort_values(ascending=False)
    top=comparison[(comparison.month=='2026-08')&(comparison.dept_id==deltas.index[0])].iloc[0]
    delta=float(aug.amount-apr.amount); top_delta=float(deltas.iloc[0])
    rate_change=(float(aug.amount/aug.hours)/float(apr.amount/apr.hours)-1)*100
    hours_change=(float(aug.hours/apr.hours)-1)*100
    over_hours=len(excess); sep_amount=float(postsep.earnings.sum()); zero_amount=float(zero.earnings.sum())
    max_cost=max(float(r["amount"]) for r in cost.to_dict("records")); svg_w,svg_h=600,220
    points=[]
    for i,r in enumerate(cost.to_dict("records")):
        x=45+i*100; y=175-(float(r["amount"])/max_cost)*135
        points.append((x,y,r["payroll_month"][5:]))
    path=" ".join(("M" if i==0 else "L")+f"{x},{y:.1f}" for i,(x,y,_) in enumerate(points))
    labels="".join(f'<text x="{x}" y="205" text-anchor="middle">{m}</text><circle cx="{x}" cy="{y:.1f}" r="5"/>' for x,y,m in points)
    svg=f'<svg viewBox="0 0 {svg_w} {svg_h}" role="img" aria-label="Monthly overtime cost line chart"><path d="{path}" fill="none" stroke="#087f78" stroke-width="4"/>{labels}</svg>'
    def table(frame, columns, headers):
        head="".join(f"<th>{h}</th>" for h in headers)
        body="".join("<tr>"+"".join(f"<td>{row[col]}</td>" for col in columns)+"</tr>" for _,row in frame.iterrows())
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    html=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Indus Weave HR dashboard</title>
<style>body{{font:15px Segoe UI,Arial,sans-serif;margin:32px;background:#f5f4ef;color:#142b3a}}h1{{margin-bottom:4px}}small,.muted{{color:#536774}}.kpis{{display:grid;grid-template-columns:repeat(4,minmax(140px,1fr));gap:24px;margin:28px 0}}.kpi{{border-top:3px solid #148a83;padding-top:12px}}.kpi b{{font-size:26px;display:block;margin:8px 0}}section{{background:white;padding:20px 24px;margin:18px 0;border-radius:5px}}svg{{width:100%;max-width:700px;height:auto}}svg text{{font-size:13px;fill:#536774}}table{{border-collapse:collapse;width:100%;margin-top:12px}}th,td{{text-align:left;border-bottom:1px solid #d4dad7;padding:9px}}th{{color:#536774;font-size:12px}}@media(max-width:720px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}</style>
<h1>Indus Weave HR pipeline</h1><div class="muted">April-September 2026 | synthetic HCM exports | generated from the SQLite warehouse</div>
<div class="kpis"><div class="kpi">OT paid cost, April<b>PKR {apr_cost:,.0f}</b></div><div class="kpi">OT paid cost, August<b>PKR {over_cost:,.0f}</b>{(over_cost/apr_cost-1)*100:.1f}% above April</div><div class="kpi">Dept-months over 15%<b>{over_hours}</b>paid vs punch-computed</div><div class="kpi">Post-separation earnings<b>PKR {sep_amount:,.0f}</b>{len(postsep)} employee-months</div></div>
<section><h2>What drove the increase?</h2><p>April to August cost rose PKR {delta:,.0f} ({delta/float(apr.amount)*100:.1f}%). {top.plant} / {top.dept_name} had the largest departmental increase: PKR {top_delta:,.0f} ({top_delta/delta*100:.1f}% of net increase). Paid hours rose {hours_change:.1f}%; average payout per paid OT hour rose {rate_change:.1f}%. September cost was PKR {float(cost.amount.iloc[-1]):,.0f}. This identifies where to investigate, not proven causation.</p></section>
<section><h2>Monthly overtime cost</h2><div class="muted">Payroll OVERTIME earning amounts, PKR</div>{svg}</section>
<section><h2>Overtime reconciliation exceptions</h2>{table(excess.sort_values('excess_pct',ascending=False),['month','plant','dept_name','computed_hours','paid_hours','excess_pct'],['Month','Plant','Department','Punch hours','Paid hours','Excess %'])}</section>
<section><h2>Recruitment funnel</h2>{table(funnel,['stage','candidates','prior_stage_candidates','conversion_pct','median_days_from_previous'],['Stage','Candidates','Previous stage','Conversion %','Median days'])}</section>
<section><h2>Other payroll and attendance reviews</h2><p>{len(zero)} paid employee-months had no Present attendance day (PKR {zero_amount:,.0f} earnings across {zero.emp_id.nunique()} people). These require review; they are not automatically overpayments.</p><p>{len(postsep)} employee-months had earnings after separation month. Absenteeism and plant results are in the q03 CSV.</p></section>
<footer class="muted">SQL evidence and CSV outputs are in analytics/queries and analytics/results.</footer></html>'''
    (ROOT/"dashboard.html").write_text(html,encoding="utf-8")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--database",type=Path,default=DB)
    args=parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True); SQL.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(args.database)
    for index_sql in [
        "CREATE INDEX IF NOT EXISTS ix_att_work_date ON fact_attendance_daily(work_date)",
        "CREATE INDEX IF NOT EXISTS ix_att_emp_date ON fact_attendance_daily(emp_id,work_date)",
        "CREATE INDEX IF NOT EXISTS ix_pay_emp_month_component ON stg_payroll_lines(emp_id,payroll_month,component)",
        "CREATE INDEX IF NOT EXISTS ix_hist_emp_from ON stg_employment_history(emp_id,effective_from)",
        "CREATE INDEX IF NOT EXISTS ix_dimemp_employee_valid ON dim_employee(employee_id,valid_from,valid_to)",
    ]: con.execute(index_sql)
    comparison=QUERIES["q05_overtime_comparison.sql"]
    QUERIES["q06_overtime_overpayment.sql"]=(
        "WITH comparison AS ("+comparison+") "
        "SELECT month,plant,dept_name,computed_punch_hours computed_hours,paid_hours,"
        "ROUND(100.0*(paid_hours-computed_punch_hours)/NULLIF(computed_punch_hours,0),2) excess_pct "
        "FROM comparison WHERE computed_punch_hours>0 AND paid_hours>computed_punch_hours*1.15 ORDER BY excess_pct DESC"
    )
    for name, query in QUERIES.items():
        (SQL/name).write_text(query.strip()+";\n",encoding="utf-8")
        frame=pd.read_sql_query(query,con)
        frame.to_csv(OUT/name.replace(".sql",".csv"),index=False,encoding="utf-8-sig")
        print(f"{name}: {len(frame)} rows")
    write_dashboard(con)
    excess=pd.read_csv(OUT/"q06_overtime_overpayment.csv")
    separated=pd.read_csv(OUT/"q08_pay_after_separation.csv")
    zero=pd.read_csv(OUT/"q07_paid_zero_attendance.csv")
    overtime=pd.read_csv(OUT/"q05_overtime_comparison.csv")
    monthly=pd.read_sql_query("SELECT payroll_month,SUM(units) hours,SUM(amount) amount FROM stg_payroll_lines WHERE component='OVERTIME' GROUP BY payroll_month ORDER BY payroll_month",con)
    apr=monthly.iloc[0]; aug=monthly.loc[monthly.payroll_month=='2026-08'].iloc[0]
    apr_dep=overtime[overtime.month=='2026-04'].set_index('dept_id').paid_overtime_cost
    aug_dep=overtime[overtime.month=='2026-08'].set_index('dept_id').paid_overtime_cost
    deltas=aug_dep.subtract(apr_dep,fill_value=0).sort_values(ascending=False)
    top=overtime[(overtime.month=='2026-08')&(overtime.dept_id==deltas.index[0])].iloc[0]
    delta=float(aug.amount-apr.amount); top_delta=float(deltas.iloc[0])
    rate_change=(float(aug.amount/aug.hours)/float(apr.amount/apr.hours)-1)*100
    hours_change=(float(aug.hours/apr.hours)-1)*100
    PRESENTATION.mkdir(parents=True,exist_ok=True)
    f1=(f"Overtime cost rose from PKR {apr.amount:,.0f} in April to PKR {aug.amount:,.0f} in August (+PKR {delta:,.0f}, {delta/float(apr.amount)*100:.1f}%), then eased to PKR {monthly.iloc[-1].amount:,.0f} in September. Paid hours increased {hours_change:.1f}% and average payout per paid OT hour increased {rate_change:.1f}%. {top.plant} / {top.dept_name} had the largest departmental increase: PKR {top_delta:,.0f} ({top_delta/delta*100:.1f}% of net increase). This identifies where to investigate, not proven causation. Evidence: `analytics/queries/q05_overtime_comparison.sql` and its CSV. Action: review shift coverage, approved requests, payroll hours and rates there first.")
    if len(excess):
        worst=excess.iloc[0]
        f2=(f"In {len(excess)} department-months, paid overtime exceeded punch-computed overtime by more than 15%. "
            f"The largest gap was {worst.plant} / {worst.dept_name} in {worst.month}: {worst.paid_hours:.1f} paid hours versus "
            f"{worst.computed_hours:.1f} computed hours ({worst.excess_pct:.2f}% higher). Evidence: `analytics/queries/q06_overtime_overpayment.sql` "
            "and `analytics/results/q06_overtime_overpayment.csv`. Action: reconcile the approved request and punch detail for each flagged month before approving overtime payroll.")
    else:
        f2="No department-month exceeded the 15% paid-versus-punch threshold. Evidence: `analytics/queries/q06_overtime_overpayment.sql` and its CSV. Action: monitor the monthly comparison and investigate any new threshold breach."
    f3=(f"Payroll contains {len(separated)} employee-month records after the recorded separation month, totaling PKR {separated.earnings.sum():,.0f} in earnings. "
        "Evidence: `analytics/queries/q08_pay_after_separation.sql` and its CSV. Action: reconcile final-settlement timing and stop recurring payroll records after separation.")
    f4=(f"{len(zero)} employee-months have earnings but no Present attendance day; the affected earnings total PKR {zero.earnings.sum():,.0f} across {zero.emp_id.nunique()} employees. "
        "Evidence: `analytics/queries/q07_paid_zero_attendance.sql` and its CSV. Action: review separated employees, leave, missing punches, and payroll cutoff exceptions before treating these as overpayments.")
    (PRESENTATION/"cfo_findings.md").write_text("# CFO findings\n\n"+"\n\n".join([f"1. {f1}",f"2. {f2}",f"3. {f3}",f"4. {f4}"])+"\n",encoding="utf-8")
    con.close()

if __name__=="__main__": main()
