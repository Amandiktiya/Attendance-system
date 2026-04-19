import argparse
import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser(description="Update the admin login email and password.")
    parser.add_argument("email", help="New admin login email")
    parser.add_argument("password", help="New admin password")
    parser.add_argument("--gmail", help="Optional admin Gmail value")
    parser.add_argument("--database", default="attendance.db", help="Path to attendance.db")
    args = parser.parse_args()

    db_path = Path(args.database)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")


    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        admin = cur.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
        if not admin:
            raise SystemExit("No admin account found in the database.")

        if args.gmail:
            cur.execute(
                """
                UPDATE users
                SET email = ?, gmail = ?, password_hash = ?
                WHERE id = ?
                """,
                (args.email, args.gmail, generate_password_hash(args.password), admin[0]),
            )
        else:
            cur.execute(
                """
                UPDATE users
                SET email = ?, password_hash = ?
                WHERE id = ?
                """,
                (args.email, generate_password_hash(args.password), admin[0]),
            )
        conn.commit()
    finally:
        conn.close()

    print(f"Admin login updated. Email: {args.email}")


if __name__ == "__main__":
    main()
