import argparse
import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash


def normalize(value):
    return (value or "").strip()


def main():
    parser = argparse.ArgumentParser(description="Reset a user's password in attendance.db.")
    parser.add_argument("identifier", help="Admin/faculty email, or student roll/registration/email/mobile")
    parser.add_argument("new_password", help="New password to set")
    parser.add_argument("--role", choices=["admin", "faculty", "student"], default="admin")
    parser.add_argument("--database", default="attendance.db", help="Path to attendance.db")
    args = parser.parse_args()

    db_path = Path(args.database)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    identifier = normalize(args.identifier)
    new_password = normalize(args.new_password)
    if not identifier or not new_password:
        raise SystemExit("Identifier and new password are required.")
    if len(new_password) < 6:
        raise SystemExit("Password must be at least 6 characters.")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        if args.role == "student":
            user = cur.execute(
                """
                SELECT id, full_name, role
                FROM users
                WHERE role = 'student'
                  AND (
                    LOWER(TRIM(roll_number)) = ?
                    OR LOWER(TRIM(registration_number)) = ?
                    OR LOWER(TRIM(email)) = ?
                    OR LOWER(TRIM(gmail)) = ?
                    OR TRIM(mobile_number) = ?
                  )
                """,
                (
                    identifier.lower(),
                    identifier.lower(),
                    identifier.lower(),
                    identifier.lower(),
                    identifier,
                ),
            ).fetchone()
        else:
            user = cur.execute(
                """
                SELECT id, full_name, role
                FROM users
                WHERE role = ? AND LOWER(TRIM(email)) = ?
                """,
                (args.role, identifier.lower()),
            ).fetchone()

        if not user:
            raise SystemExit(f"No {args.role} account found for: {identifier}")

        cur.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password), user["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    print(f"Password reset for {user['role']} '{user['full_name']}' (id {user['id']}).")


if __name__ == "__main__":
    main()
