SELECT a.emp_id,e.full_name,CASE WHEN d.previous_name<>'' AND a.work_date<d.renamed_on THEN d.previous_name ELSE d.dept_name END dept_name,d.plant,COUNT(*) late_days,SUM(a.late_minutes) total_late_minutes
FROM fact_attendance_daily a JOIN stg_employees e ON e.emp_id=a.emp_id
JOIN dim_employee h ON h.employee_sk=a.employee_sk JOIN dim_department d ON d.dept_id=h.dept_id
WHERE a.late_minutes>0 GROUP BY a.emp_id,e.full_name,d.dept_name,d.plant
ORDER BY late_days DESC,total_late_minutes DESC,a.emp_id LIMIT 10;
