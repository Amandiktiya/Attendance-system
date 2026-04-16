import sqlite3
from datetime import date
from pathlib import Path

from flask import current_app, g
from werkzeug.security import generate_password_hash


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK (role IN ('admin', 'faculty', 'student')),
    profile_picture TEXT,
    institution_type TEXT,
    institution_name TEXT,
    class_roll_number TEXT,
    roll_number TEXT UNIQUE,
    registration_number TEXT UNIQUE,
    full_name TEXT NOT NULL,
    father_name TEXT,
    branch TEXT,
    semester TEXT,
    year TEXT,
    dob TEXT,
    mobile_number TEXT UNIQUE,
    gmail TEXT,
    email TEXT,
    password_hash TEXT NOT NULL,
    is_mobile_verified INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS otp_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mobile_number TEXT NOT NULL,
    otp_code TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    is_used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    attendance_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Present', 'Absent', 'Late')),
    marked_by INTEGER NOT NULL,
    remarks TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(student_id, attendance_date),
    FOREIGN KEY(student_id) REFERENCES users(id),
    FOREIGN KEY(marked_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS pending_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_user_id INTEGER NOT NULL,
    target_role TEXT NOT NULL CHECK (target_role IN ('faculty', 'student')),
    requested_by INTEGER NOT NULL,
    approver_role TEXT NOT NULL CHECK (approver_role IN ('admin', 'faculty')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    FOREIGN KEY(target_user_id) REFERENCES users(id),
    FOREIGN KEY(requested_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS student_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    application_date TEXT NOT NULL,
    reason TEXT NOT NULL,
    file_name TEXT,
    original_file_name TEXT,
    file_type TEXT,
    status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'reviewed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(student_id) REFERENCES users(id)
);
"""


def get_db():
    if "db" not in g:
        db_path = Path(current_app.root_path).parent / current_app.config["DATABASE"]
        g.db = sqlite3.connect(db_path)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    migrate_db(db)
    ensure_default_admin(db)
    db.commit()


def migrate_db(db):
    user_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()
    }
    if "institution_type" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN institution_type TEXT")
    if "institution_name" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN institution_name TEXT")
    if "class_roll_number" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN class_roll_number TEXT")
    if "profile_picture" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN profile_picture TEXT")


def ensure_default_admin(db):
    admin = db.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
    if admin:
        db.execute(
            """
            UPDATE users
            SET institution_type = COALESCE(NULLIF(institution_type, ''), 'other_institution'),
                institution_name = COALESCE(NULLIF(institution_name, ''), 'System Administration')
            WHERE role = 'admin'
            """
        )
        return

    db.execute(
        """
        INSERT INTO users (
            role, institution_type, institution_name, class_roll_number, full_name, father_name, branch, semester, year, dob,
            mobile_number, gmail, email, password_hash, is_mobile_verified
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "admin",
            "other_institution",
            "System Administration",
            "",
            "System Admin",
            "",
            "Administration",
            "",
            str(date.today().year),
            "",
            "9999999999",
            "admin@gmail.com",
            "admin@example.com",
            generate_password_hash("admin123"),
            1,
        ),
    )


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
