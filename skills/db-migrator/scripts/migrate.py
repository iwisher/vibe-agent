#!/usr/bin/env python3
"""Deterministic database migration script with automatic backup and rollback."""

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path


def run_migration(db_path: str, sql_script: str) -> dict:
    target_db = Path(db_path).resolve()
    target_sql = Path(sql_script).resolve()

    if not target_db.exists():
        return {
            "status": "error",
            "error": f"Database file '{db_path}' not found",
            "applied": False,
            "backup_created": False,
            "rows_preserved": 0,
        }

    if not target_sql.exists():
        return {
            "status": "error",
            "error": f"SQL script '{sql_script}' not found",
            "applied": False,
            "backup_created": False,
            "rows_preserved": 0,
        }

    # Count rows before migration
    conn = sqlite3.connect(target_db)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall() if not row[0].startswith("sqlite_")]
    initial_rows = 0
    for t in tables:
        cursor.execute(f"SELECT COUNT(*) FROM `{t}`")
        initial_rows += cursor.fetchone()[0]
    conn.close()

    # Create snapshot backup
    backup_file = target_db.with_suffix(".bak.sqlite")
    shutil.copy2(target_db, backup_file)

    # Execute migration script in transaction
    sql_text = target_sql.read_text(encoding="utf-8")
    conn = sqlite3.connect(target_db)
    try:
        conn.executescript(sql_text)
        conn.commit()

        # Count rows after migration
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables_after = [row[0] for row in cursor.fetchall() if not row[0].startswith("sqlite_")]
        after_rows = 0
        for t in tables_after:
            cursor.execute(f"SELECT COUNT(*) FROM `{t}`")
            after_rows += cursor.fetchone()[0]
        conn.close()

        return {
            "status": "success",
            "backup_created": True,
            "rows_preserved": after_rows,
            "initial_rows": initial_rows,
            "tables": tables_after,
            "applied": True,
        }
    except Exception as e:
        conn.close()
        # Roll back by restoring backup
        shutil.copy2(backup_file, target_db)
        return {
            "status": "rolled_back",
            "error": str(e),
            "backup_created": True,
            "rows_preserved": initial_rows,
            "applied": False,
        }


def main():
    parser = argparse.ArgumentParser(description="Safely migrate SQLite database")
    parser.add_argument("--db", required=True, help="Path to database")
    parser.add_argument("--sql", required=True, help="Path to SQL migration script")
    args = parser.parse_args()

    result = run_migration(args.db, args.sql)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("applied") else 1)


if __name__ == "__main__":
    main()
