import secrets
from collections import Counter
from functools import wraps

from flask import Blueprint, jsonify, request
from werkzeug.security import check_password_hash

from .database import get_db
from .routes import (
    STUDENT_SORT_SQL,
    add_semester_group_filter,
    branch_options,
    faculty_data_clause,
    institution_name_required,
    student_identifier_query,
)
from .utils import one_year_ago_iso, today_iso


bp = Blueprint("api", __name__, url_prefix="/api")


def json_error(message, status=400):
    response = jsonify({"ok": False, "message": message})
    response.status_code = status
    return response


def row_to_user(row):
    return {
        "id": row["id"],
        "role": row["role"],
        "full_name": row["full_name"],
        "email": row["email"],
        "mobile_number": row["mobile_number"],
        "institution_type": row["institution_type"],
        "institution_name": row["institution_name"],
        "branch": row["branch"],
        "semester": row["semester"],
        "year": row["year"],
        "roll_number": row["roll_number"],
        "registration_number": row["registration_number"],
        "class_roll_number": row["class_roll_number"],
    }


def row_to_attendance_row(row):
    return {
        "student_id": row["id"],
        "class_roll_number": row["class_roll_number"] or "",
        "roll_number": row["roll_number"] or "",
        "full_name": row["full_name"],
        "branch": row["branch"] or "",
        "semester": row["semester"] or "",
        "year": row["year"] or "",
        "status": row["status"] or "Absent",
        "remarks": row["remarks"] or "",
    }


def create_token(user_id):
    token = secrets.token_hex(24)
    db = get_db()
    db.execute("INSERT INTO api_tokens (user_id, token) VALUES (?, ?)", (user_id, token))
    db.commit()
    return token


def current_api_user():
    auth_header = request.headers.get("Authorization", "").strip()
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return None
    db = get_db()
    return db.execute(
        """
        SELECT users.*
        FROM api_tokens
        JOIN users ON users.id = api_tokens.user_id
        WHERE api_tokens.token = ?
        """,
        (token,),
    ).fetchone()


def api_login_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_api_user()
            if user is None:
                return json_error("Unauthorized. Please login again.", 401)
            if roles and user["role"] not in roles:
                return json_error("You do not have access to this endpoint.", 403)
            return view(user, *args, **kwargs)

        return wrapped

    return decorator


def attendance_students_for_user(user, attendance_date, branch, year_group):
    db = get_db()
    query = """
        SELECT
            users.id,
            users.class_roll_number,
            users.roll_number,
            users.full_name,
            users.branch,
            users.semester,
            users.year,
            attendance.status,
            attendance.remarks
        FROM users
        LEFT JOIN attendance
            ON attendance.student_id = users.id AND attendance.attendance_date = ?
        WHERE users.role = 'student'
    """
    params = [attendance_date]
    effective_branch = branch.strip()
    if user["role"] == "faculty":
        effective_branch = user["branch"] or ""
        query += " AND " + faculty_data_clause("users")
        params.extend([user["institution_type"], user["institution_name"], user["branch"]])
    if effective_branch:
        query += " AND users.branch = ?"
        params.append(effective_branch)
    query, params = add_semester_group_filter(query, params, "users.semester", year_group)
    query += " ORDER BY " + STUDENT_SORT_SQL
    rows = db.execute(query, tuple(params)).fetchall()
    return rows, effective_branch


@bp.route("/health")
def health():
    return jsonify({"ok": True, "message": "Attendance API running"})


@bp.route("/login", methods=["POST"])
def login():
    payload = request.get_json(silent=True) or {}
    role = (payload.get("role") or "").strip()
    identifier = (payload.get("identifier") or "").strip()
    password = payload.get("password") or ""
    institution_type = (payload.get("institution_type") or "").strip()
    institution_name = (payload.get("institution_name") or "").strip()
    branch = (payload.get("branch") or "").strip()

    if role not in {"admin", "faculty", "student"}:
        return json_error("Please select a valid role.")
    if not identifier or not password:
        return json_error("Identifier and password are required.")

    db = get_db()
    if role == "student":
        query, params = student_identifier_query(identifier)
        user = db.execute(query, params).fetchone()
    else:
        if not institution_type:
            return json_error("Institution type is required for admin and faculty login.")
        if role != "admin" and institution_name_required(role, institution_type) and not institution_name:
            return json_error("Institution name is required for this login.")
        if role == "faculty" and not branch:
            return json_error("Branch is required for faculty login.")

        params = [role, identifier, institution_type]
        conditions = ["role = ?", "email = ?", "institution_type = ?"]
        if institution_name_required(role, institution_type):
            conditions.append("institution_name = ?")
            params.append(institution_name)
        if role == "faculty":
            conditions.append("branch = ?")
            params.append(branch)
        user = db.execute("SELECT * FROM users WHERE " + " AND ".join(conditions), tuple(params)).fetchone()

    if not user or not check_password_hash(user["password_hash"], password):
        return json_error("Invalid login details.", 401)

    token = create_token(user["id"])
    return jsonify(
        {
            "ok": True,
            "message": "Login successful.",
            "token": token,
            "user": row_to_user(user),
        }
    )


