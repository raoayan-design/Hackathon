# CFO findings

1. Overtime cost rose from PKR 2,814,309 in April to PKR 3,566,669 in August (+PKR 752,360, 26.7%), then eased to PKR 3,475,158 in September. Paid hours increased 17.9% and average payout per paid OT hour increased 7.5%. Faisalabad Plant / Stitching had the largest departmental increase: PKR 433,166 (57.6% of net increase). This identifies where to investigate, not proven causation. Evidence: `analytics/queries/q05_overtime_comparison.sql` and its CSV. Action: review shift coverage, approved requests, payroll hours and rates there first.

2. In 6 department-months, paid overtime exceeded punch-computed overtime by more than 15%. The largest gap was Faisalabad Plant / Stitching in 2026-09: 1778.5 paid hours versus 775.5 computed hours (129.34% higher). Evidence: `analytics/queries/q06_overtime_overpayment.sql` and `analytics/results/q06_overtime_overpayment.csv`. Action: reconcile the approved request and punch detail for each flagged month before approving overtime payroll.

3. Payroll contains 21 employee-month records after the recorded separation month, totaling PKR 1,948,000 in earnings. Evidence: `analytics/queries/q08_pay_after_separation.sql` and its CSV. Action: reconcile final-settlement timing and stop recurring payroll records after separation.

4. 59 employee-months have earnings but no Present attendance day; the affected earnings total PKR 4,829,312 across 13 employees. Evidence: `analytics/queries/q07_paid_zero_attendance.sql` and its CSV. Action: review separated employees, leave, missing punches, and payroll cutoff exceptions before treating these as overpayments.
