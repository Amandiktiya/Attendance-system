import argparse
import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash


DEFAULT_ADMIN_EMAILS = {"admin@example.com", "admin@gmail.com"}


def normalize(value):
    return (value or "").strip()


def main():
    parser = argparse.ArgumentParser(
        description="Create or update a new admin account and remove the default admin account."
    )
    parser.add_argument("email", help="New admin login email")
    parser.add_argument("password", help="New admin login password")
    parser.add_argument("--name", default="System Admin", help="Admin full name")
    parser.add_argument("--gmail", help="Optional Gmail value. Defaults to email.")
    parser.add_argument("--mobile", help="Optional mobile number")
    parser.add_argument("--database", default="attendance.db", help="Path to attendance.db")
    parser.add_argument(
        "--delete-email",
        default="admin@example.com",
        help="Default admin email to remove after the new admin is ready.",
    )
    args = parser.parse_args()

    db_path = Path(args.database)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    new_email = normalize(args.email).lower()
    new_gmail = normalize(args.gmail) or new_email
    delete_email = normalize(args.delete_email).lower()
    if not new_email or not normalize(args.password):
        raise SystemExit("Email and password are required.")
    if new_email in DEFAULT_ADMIN_EMAILS:
        raise SystemExit("Use a non-default email for the new admin account.")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        old_admin = cur.execute(
            "SELECT id, email FROM users WHERE role = 'admin' AND LOWER(email) = ?",
            (delete_email,),
        ).fetchone()
        existing_new_admin = cur.execute(
            "SELECT id FROM users WHERE role = 'admin' AND LOWER(email) = ?",
            (new_email,),
        ).fetchone()

        password_hash = generate_password_hash(args.password)
        if existing_new_admin:
            new_admin_id = existing_new_admin["id"]
            cur.execute(
                """
                UPDATE users
                SET full_name = ?,
                    institution_type = 'other_institution',
                    institution_name = 'System Administration',
                    branch = 'Administration',
                    gmail = ?,
                    email = ?,
                    mobile_number = COALESCE(NULLIF(?, ''), mobile_number),
                    password_hash = ?,
                    is_mobile_verified = 1
                WHERE id = ?
                """,
                (normalize(args.name), new_gmail, new_email, normalize(args.mobile), password_hash, new_admin_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO users (
                    role, institution_type, institution_name, class_roll_number,
                    full_name, father_name, branch, semester, year, dob,
                    mobile_number, gmail, email, password_hash, is_mobile_verified
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "admin",
                    "other_institution",
                    "System Administration",
                    "",
                    normalize(args.name),
                    "",
                    "Administration",
                    "",
                    "",
                    "",
                    normalize(args.mobile) or None,
                    new_gmail,
                    new_email,
                    password_hash,
                    1,
                ),
            )
            new_admin_id = cur.lastrowid

        if old_admin and old_admin["id"] != new_admin_id:
            old_admin_id = old_admin["id"]
            cur.execute("UPDATE attendance SET marked_by = ? WHERE marked_by = ?", (new_admin_id, old_admin_id))
            cur.execute(
                "UPDATE student_applications SET resolved_by = ? WHERE resolved_by = ?",
                (new_admin_id, old_admin_id),
            )
            cur.execute("UPDATE pending_changes SET requested_by = ? WHERE requested_by = ?", (new_admin_id, old_admin_id))
            cur.execute("UPDATE users SET created_by = ? WHERE created_by = ?", (new_admin_id, old_admin_id))
            cur.execute("DELETE FROM users WHERE id = ?", (old_admin_id,))

        conn.commit()
    finally:
        conn.close()

    print(f"New admin ready. Email: {new_email}")
    if old_admin:
        print(f"Default admin removed: {delete_email}")
    else:
        print(f"No default admin found for: {delete_email}")


if __name__ == "__main__":
    main()
