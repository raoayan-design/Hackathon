SELECT substr(a.work_date,1,7) month,d.plant,
 SUM(CASE WHEN a.status='Absent' THEN 1 ELSE 0 END) absent_days,
 SUM(CASE WHEN a.status IN ('Present','Absent','Missing Punch') THEN 1 ELSE 0 END) scheduled_workdays_excluding_leave,
 ROUND(100.0*SUM(CASE WHEN a.status='Absent' THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN a.status IN ('Present','Absent','Missing Punch') THEN 1 ELSE 0 END),0),2) absenteeism_rate_pct
FROM fact_attendance_daily a JOIN dim_employee e ON e.employee_sk=a.employee_sk AND a.work_date BETWEEN e.valid_from AND e.valid_to
JOIN dim_department d ON d.dept_id=e.dept_id JOIN dim_date cal ON cal.date_key=a.date_key WHERE a.work_date BETWEEN '2026-04-01' AND '2026-09-30' AND cal.day_of_week<>'Sunday'
AND NOT EXISTS (SELECT 1 FROM stg_holidays h WHERE h.holiday_date=a.work_date AND (h.scope='ALL' OR h.scope=CASE d.plant WHEN 'Head Office' THEN 'HO' WHEN 'Korangi Plant' THEN 'KOR' WHEN 'SITE Plant' THEN 'SITE' WHEN 'Faisalabad Plant' THEN 'FSD' END))
GROUP BY 1,d.plant ORDER BY 1,d.plant;
