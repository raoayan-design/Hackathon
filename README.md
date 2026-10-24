# Indus Weave HR pipeline

Python, pandas and SQLite pipeline for the PeopleOps HR case study. It preserves the source exports in a raw layer, builds typed staging and a dimensional warehouse, runs the ten requested SQL analyses, exports each result to CSV, and records automated checks in `dq_results`.

## Source files

Get `indus_weave_hcm_data.zip` and `data_dictionary.md` from the hackathon mentors. Extract the 12 source exports and dictionary into `data/raw/`. Source files are excluded from Git; the dataset is synthetic but supplied separately for the challenge.

## Run from a clean checkout

Python 3.10+ is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run_pipeline.py
```

One command rebuilds raw, staging and warehouse tables, runs data quality checks, executes the ten SQL files and writes their CSV results. The SQLite database is `warehouse/peopleops.sqlite`.

To load only the untouched source tables:

```powershell
python run_pipeline.py --raw-only
```

To append a separately delivered month of punches (same four columns as the original punch file), run:

```powershell
python run_pipeline.py --incremental-punches C:\path\to\biometric_punches_2026-10.csv
```

The new source is appended under its own `_source_file` value; existing source rows remain untouched. Repeating the command inserts zero duplicate rows. The warehouse attendance date range extends through the latest event in appended punch files. The case pack does not include a separate seventh-month export, so this option is ready for that delivery but has not been run with new-month records.

Raw rows are stored as text and include `_source_file`, `_loaded_at`, `_row_hash`, and `_source_row_number`. Re-running unchanged inputs inserts zero duplicate rows. Changed input rows fail fast; preserve or move the existing database before intentionally rebuilding from a changed pack.

## Outputs

- `warehouse/peopleops.sqlite`: `raw`, `stg_*`, `dim_*`, `fact_*`, and `dq_results` tables.
- `warehouse/ddl/01_star_schema.sql`: warehouse table/key design.
- `warehouse/erd.md`: Mermaid ER diagram.
- `analytics/queries/q01_*.sql` through `q10_*.sql`: requested business queries; `q11_recruitment_funnel.sql` is the bonus funnel.
- `analytics/results/q01_*.csv` through `q10_*.csv`: requested query outputs; q11 has its own CSV result.
- `presentation/cfo_findings.md`: three data-backed findings with query evidence and recommended actions.
- `presentation/slides.pdf`: CFO demo deck (six slides maximum).
- `analytics/dashboard.html`: offline dashboard with overtime, payroll exceptions, absenteeism and recruitment funnel views.
- `analytics/results/q11_recruitment_funnel.csv`: bonus stage conversion and median days from the previous stage.

## Business-rule choices

- Employee duplicates retain the latest `updated_at`; `stg_employee_dedup_log` records duplicate IDs and the reason.
- SCD2 end dates come from the day before the next history record. If a hire has no earlier history row, the first version can be extended back to hire date and logged in `stg_history_gap_repairs`; no such repairs were needed in the supplied data.
- Monthly department assignment uses month end, or the employee's separation date when they leave during the month. Department renames use the historical name until `renamed_on`.
- Missing roster weeks carry the prior week's shift. Night punches before 08:00 shift to the previous date only when the previous roster day was NIGHT.
- The `DEV-SITE-01` clock is corrected by -60 minutes for July–September 2026. The diagnosis compares median first-punch residuals to scheduled starts by device and month: +56, +57 and +56 minutes before correction, and -4, -3 and -4 minutes after.
- Punches within two minutes are deduplicated after device mapping. Unmapped punches are retained in `stg_rejected_punches` with a reason.
- Approved leave requires Manager and HR approval and no cancellation; Sundays and applicable holidays are excluded from leave days.
- The absenteeism denominator includes scheduled weekday workdays with Present, Absent or Missing Punch status, excluding approved leave, Sundays and applicable holidays.
- Payroll cost uses EARNING lines; overtime paid hours use `OVERTIME.units`.

The optional recruitment funnel is computed from `applications.csv`; the dashboard is a standalone HTML file with no external services. Cloud services, Spark and BI tools are not required.
