WITH months(month) AS (VALUES ('2026-04'),('2026-05'),('2026-06'),('2026-07'),('2026-08'),('2026-09')), employee_month AS (
 SELECT m.month,e.emp_id,e.hire_date,e.separation_date,
        CASE WHEN e.separation_date BETWEEN m.month||'-01' AND date(m.month||'-01','+1 month','-1 day') THEN e.separation_date
             ELSE date(m.month||'-01','+1 month','-1 day') END snapshot_date
 FROM months m CROSS JOIN stg_employees e
), dept_at AS (
 SELECT em.*,h.dept_id,ROW_NUMBER() OVER(PARTITION BY em.month,em.emp_id ORDER BY h.effective_from DESC) rn
 FROM employee_month em LEFT JOIN stg_employment_history h
 ON h.emp_id=em.emp_id AND h.effective_from<=em.snapshot_date
),
month_hc AS (SELECT d.month,d.dept_id,COUNT(*) hc FROM dept_at d JOIN stg_employees e USING(emp_id)
 WHERE d.rn=1 AND e.hire_date<=date(d.month||'-01','+1 month','-1 day') AND (e.separation_date IS NULL OR e.separation_date='' OR e.separation_date>date(d.month||'-01','+1 month','-1 day')) GROUP BY d.month,d.dept_id),
seps AS (SELECT substr(e.separation_date,1,7) month,h.dept_id,COUNT(DISTINCT e.emp_id) separations FROM stg_employees e JOIN stg_employment_history h ON h.emp_id=e.emp_id AND h.effective_from=(SELECT MAX(h2.effective_from) FROM stg_employment_history h2 WHERE h2.emp_id=e.emp_id AND h2.effective_from<=e.separation_date) WHERE e.separation_date BETWEEN '2026-04-01' AND '2026-09-30' GROUP BY 1,2)
SELECT m.month,d.plant,CASE WHEN d.previous_name<>'' AND date(m.month||'-01','+1 month','-1 day')<d.renamed_on THEN d.previous_name ELSE d.dept_name END dept_name,COALESCE(s.separations,0) separations,
 ROUND(100.0*COALESCE(s.separations,0)/NULLIF((COALESCE(cur.hc,0)+COALESCE(prev.hc,cur.hc,0))/2.0,0),2) attrition_rate_pct
FROM months m CROSS JOIN dim_department d LEFT JOIN seps s ON s.month=m.month AND s.dept_id=d.dept_id
LEFT JOIN month_hc cur ON cur.month=m.month AND cur.dept_id=d.dept_id
LEFT JOIN month_hc prev ON prev.month=strftime('%Y-%m',date(m.month||'-01','-1 month')) AND prev.dept_id=d.dept_id
ORDER BY m.month,d.plant,dept_name;
