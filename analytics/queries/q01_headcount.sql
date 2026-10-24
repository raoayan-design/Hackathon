WITH months(month) AS (VALUES ('2026-04'),('2026-05'),('2026-06'),('2026-07'),('2026-08'),('2026-09')), employee_month AS (
 SELECT m.month,e.emp_id,e.hire_date,e.separation_date,
        CASE WHEN e.separation_date BETWEEN m.month||'-01' AND date(m.month||'-01','+1 month','-1 day') THEN e.separation_date
             ELSE date(m.month||'-01','+1 month','-1 day') END snapshot_date
 FROM months m CROSS JOIN stg_employees e
), dept_at AS (
 SELECT em.*,h.dept_id,ROW_NUMBER() OVER(PARTITION BY em.month,em.emp_id ORDER BY h.effective_from DESC) rn
 FROM employee_month em LEFT JOIN stg_employment_history h
 ON h.emp_id=em.emp_id AND h.effective_from<=em.snapshot_date
)
SELECT d.month,dep.plant,CASE WHEN dep.previous_name<>'' AND d.snapshot_date<dep.renamed_on THEN dep.previous_name ELSE dep.dept_name END dept_name,COUNT(*) headcount
FROM dept_at d JOIN dim_department dep ON dep.dept_id=d.dept_id
JOIN stg_employees e ON e.emp_id=d.emp_id
WHERE d.rn=1 AND e.hire_date<=date(d.month||'-01','+1 month','-1 day')
AND (e.separation_date IS NULL OR e.separation_date='' OR e.separation_date>date(d.month||'-01','+1 month','-1 day'))
GROUP BY d.month,dep.plant,dep.dept_id,dept_name ORDER BY d.month,dep.plant,dept_name;
