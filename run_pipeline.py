"""Rebuild the PeopleOps HR pipeline from the supplied source exports.

The raw loader is intentionally generic: it preserves source columns and values
as text. Transformations must be driven by the supplied data dictionary.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_FILES = (
    "employees.csv",
    "employment_history.csv",
    "departments.csv",
    "shifts.json",
    "shift_roster.csv",
    "device_mapping.csv",
    "biometric_punches.csv",
    "holidays.csv",
    "leave_requests.json",
    "overtime_requests.csv",
    "payroll_lines.csv",
    "applications.csv",
)


def table_name(filename: str) -> str:
    """Map a delivered filename to a stable, safe SQL identifier."""
    return re.sub(r"[^a-z0-9_]+", "_", Path(filename).stem.lower()).strip("_")


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def row_hash(values: list[str]) -> str:
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    # utf-8-sig accepts ordinary UTF-8 and files exported with a BOM.
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path.name}")
        headers = [str(name) for name in reader.fieldnames]
        rows = []
        for line_number, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"{path.name}:{line_number} has more fields than its header")
            rows.append(["" if row.get(name) is None else str(row[name]) for name in headers])
        return headers, rows


def read_json(path: Path) -> tuple[list[str], list[list[str]]]:
    """Keep each JSON record intact in a single text column; do not flatten raw."""
    with path.open("r", encoding="utf-8-sig") as stream:
        payload: Any = json.load(stream)
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        # A common export wrapper contains the record array under a named key.
        arrays = [value for value in payload.values() if isinstance(value, list)]
        if len(arrays) == 1:
            records = arrays[0]
        else:
            records = [payload]
    else:
        raise ValueError(f"JSON root must be an object or array: {path.name}")
    return ["_raw_json"], [[json.dumps(item, ensure_ascii=False, separators=(",", ":"))] for item in records]


def load_file(connection: sqlite3.Connection, path: Path, loaded_at: str, target_table: str | None = None) -> tuple[int, int]:
    if path.suffix.lower() == ".csv":
        headers, rows = read_csv(path)
    elif path.suffix.lower() == ".json":
        headers, rows = read_json(path)
    else:
        raise ValueError(f"Unsupported raw file format: {path.name}")

    table = target_table or table_name(path.name)
    source_columns = [quote_identifier(column) for column in headers]
    metadata_columns = ["_source_file", "_loaded_at", "_row_hash", "_source_row_number"]
    definitions = [f"{column} TEXT" for column in source_columns]
    definitions.extend(f"{quote_identifier(column)} TEXT NOT NULL" for column in metadata_columns)
    definitions.append("UNIQUE (_source_file, _source_row_number)")
    connection.execute(f"CREATE TABLE IF NOT EXISTS {quote_identifier(table)} ({', '.join(definitions)})")
    if target_table == "biometric_punches":
        connection.execute("CREATE INDEX IF NOT EXISTS ix_biometric_row_hash ON biometric_punches(_row_hash)")

    actual = [row[1] for row in connection.execute(f"PRAGMA table_info({quote_identifier(table)})")]
    expected = headers + metadata_columns
    if actual != expected:
        raise ValueError(
            f"Raw schema drift for {path.name}: database columns {actual!r} do not match {expected!r}. "
            "Move the old database aside to rebuild from changed source headers."
        )

    columns_sql = ", ".join(quote_identifier(column) for column in expected)
    placeholders = ", ".join("?" for _ in expected)
    existing_rows = {
        int(source_row_number): digest
        for digest, source_row_number in connection.execute(
            f"SELECT _row_hash, _source_row_number FROM {quote_identifier(table)} WHERE _source_file = ?",
            (path.name,),
        )
    }
    existing_hashes = set()
    if target_table == "biometric_punches":
        existing_hashes = {
            digest for (digest,) in connection.execute(
                "SELECT _row_hash FROM biometric_punches WHERE _source_file <> ?", (path.name,)
            )
        }
    batch = []
    for source_row_number, values in enumerate(rows, start=1):
        digest = row_hash(values)
        existing = existing_rows.get(source_row_number)
        if existing is not None:
            if existing != digest:
                raise ValueError(f"Source changed at row {source_row_number} in {path.name}; rebuild the raw database")
            continue
        if digest in existing_hashes:
            continue
        metadata = [path.name, loaded_at, digest, str(source_row_number)]
        batch.append(values + metadata)
    connection.executemany(
        f"INSERT INTO {quote_identifier(table)} ({columns_sql}) VALUES ({placeholders})", batch
    )
    return len(rows), len(batch)


def run_raw(data_dir: Path, database: Path) -> None:
    missing = [name for name in EXPECTED_FILES if not (data_dir / name).is_file()]
    dictionary = data_dir / "data_dictionary.md"
    if missing or not dictionary.is_file():
        details = []
        if missing:
            details.append("missing exports: " + ", ".join(missing))
        if not dictionary.is_file():
            details.append("missing data_dictionary.md")
        raise FileNotFoundError(
            f"Dataset pack is incomplete in {data_dir}: {'; '.join(details)}. "
            "No synthetic data will be substituted. Extract indus_weave_hcm_data.zip there and rerun."
        )

    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS load_log ("
            "source_file TEXT NOT NULL, loaded_at TEXT NOT NULL, source_rows INTEGER NOT NULL, "
            "inserted_rows INTEGER NOT NULL, table_rows INTEGER NOT NULL, "
            "PRIMARY KEY (source_file, loaded_at))"
        )
        loaded_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        total_source = total_inserted = 0
        for filename in EXPECTED_FILES:
            source_count, inserted_count = load_file(connection, data_dir / filename, loaded_at)
            table_count = connection.execute(
                f"SELECT COUNT(*) FROM {quote_identifier(table_name(filename))}"
            ).fetchone()[0]
            file_table_count = connection.execute(
                f"SELECT COUNT(*) FROM {quote_identifier(table_name(filename))} WHERE _source_file = ?",
                (filename,),
            ).fetchone()[0]
            if file_table_count != source_count:
                raise ValueError(
                    f"Raw reconciliation failed for {filename}: file_rows={source_count}, "
                    f"source_rows_in_raw_table={file_table_count}"
                )
            connection.execute(
                "INSERT INTO load_log VALUES (?, ?, ?, ?, ?)",
                (filename, loaded_at, source_count, inserted_count, table_count),
            )
            print(f"{filename}: file_rows={source_count}, inserted={inserted_count}, raw_table_rows={table_count}")
            total_source += source_count
            total_inserted += inserted_count
        connection.commit()
        print(f"Raw load complete: file_rows={total_source}, inserted={total_inserted}, database={database}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def load_incremental_punch_file(path: Path, database: Path) -> None:
    """Append one separately delivered punch export to raw.biometric_punches."""
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Incremental punch file not found: {path}")
    if path.name == "biometric_punches.csv":
        raise ValueError("Use a distinct filename for an incremental punch export, for example biometric_punches_2026-10.csv")
    headers, rows = read_csv(path)
    required = ["punch_id", "device_id", "device_user_id", "punch_time"]
    if headers != required:
        raise ValueError(f"Incremental punch columns must be exactly {required}; got {headers}")
    connection = sqlite3.connect(database)
    try:
        loaded_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        source_rows, inserted_rows = load_file(connection, path, loaded_at, target_table="biometric_punches")
        table_rows = connection.execute("SELECT COUNT(*) FROM biometric_punches").fetchone()[0]
        connection.execute(
            "INSERT INTO load_log VALUES (?, ?, ?, ?, ?)",
            (path.name, loaded_at, source_rows, inserted_rows, table_rows),
        )
        connection.commit()
        print(f"Incremental {path.name}: file_rows={source_rows}, inserted={inserted_rows}, raw_biometric_punches_rows={table_rows}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw"), help="Folder containing the unmodified exports and data_dictionary.md")
    parser.add_argument("--database", type=Path, default=Path("warehouse/peopleops.sqlite"), help="SQLite database output path")
    parser.add_argument("--raw-only", action="store_true", help="Load source exports into the raw layer")
    parser.add_argument("--incremental-punches", type=Path, help="Append an additional punch export without reloading unchanged source rows")
    args = parser.parse_args()
    try:
        run_raw(args.data_dir, args.database)
        if args.incremental_punches:
            load_incremental_punch_file(args.incremental_punches, args.database)
        if not args.raw_only:
            from transform_pipeline import build
            build(args.data_dir, args.database)
            analytics_script = Path(__file__).parent / "analytics" / "run_analytics.py"
            subprocess.run([sys.executable, str(analytics_script), "--database", str(args.database)], check=True)
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
