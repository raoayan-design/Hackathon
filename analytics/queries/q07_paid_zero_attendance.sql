SELECT p.payroll_month,p.emp_id,e.full_name,SUM(p.amount) earnings
FROM stg_payroll_lines p JOIN stg_employees e ON e.emp_id=p.emp_id
WHERE p.component_type='EARNING' GROUP BY p.payroll_month,p.emp_id,e.full_name
HAVING NOT EXISTS (SELECT 1 FROM fact_attendance_daily a WHERE a.emp_id=p.emp_id AND substr(a.work_date,1,7)=p.payroll_month AND a.status='Present') ORDER BY p.payroll_month,p.emp_id;
