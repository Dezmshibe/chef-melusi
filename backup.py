"""Make a backup zip (database + photos).  Usage: python backup.py [output_folder]"""
import sys
from datetime import datetime
from pathlib import Path
import app

out = Path(sys.argv[1]) if len(sys.argv) > 1 else app.BACKUP_DIR
out.mkdir(parents=True, exist_ok=True)
target = out / f"chef-backup-{datetime.utcnow():%Y%m%d-%H%M%S}.zip"
with open(target, "wb") as f:
    app.build_backup(f)
print("Backup written to", target)
