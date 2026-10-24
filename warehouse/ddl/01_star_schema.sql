-- SQLite-compatible warehouse design. The build process materializes these
-- dimensions and facts from the typed staging tables.
CREATE TABLE dim_department (
  dept_id TEXT PRIMARY KEY,
  dept_name TEXT NOT NULL,
  plant TEXT NOT NULL,
  cost_center TEXT NOT NULL,
  previous_name TEXT,
  renamed_on TEXT
);
CREATE TABLE dim_shift (
  shift_code TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  start_time TEXT NOT NULL,
  end_time TEXT NOT NULL,
  crosses_midnight INTEGER NOT NULL CHECK (crosses_midnight IN (0,1)),
  grace_minutes INTEGER NOT NULL CHECK (grace_minutes >= 0),
  applies_to TEXT
);
CREATE TABLE dim_date (
  date_key INTEGER PRIMARY KEY,
  full_date TEXT NOT NULL UNIQUE,
  year INTEGER NOT NULL,
  month INTEGER NOT NULL,
  day_of_week TEXT NOT NULL
);
CREATE TABLE dim_employee (
  employee_sk INTEGER PRIMARY KEY,
  employee_id TEXT NOT NULL,
  dept_id TEXT NOT NULL REFERENCES dim_department(dept_id),
  designation TEXT,
  grade TEXT,
  basic_salary REAL,
  valid_from TEXT NOT NULL,
  valid_to TEXT NOT NULL,
  change_reason TEXT,
  UNIQUE(employee_id, valid_from),
  CHECK (valid_from <= valid_to)
);
CREATE TABLE fact_attendance_daily (
  emp_id TEXT NOT NULL,
  work_date TEXT NOT NULL,
  employee_sk INTEGER NOT NULL REFERENCES dim_employee(employee_sk),
  date_key INTEGER NOT NULL REFERENCES dim_date(date_key),
  shift_code TEXT NOT NULL REFERENCES dim_shift(shift_code),
  status TEXT NOT NULL CHECK (status IN ('Present','Absent','On Leave','Holiday','Weekly Off','Missing Punch')),
  first_in TEXT,
  last_out TEXT,
  punch_count INTEGER NOT NULL,
  worked_hours REAL NOT NULL CHECK (worked_hours >= 0),
  late_minutes INTEGER NOT NULL,
  early_departure_minutes INTEGER NOT NULL,
  overtime_hours REAL NOT NULL CHECK (overtime_hours >= 0),
  UNIQUE(emp_id, work_date)
);
CREATE TABLE fact_leave_day (
  leave_day_id INTEGER PRIMARY KEY,
  request_id TEXT NOT NULL,
  employee_sk INTEGER NOT NULL REFERENCES dim_employee(employee_sk),
  date_key INTEGER NOT NULL REFERENCES dim_date(date_key),
  leave_type TEXT NOT NULL
);
CREATE TABLE fact_overtime_approved (
  request_id TEXT PRIMARY KEY,
  employee_sk INTEGER NOT NULL REFERENCES dim_employee(employee_sk),
  date_key INTEGER NOT NULL REFERENCES dim_date(date_key),
  requested_hours REAL NOT NULL,
  approved_hours REAL NOT NULL
);
CREATE TABLE fact_payroll_line (
  line_id TEXT PRIMARY KEY,
  employee_sk INTEGER NOT NULL REFERENCES dim_employee(employee_sk),
  payroll_month TEXT NOT NULL,
  component TEXT NOT NULL,
  component_type TEXT NOT NULL CHECK (component_type IN ('EARNING','DEDUCTION')),
  units REAL NOT NULL,
  amount REAL NOT NULL
);
