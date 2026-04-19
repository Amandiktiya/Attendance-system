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
    status TEXT NOT NULL CHECK (status IN ('Present', 'Absent', 'Late', 'Leave')),
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
    start_date TEXT,
    end_date TEXT,
    reason TEXT NOT NULL,
    file_name TEXT,
    original_file_name TEXT,
    file_type TEXT,
    status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'approved', 'rejected')),
    resolved_by INTEGER,
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(student_id) REFERENCES users(id),
    FOREIGN KEY(resolved_by) REFERENCES users(id)
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

    attendance_schema = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'attendance'"
    ).fetchone()
    attendance_sql = attendance_schema["sql"] if attendance_schema else ""
    if "Leave" not in attendance_sql:
        db.execute("ALTER TABLE attendance RENAME TO attendance_old")
        db.execute(
            """
            CREATE TABLE attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                attendance_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('Present', 'Absent', 'Late', 'Leave')),
                marked_by INTEGER NOT NULL,
                remarks TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, attendance_date),
                FOREIGN KEY(student_id) REFERENCES users(id),
                FOREIGN KEY(marked_by) REFERENCES users(id)
            )
            """
        )
        db.execute(
            """
            INSERT INTO attendance (id, student_id, attendance_date, status, marked_by, remarks, created_at)
            SELECT id, student_id, attendance_date, status, marked_by, remarks, created_at
            FROM attendance_old
            """
        )
        db.execute("DROP TABLE attendance_old")
    db.execute(
        """
        UPDATE attendance
        SET status = 'Leave',
            remarks = REPLACE(remarks, 'Approved application:', 'Approved leave application:')
        WHERE remarks LIKE 'Approved application:%'
        """
    )

    application_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(student_applications)").fetchall()
    }
    application_schema = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'student_applications'"
    ).fetchone()
    schema_sql = application_schema["sql"] if application_schema else ""
    needs_application_rebuild = (
        "reviewed" in schema_sql
        or "approved" not in schema_sql
        or "start_date" not in application_columns
        or "end_date" not in application_columns
        or "resolved_by" not in application_columns
        or "resolved_at" not in application_columns
    )
    if needs_application_rebuild:
        db.execute("ALTER TABLE student_applications RENAME TO student_applications_old")
        db.execute(
            """
            CREATE TABLE student_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                application_date TEXT NOT NULL,
                start_date TEXT,
                end_date TEXT,
                reason TEXT NOT NULL,
                file_name TEXT,
                original_file_name TEXT,
                file_type TEXT,
                status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'approved', 'rejected')),
                resolved_by INTEGER,
                resolved_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(student_id) REFERENCES users(id),
                FOREIGN KEY(resolved_by) REFERENCES users(id)
            )
            """
        )
        old_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(student_applications_old)").fetchall()
        }
        start_expr = "start_date" if "start_date" in old_columns else "application_date"
        end_expr = "end_date" if "end_date" in old_columns else "application_date"
        resolved_by_expr = "resolved_by" if "resolved_by" in old_columns else "NULL"
        resolved_at_expr = "resolved_at" if "resolved_at" in old_columns else "NULL"
        db.execute(
            f"""
            INSERT INTO student_applications (
                id, student_id, application_date, start_date, end_date, reason,
                file_name, original_file_name, file_type, status, resolved_by, resolved_at, created_at
            )
            SELECT
                id,
                student_id,
                application_date,
                COALESCE(NULLIF({start_expr}, ''), application_date),
                COALESCE(NULLIF({end_expr}, ''), application_date),
                reason,
                file_name,
                original_file_name,
                file_type,
                CASE
                    WHEN status = 'reviewed' THEN 'approved'
                    WHEN status IN ('submitted', 'approved', 'rejected') THEN status
                    ELSE 'submitted'
                END,
                {resolved_by_expr},
                {resolved_at_expr},
                created_at
            FROM student_applications_old
            """
        )
        db.execute("DROP TABLE student_applications_old")


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
            "9720401718",
            "amandiktiya@gmail.com",
            "amandiktiya@gmail.com",
            generate_password_hash("Aman@8280"),
            1,
        ),
    )


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
