WITH stage_dates AS (
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
FROM counts c LEFT JOIN medians m ON m.stage=c.stage ORDER BY c.stage_order;
