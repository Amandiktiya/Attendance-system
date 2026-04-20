import argparse
import os
import sqlite3
from pathlib import Path

import psycopg


TABLES = {
    "users": [
        "id",
        "role",
        "profile_picture",
        "institution_type",
        "institution_name",
        "class_roll_number",
        "roll_number",
        "registration_number",
        "full_name",
        "father_name",
        "branch",
        "semester",
        "year",
        "dob",
        "mobile_number",
        "gmail",
        "email",
        "password_hash",
        "is_mobile_verified",
        "created_by",
        "created_at",
    ],
    "otp_codes": ["id", "mobile_number", "otp_code", "expires_at", "is_used", "created_at"],
    "attendance": ["id", "student_id", "attendance_date", "status", "marked_by", "remarks", "created_at"],
    "pending_changes": [
        "id",
        "target_user_id",
        "target_role",
        "requested_by",
        "approver_role",
        "status",
        "payload",
        "created_at",
        "resolved_at",
    ],
    "student_applications": [
        "id",
        "student_id",
        "application_date",
        "start_date",
        "end_date",
        "reason",
        "file_name",
        "original_file_name",
        "file_type",
        "status",
        "resolved_by",
        "resolved_at",
        "created_at",
    ],
}


def sqlite_columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def copy_table(sqlite_conn, pg_conn, table, columns):
    available = sqlite_columns(sqlite_conn, table)
    selected_columns = [column for column in columns if column in available]
    if not selected_columns:
        return 0

    rows = sqlite_conn.execute(
        f"SELECT {', '.join(selected_columns)} FROM {table} ORDER BY id"
    ).fetchall()
    if not rows:
        return 0

    placeholders = ", ".join(["%s"] * len(selected_columns))
    column_list = ", ".join(selected_columns)
    update_columns = [column for column in selected_columns if column != "id"]
    update_sql = ", ".join([f"{column} = EXCLUDED.{column}" for column in update_columns])
    sql = f"""
        INSERT INTO {table} ({column_list})
        VALUES ({placeholders})
        ON CONFLICT (id) DO UPDATE SET {update_sql}
    """
    for row in rows:
        pg_conn.execute(sql, tuple(row[column] for column in selected_columns))

    pg_conn.execute(
        "SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE((SELECT MAX(id) FROM " + table + "), 1), true)",
        (table,),
    )
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Copy local SQLite attendance.db data to Supabase PostgreSQL.")
    parser.add_argument("--sqlite", default="attendance.db", help="Path to local attendance.db")
    parser.add_argument("--database-url", default=os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL"))
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite database not found: {sqlite_path}")
    if not args.database_url:
        raise SystemExit("Set SUPABASE_DB_URL/DATABASE_URL or pass --database-url.")

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    try:
        with psycopg.connect(args.database_url) as pg_conn:
            for table, columns in TABLES.items():
                count = copy_table(sqlite_conn, pg_conn, table, columns)
                print(f"{table}: {count} row(s) copied")
            pg_conn.commit()
    finally:
        sqlite_conn.close()

    print("SQLite data migration complete.")


if __name__ == "__main__":
    main()
