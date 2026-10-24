SELECT e.emp_id,e.full_name,e.plant,e.annual_leave_entitlement,SUM(CASE WHEN l.leave_type='Annual' THEN 1 ELSE 0 END) approved_annual_leave_days
FROM stg_employees e LEFT JOIN stg_leave_days l ON l.emp_id=e.emp_id AND l.leave_date BETWEEN '2026-04-01' AND '2026-09-30'
GROUP BY e.emp_id,e.full_name,e.plant,e.annual_leave_entitlement
HAVING approved_annual_leave_days>e.annual_leave_entitlement ORDER BY approved_annual_leave_days DESC,e.emp_id;
