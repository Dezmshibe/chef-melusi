"""One-time, guarded migration of Chef Melusi's SQLite backup to Supabase PostgreSQL."""
import os
import sys
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
os.environ["SKIP_DB_SEED"] = "1"

# Importing app creates the PostgreSQL schema without adding default rows.
from app import conn  # noqa: E402

TABLES = [
    "settings", "admin_users", "services", "testimonials", "occasions",
    "quotes", "photos", "admin_login_attempts", "admin_audit_log",
    "admin_recovery_codes", "quote_submission_limits",
]
IDENTITY_TABLES = ["admin_users", "services", "testimonials", "occasions", "quotes", "photos", "admin_audit_log", "admin_recovery_codes"]
TIMESTAMP_COLUMNS = {"created_at", "used_at"}


def parse_timestamp(value):
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return value


def main():
    archive = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE / "chef-live-backup.zip"
    if not archive.is_file():
        raise SystemExit(f"Backup ZIP not found: {archive}")
    with zipfile.ZipFile(archive) as zf:
        db_candidates = [n for n in zf.namelist() if n.endswith(".db")]
        if len(db_candidates) != 1:
            raise SystemExit("Expected exactly one .db file in the backup ZIP.")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "chef.db"
            db_path.write_bytes(zf.read(db_candidates[0]))
            source = sqlite3.connect(db_path)
            source.row_factory = sqlite3.Row
            source_tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = set(TABLES) - source_tables
            if missing:
                raise SystemExit("Backup is missing expected tables: " + ", ".join(sorted(missing)))
            data = {table: [dict(row) for row in source.execute(f'SELECT * FROM "{table}"')] for table in TABLES}
            source.close()

    expected = {table: len(rows) for table, rows in data.items()}
    with conn() as target:
        # Never merge into or overwrite a non-empty database.
        current = {table: target.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in TABLES}
        nonempty = {table: count for table, count in current.items() if count}
        if nonempty:
            raise SystemExit("STOP: Supabase already contains rows. No data was changed. Existing row counts: " + repr(nonempty))
        for table in TABLES:
            rows = data[table]
            if not rows:
                continue
            columns = list(rows[0].keys())
            quoted_columns = ",".join(f'"{c}"' for c in columns)
            placeholders = ",".join(["%s"] * len(columns))
            sql = f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})'
            values = []
            for row in rows:
                values.append(tuple(parse_timestamp(row[c]) if c in TIMESTAMP_COLUMNS else row[c] for c in columns))
            target.raw.cursor().executemany(sql, values)
        for table in IDENTITY_TABLES:
            target.execute(
                "SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE((SELECT MAX(id) FROM " + table + "), 1), (SELECT COUNT(*) > 0 FROM " + table + "))",
                (table,),
            )
    print("Migration completed. Imported row counts:")
    for table, count in expected.items():
        print(f"  {table}: {count}")
    print("Existing IDs, password hashes, TOTP configuration and recovery-code hashes were retained.")

if __name__ == "__main__":
    main()
