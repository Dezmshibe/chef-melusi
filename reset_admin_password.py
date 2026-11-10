"""Safely reset the designated owner password for the Chef Portfolio CMS.

Run from the project folder:
    python reset_admin_password.py

The script backs up chef.db before changing the password.
"""
import os
import sqlite3
import shutil
from datetime import datetime
from getpass import getpass
from pathlib import Path

try:
    from werkzeug.security import generate_password_hash
except ImportError:
    raise SystemExit("Missing dependency. Run: pip install -r requirements.txt")

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("DATA_DIR") or BASE).resolve()
DB = DATA / "chef.db"
BACKUPS = DATA / "backups"

def main():
    if not DB.is_file():
        raise SystemExit(f"Database not found: {DB}\nPut this script beside chef.db, or set DATA_DIR.")

    with sqlite3.connect(DB) as con:
        con.row_factory = sqlite3.Row
        tables = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"admin_users", "settings"}.issubset(tables):
            raise SystemExit("This database does not look like the Chef Portfolio CMS database.")

        owner = con.execute("SELECT value FROM settings WHERE key='owner_admin_id'").fetchone()
        if not owner:
            raise SystemExit("No designated owner account found. No changes made.")

        account = con.execute(
            "SELECT id, username FROM admin_users WHERE id=?", (owner["value"],)
        ).fetchone()
        if not account:
            raise SystemExit("The designated owner account is missing. No changes made.")

    print(f"Database: {DB}")
    print(f"Owner username: {account['username']}")
    print("Choose a new password of at least 12 characters.")
    while True:
        password = getpass("New password: ")
        confirm = getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match. Try again.")
        elif len(password) < 12:
            print("Use at least 12 characters. Try again.")
        else:
            break

    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f"chef-before-password-reset-{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(DB, backup)

    with sqlite3.connect(DB) as con:
        con.execute(
            "UPDATE admin_users SET password_hash=?, must_change=0 WHERE id=?",
            (generate_password_hash(password), account["id"])
        )
        con.commit()

    print("\nPassword reset successful.")
    print(f"Login username: {account['username']}")
    print("Use the new password you just entered at /admin/login.")
    print(f"Database backup: {backup}")

if __name__ == "__main__":
    main()
