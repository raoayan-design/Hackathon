SELECT DISTINCT p.payroll_month,p.emp_id,e.full_name,e.separation_date,SUM(p.amount) earnings
FROM stg_payroll_lines p JOIN stg_employees e ON e.emp_id=p.emp_id
WHERE e.separation_date IS NOT NULL AND e.separation_date<>'' AND p.payroll_month>substr(e.separation_date,1,7) AND p.component_type='EARNING'
GROUP BY p.payroll_month,p.emp_id,e.full_name,e.separation_date ORDER BY p.payroll_month,p.emp_id;