@bp.route("/logout", methods=["POST"])
@api_login_required("admin", "faculty", "student")
def logout(user):
    auth_header = request.headers.get("Authorization", "").strip()
    token = auth_header.split(" ", 1)[1].strip()
    db = get_db()
    db.execute("DELETE FROM api_tokens WHERE token = ?", (token,))
    db.commit()
    return jsonify({"ok": True, "message": "Logged out successfully.", "user_id": user["id"]})


@bp.route("/me")
@api_login_required("admin", "faculty", "student")
def me(user):
    return jsonify({"ok": True, "user": row_to_user(user)})


@bp.route("/dashboard")
@api_login_required("admin", "faculty", "student")
def dashboard(user):
    db = get_db()
    if user["role"] == "student":
        attendance_rows = db.execute(
            """
            SELECT attendance_date, status, remarks
            FROM attendance
            WHERE student_id = ?
            ORDER BY attendance_date DESC
            LIMIT 20
            """,
            (user["id"],),
        ).fetchall()
        counts = Counter(row["status"] for row in attendance_rows)
        return jsonify(
            {
                "ok": True,
                "user": row_to_user(user),
                "summary": {
                    "present": counts.get("Present", 0),
                    "absent": counts.get("Absent", 0),
                    "late": counts.get("Late", 0),
                    "total_records": len(attendance_rows),
                },
                "recent_attendance": [
                    {
                        "attendance_date": row["attendance_date"],
                        "status": row["status"],
                        "remarks": row["remarks"] or "",
                    }
                    for row in attendance_rows
                ],
            }
        )

    student_query = "SELECT COUNT(*) AS total FROM users WHERE role = 'student'"
    student_params = []
    if user["role"] == "faculty":
        student_query += " AND " + faculty_data_clause("users")
        student_params.extend([user["institution_type"], user["institution_name"], user["branch"]])
    student_count = db.execute(student_query, tuple(student_params)).fetchone()["total"]

    faculty_count = 0
    if user["role"] == "admin":
        faculty_count = db.execute("SELECT COUNT(*) AS total FROM users WHERE role = 'faculty'").fetchone()["total"]

    return jsonify(
        {
            "ok": True,
            "user": row_to_user(user),
            "summary": {
                "student_count": student_count,
                "faculty_count": faculty_count,
                "branches": branch_options(user),
            },
        }
    )


@bp.route("/attendance")
@api_login_required("admin", "faculty")
def attendance_list(user):
    attendance_date = request.args.get("attendance_date", today_iso())
    branch = request.args.get("branch", "")
    year_group = request.args.get("year_group", "")
    rows, effective_branch = attendance_students_for_user(user, attendance_date, branch, year_group)
    return jsonify(
        {
            "ok": True,
            "attendance_date": attendance_date,
            "branch": effective_branch,
            "students": [row_to_attendance_row(row) for row in rows],
        }
    )


@bp.route("/attendance", methods=["POST"])
@api_login_required("admin", "faculty")
def save_attendance(user):
    payload = request.get_json(silent=True) or {}
    attendance_date = (payload.get("attendance_date") or today_iso()).strip()
    branch = (payload.get("branch") or "").strip()
    year_group = (payload.get("year_group") or "").strip()
    records = payload.get("records") or []

    if not isinstance(records, list) or not records:
        return json_error("Attendance records are required.")

    students, effective_branch = attendance_students_for_user(user, attendance_date, branch, year_group)
    allowed_student_ids = {row["id"] for row in students}
    db = get_db()

    for item in records:
        student_id = item.get("student_id")
        status = (item.get("status") or "Absent").strip()
        remarks = (item.get("remarks") or "").strip()
        if student_id not in allowed_student_ids:
            return json_error(f"Student {student_id} is outside your allowed scope.", 403)
        if status not in {"Present", "Absent", "Late"}:
            return json_error("Status must be Present, Absent, or Late.")
        db.execute(
            """
            INSERT INTO attendance (student_id, attendance_date, status, marked_by, remarks)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(student_id, attendance_date)
            DO UPDATE SET status = excluded.status, marked_by = excluded.marked_by, remarks = excluded.remarks
            """,
            (student_id, attendance_date, status, user["id"], remarks),
        )

    db.commit()
    return jsonify(
        {
            "ok": True,
            "message": "Attendance saved successfully.",
            "attendance_date": attendance_date,
            "branch": effective_branch,
            "saved_records": len(records),
        }
    )


@bp.route("/my-attendance")
@api_login_required("student")
def my_attendance(user):
    from_date = request.args.get("from_date", one_year_ago_iso())
    to_date = request.args.get("to_date", today_iso())
    db = get_db()
    rows = db.execute(
        """
        SELECT attendance_date, status, remarks
        FROM attendance
        WHERE student_id = ? AND attendance_date BETWEEN ? AND ?
        ORDER BY attendance_date DESC
        """,
        (user["id"], from_date, to_date),
    ).fetchall()
    counts = Counter(row["status"] for row in rows)
    return jsonify(
        {
            "ok": True,
            "summary": {
                "present": counts.get("Present", 0),
                "absent": counts.get("Absent", 0),
                "late": counts.get("Late", 0),
                "total_records": len(rows),
            },
            "records": [
                {
                    "attendance_date": row["attendance_date"],
                    "status": row["status"],
                    "remarks": row["remarks"] or "",
                }
                for row in rows
            ],
        }
    )
