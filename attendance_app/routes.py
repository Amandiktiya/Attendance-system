import json
import uuid
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash

from .auth import current_user, login_required
from .database import get_db
from .utils import attendance_workbook, build_otp, is_valid_mobile, one_year_ago_iso, otp_expiry, today_iso

bp = Blueprint("main", __name__)

INSTITUTION_OPTIONS = [
    ("government_program", "Government Program"),
    ("government_college", "Government College"),
    ("government_school", "Government School"),
    ("private_school", "Private School"),
    ("other_institution", "Other Institutions"),
]
SCHOOL_COLLEGE_TYPES = {"government_college", "government_school", "private_school"}
ALLOWED_APPLICATION_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
ALLOWED_PROFILE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MAX_APPLICATION_SIZE = 5 * 1024 * 1024
YEAR_SEMESTER_FILTERS = [
    ("1", "1st Year (1st / 2nd Sem)", ("1", "2")),
    ("2", "2nd Year (3rd / 4th Sem)", ("3", "4")),
    ("3", "3rd Year (5th / 6th Sem)", ("5", "6")),
]

STUDENT_SORT_SQL = """
CASE
    WHEN COALESCE(NULLIF(users.class_roll_number, ''), '') GLOB '[0-9]*'
    THEN CAST(users.class_roll_number AS INTEGER)
    ELSE 999999999
END,
users.class_roll_number,
CASE
    WHEN COALESCE(NULLIF(users.roll_number, ''), '') GLOB '[0-9]*'
    THEN CAST(users.roll_number AS INTEGER)
    ELSE 999999999
END,
users.roll_number,
users.full_name
"""


def institution_map():
    return dict(INSTITUTION_OPTIONS)


def institution_name_required(role, institution_type):
    if role == "admin":
        return False
    if role == "faculty":
        return institution_type in SCHOOL_COLLEGE_TYPES
    return institution_type != "other_institution"


def faculty_scope_clause(alias):
    return f"{alias}.institution_type = ? AND {alias}.institution_name = ? AND {alias}.branch = ?"


def faculty_branch_clause(alias):
    return f"{alias}.branch = ?"


def faculty_data_clause(alias):
    return f"{alias}.institution_type = ? AND {alias}.institution_name = ? AND {alias}.branch = ?"


def available_institutions(role=None, institution_type=None):
    db = get_db()
    query = """
        SELECT DISTINCT institution_name
        FROM users
        WHERE COALESCE(institution_name, '') != ''
    """
    params = []
    if role:
        query += " AND role = ?"
        params.append(role)
    if institution_type:
        query += " AND institution_type = ?"
        params.append(institution_type)
    query += " ORDER BY institution_name"
    return [row["institution_name"] for row in db.execute(query, tuple(params)).fetchall()]


def branch_options(user=None):
    db = get_db()
    query = """
        SELECT DISTINCT branch
        FROM users
        WHERE role = 'student' AND COALESCE(branch, '') != ''
    """
    params = []
    if user and user["role"] == "faculty":
        query += " AND " + faculty_data_clause("users")
        params.extend([user["institution_type"], user["institution_name"], user["branch"]])
    query += " ORDER BY branch"
    rows = db.execute(query, tuple(params)).fetchall()
    return [row["branch"] for row in rows]


def faculty_department_options():
    db = get_db()
    rows = db.execute(
        """
        SELECT DISTINCT branch
        FROM users
        WHERE role = 'faculty' AND COALESCE(branch, '') != ''
        ORDER BY branch
        """
    ).fetchall()
    return [row["branch"] for row in rows]


def student_identifier_query(identifier):
    normalized = identifier.strip().lower()
    return (
        """
        SELECT * FROM users
        WHERE role = 'student'
          AND (
              LOWER(TRIM(roll_number)) = ?
              OR LOWER(TRIM(registration_number)) = ?
              OR LOWER(TRIM(email)) = ?
              OR LOWER(TRIM(gmail)) = ?
              OR TRIM(mobile_number) = ?
          )
        """,
        (normalized, normalized, normalized, normalized, identifier.strip()),
    )


def validate_profile_form(form, role, editing_user_id=None):
    db = get_db()
    errors = []
    required_fields = ["full_name", "email", "mobile_number", "institution_type"]
    if role == "student":
        required_fields.extend(
            [
                "class_roll_number",
                "father_name",
                "branch",
                "semester",
                "year",
                "dob",
                "roll_number",
                "registration_number",
                "gmail",
            ]
        )
    else:
        required_fields.append("branch")

    for field in required_fields:
        if not form.get(field, "").strip():
            errors.append("Please fill all required fields.")
            break

    institution_type = form.get("institution_type", "").strip()
    institution_name = form.get("institution_name", "").strip()
    if institution_type and institution_type not in institution_map():
        errors.append("Please choose a valid institution type.")
    elif institution_name_required(role, institution_type) and not institution_name:
        errors.append("Please enter the college or school name.")

    mobile_number = form.get("mobile_number", "").strip()
    if mobile_number and not is_valid_mobile(mobile_number):
        errors.append("Mobile number must be 10 digits.")

    password = form.get("password", "").strip()
    if password and len(password) < 6:
        errors.append("Password must be at least 6 characters.")

    class_roll_number = form.get("class_roll_number", "").strip()
    if role == "student" and class_roll_number and not class_roll_number.isdigit():
        errors.append("Class roll number must contain digits only.")

    email = form.get("email", "").strip()
    if email:
        existing = db.execute(
            "SELECT id FROM users WHERE email = ? AND id != ?",
            (email, editing_user_id or 0),
        ).fetchone()
        if existing:
            errors.append("This email is already in use.")

    if mobile_number:
        existing = db.execute(
            "SELECT id FROM users WHERE mobile_number = ? AND id != ?",
            (mobile_number, editing_user_id or 0),
        ).fetchone()
        if existing:
            errors.append("This mobile number is already in use.")

    if role == "student":
        for field_name, message in [
            ("class_roll_number", "This class roll number is already in use."),
            ("roll_number", "This roll number is already in use."),
            ("registration_number", "This registration number is already in use."),
        ]:
            value = form.get(field_name, "").strip()
            if value:
                existing = db.execute(
                    f"SELECT id FROM users WHERE {field_name} = ? AND id != ?",
                    (value, editing_user_id or 0),
                ).fetchone()
                if existing:
                    errors.append(message)

    return errors


def profile_payload(form, role):
    payload = {
        "full_name": form.get("full_name", "").strip(),
        "father_name": form.get("father_name", "").strip(),
        "branch": form.get("branch", "").strip(),
        "semester": form.get("semester", "").strip(),
        "year": form.get("year", "").strip(),
        "dob": form.get("dob", "").strip(),
        "mobile_number": form.get("mobile_number", "").strip(),
        "gmail": form.get("gmail", "").strip() or form.get("email", "").strip(),
        "email": form.get("email", "").strip(),
        "institution_type": form.get("institution_type", "").strip(),
        "institution_name": form.get("institution_name", "").strip(),
        "profile_picture": form.get("profile_picture", "").strip(),
        "password": form.get("password", "").strip(),
    }
    if role == "student":
        payload.update(
            {
                "class_roll_number": form.get("class_roll_number", "").strip(),
                "roll_number": form.get("roll_number", "").strip(),
                "registration_number": form.get("registration_number", "").strip(),
            }
        )
    return payload


def grouped_students(rows):
    groups = OrderedDict()
    for row in rows:
        key = (row["year"] or "-", row["semester"] or "-")
        if key not in groups:
            groups[key] = []
        groups[key].append(row)
    return [{"year": key[0], "semester": key[1], "students": value} for key, value in groups.items()]


def semester_filter_options():
    return [{"value": value, "label": label} for value, label, _ in YEAR_SEMESTER_FILTERS]


def semester_values_for_filter(year_filter):
    for value, _, semesters in YEAR_SEMESTER_FILTERS:
        if value == year_filter:
            return semesters
    return ()


def add_semester_group_filter(query, params, column_name, year_filter):
    selected_semesters = semester_values_for_filter(year_filter)
    if not selected_semesters:
        return query, params

    semester_conditions = []
    for semester in selected_semesters:
        semester_conditions.extend(
            [
                f"LOWER(TRIM({column_name})) = ?",
                f"LOWER(TRIM({column_name})) = ?",
                f"LOWER(TRIM({column_name})) = ?",
                f"LOWER(TRIM({column_name})) LIKE ?",
                f"LOWER(TRIM({column_name})) LIKE ?",
            ]
        )
        params.extend(
            [
                semester,
                f"{semester}st",
                f"{semester}nd",
                f"{semester}%",
                f"%sem%{semester}%",
            ]
        )
    query += " AND (" + " OR ".join(semester_conditions) + ")"
    return query, params


def application_upload_dir():
    upload_dir = Path(current_app.root_path).parent / current_app.config["APPLICATION_UPLOAD_FOLDER"]
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def profile_upload_dir():
    upload_dir = Path(current_app.root_path).parent / current_app.config["PROFILE_UPLOAD_FOLDER"]
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def validate_application_file(file_storage):
    if not file_storage or not file_storage.filename:
        return None, None
    original_name = secure_filename(file_storage.filename)
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_APPLICATION_EXTENSIONS:
        return None, "Only JPG, JPEG, PNG, and PDF files are allowed."

    file_storage.stream.seek(0, 2)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_APPLICATION_SIZE:
        return None, "Application file size must be less than 5 MB."

    stored_name = f"{uuid.uuid4().hex}{extension}"
    file_storage.save(application_upload_dir() / stored_name)
    return (stored_name, original_name, extension.lstrip(".")), None


def save_profile_picture(file_storage, required=False):
    if not file_storage or not file_storage.filename:
        if required:
            return None, "Profile picture upload is required."
        return None, None
    original_name = secure_filename(file_storage.filename)
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_PROFILE_EXTENSIONS:
        return None, "Profile picture must be in JPG, JPEG, or PNG format only."
    file_storage.stream.seek(0, 2)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_APPLICATION_SIZE:
        return None, "Profile picture size must be less than 5 MB."
    stored_name = f"{uuid.uuid4().hex}{extension}"
    file_storage.save(profile_upload_dir() / stored_name)
    return stored_name, None


def update_user_record(user_id, form, role, allow_password_update=True):
    db = get_db()
    params = {
        "full_name": form.get("full_name", "").strip(),
        "father_name": form.get("father_name", "").strip(),
        "branch": form.get("branch", "").strip(),
        "semester": form.get("semester", "").strip(),
        "year": form.get("year", "").strip(),
        "dob": form.get("dob", "").strip(),
        "mobile_number": form.get("mobile_number", "").strip(),
        "gmail": form.get("gmail", "").strip() or form.get("email", "").strip(),
        "email": form.get("email", "").strip(),
        "institution_type": form.get("institution_type", "").strip(),
        "institution_name": form.get("institution_name", "").strip(),
        "profile_picture": form.get("profile_picture", "").strip(),
        "user_id": user_id,
    }

    if role == "student":
        params["class_roll_number"] = form.get("class_roll_number", "").strip()
        params["roll_number"] = form.get("roll_number", "").strip()
        params["registration_number"] = form.get("registration_number", "").strip()
        db.execute(
            """
            UPDATE users
            SET full_name = :full_name,
                father_name = :father_name,
                profile_picture = COALESCE(NULLIF(:profile_picture, ''), profile_picture),
                institution_type = :institution_type,
                institution_name = :institution_name,
                branch = :branch,
                semester = :semester,
                year = :year,
                dob = :dob,
                class_roll_number = :class_roll_number,
                roll_number = :roll_number,
                registration_number = :registration_number,
                mobile_number = :mobile_number,
                gmail = :gmail,
                email = :email
            WHERE id = :user_id
            """,
            params,
        )
    else:
        db.execute(
            """
            UPDATE users
            SET full_name = :full_name,
                father_name = :father_name,
                profile_picture = COALESCE(NULLIF(:profile_picture, ''), profile_picture),
                institution_type = :institution_type,
                institution_name = :institution_name,
                branch = :branch,
                mobile_number = :mobile_number,
                gmail = :gmail,
                email = :email
            WHERE id = :user_id
            """,
            params,
        )

    password = form.get("password", "").strip()
    if allow_password_update and password:
        db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password), user_id),
        )

    db.commit()


def create_pending_change(target_user_id, target_role, requested_by, approver_role, payload):
    db = get_db()
    db.execute(
        """
        INSERT INTO pending_changes (target_user_id, target_role, requested_by, approver_role, payload)
        VALUES (?, ?, ?, ?, ?)
        """,
        (target_user_id, target_role, requested_by, approver_role, json.dumps(payload)),
    )
    db.commit()


def pending_requests_for_user(user):
    db = get_db()
    query = """
        SELECT
            pending_changes.*,
            target.full_name AS target_name,
            requester.full_name AS requester_name,
            target.branch AS target_branch,
            target.institution_name AS target_institution
        FROM pending_changes
        JOIN users AS target ON target.id = pending_changes.target_user_id
        JOIN users AS requester ON requester.id = pending_changes.requested_by
        WHERE pending_changes.status = 'pending'
    """
    params = []
    if user["role"] == "faculty":
        query += " AND pending_changes.approver_role = 'faculty'"
        query += " AND " + faculty_data_clause("target")
        params.extend([user["institution_type"], user["institution_name"], user["branch"]])
    elif user["role"] == "admin":
        query += " AND pending_changes.target_role IN ('student', 'faculty')"
    else:
        query += " AND 1 = 0"
    query += " ORDER BY pending_changes.created_at DESC"
    rows = db.execute(query, tuple(params)).fetchall()
    formatted = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        target = db.execute("SELECT * FROM users WHERE id = ?", (item["target_user_id"],)).fetchone()
        item["changes"] = build_change_summary(target, item["payload"])
        formatted.append(item)
    return formatted


def approval_history_for_user(user):
    db = get_db()
    query = """
        SELECT
            pending_changes.*,
            target.full_name AS target_name,
            requester.full_name AS requester_name,
            target.branch AS target_branch,
            target.institution_name AS target_institution
        FROM pending_changes
        JOIN users AS target ON target.id = pending_changes.target_user_id
        JOIN users AS requester ON requester.id = pending_changes.requested_by
        WHERE pending_changes.status IN ('approved', 'rejected')
    """
    params = []
    if user["role"] == "student":
        query += " AND pending_changes.requested_by = ?"
        params.append(user["id"])
    elif user["role"] == "faculty":
        query += " AND (pending_changes.requested_by = ? OR pending_changes.approver_role = 'faculty')"
        params.append(user["id"])
        query += " AND " + faculty_data_clause("target")
        params.extend([user["institution_type"], user["institution_name"], user["branch"]])
    elif user["role"] != "admin":
        query += " AND 1 = 0"
    query += " ORDER BY COALESCE(pending_changes.resolved_at, pending_changes.created_at) DESC"
    rows = db.execute(query, tuple(params)).fetchall()
    formatted = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        target = db.execute("SELECT * FROM users WHERE id = ?", (item["target_user_id"],)).fetchone()
        item["changes"] = build_change_summary(target, item["payload"])
        formatted.append(item)
    return formatted


def edit_request_status_for_user(user):
    db = get_db()
    query = """
        SELECT
            pending_changes.*,
            target.full_name AS target_name,
            requester.full_name AS requester_name,
            target.branch AS target_branch,
            target.institution_name AS target_institution
        FROM pending_changes
        JOIN users AS target ON target.id = pending_changes.target_user_id
        JOIN users AS requester ON requester.id = pending_changes.requested_by
        WHERE pending_changes.requested_by = ?
        ORDER BY pending_changes.created_at DESC
    """
    rows = db.execute(query, (user["id"],)).fetchall()
    formatted = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        target = db.execute("SELECT * FROM users WHERE id = ?", (item["target_user_id"],)).fetchone()
        item["changes"] = build_change_summary(target, item["payload"])
        formatted.append(item)
    return formatted


def build_change_summary(target, payload):
    labels = {
        "profile_picture": "Profile Picture",
        "full_name": "Name",
        "father_name": "Father Name",
        "institution_type": "Institution Type",
        "institution_name": "Institution",
        "branch": "Branch / Department",
        "semester": "Semester",
        "year": "Year",
        "dob": "Date of Birth",
        "class_roll_number": "Class Roll Number",
        "roll_number": "Roll Number",
        "registration_number": "Registration Number",
        "mobile_number": "Mobile Number",
        "gmail": "Gmail",
        "email": "Email",
        "password": "Password",
    }
    changes = []
    if not target:
        return changes
    for field, label in labels.items():
        requested = payload.get(field, "")
        if field == "password":
            if requested:
                changes.append({"field": label, "current": "Hidden", "requested": "New password requested"})
            continue
        if field == "profile_picture":
            if requested:
                changes.append({"field": label, "current": "Current photo", "requested": "New photo uploaded"})
            continue
        current = target[field] if field in target.keys() else ""
        current = "" if current is None else str(current)
        requested = "" if requested is None else str(requested)
        if requested and requested != current:
            changes.append({"field": label, "current": current or "-", "requested": requested})
    return changes


def approver_can_process(user, change):
    if user["role"] == "admin":
        return change["target_role"] in {"student", "faculty"}
    if user["role"] == "faculty":
        if change["approver_role"] != "faculty":
            return False
        target = get_db().execute(
            "SELECT * FROM users WHERE id = ?",
            (change["target_user_id"],),
        ).fetchone()
        return bool(
            target
            and target["institution_type"] == user["institution_type"]
            and target["institution_name"] == user["institution_name"]
            and target["branch"] == user["branch"]
        )
    return False


@bp.context_processor
def inject_globals():
    user = current_user()
    pending_count = len(pending_requests_for_user(user)) if user and user["role"] in {"admin", "faculty"} else 0
    return {
        "current_user": user,
        "today": today_iso(),
        "min_date": one_year_ago_iso(),
        "institution_options": INSTITUTION_OPTIONS,
        "school_college_types": list(SCHOOL_COLLEGE_TYPES),
        "year_semester_filters": semester_filter_options(),
        "pending_count": pending_count,
    }


@bp.route("/")
def home():
    return render_template("home.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    db = get_db()
    if request.method == "POST":
        role = request.form["role"]
        password = request.form["password"]
        identifier = request.form["identifier"].strip()
        institution_type = request.form.get("institution_type", "").strip()
        institution_name = request.form.get("institution_name", "").strip()
        branch = request.form.get("branch", "").strip()

        if role in {"admin", "faculty"} and not institution_type:
            flash("Please choose how you want to use this app.", "error")
            return render_template("login.html", faculty_institutions=available_institutions("faculty"), student_institutions=available_institutions("student"))

        if role == "student":
            query, params = student_identifier_query(identifier)
            user = db.execute(query, params).fetchone()
        else:
            if role != "admin" and institution_name_required(role, institution_type) and not institution_name:
                flash("Please select a college or school.", "error")
                return render_template("login.html", faculty_institutions=available_institutions("faculty", institution_type), student_institutions=available_institutions("student", institution_type))

            if role == "faculty" and not branch:
                flash("Please select a branch or department for faculty login.", "error")
                return render_template("login.html", faculty_institutions=available_institutions("faculty", institution_type), student_institutions=available_institutions("student", institution_type))

            params = [role, identifier, institution_type]
            conditions = ["role = ?", "email = ?", "institution_type = ?"]
            if institution_name_required(role, institution_type):
                conditions.append("institution_name = ?")
                params.append(institution_name)
            if role == "faculty":
                conditions.append("branch = ?")
                params.append(branch)
            user = db.execute("SELECT * FROM users WHERE " + " AND ".join(conditions), tuple(params)).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            flash("Login successful.", "success")
            return redirect(url_for("main.dashboard"))

        if role == "student":
            flash("Student login failed. Use the correct password with your roll number, registration number, email, Gmail address, or mobile number.", "error")
        else:
            flash("Invalid credentials or institution details.", "error")

    return render_template("login.html", faculty_institutions=available_institutions("faculty"), student_institutions=available_institutions("student"))


@bp.route("/profiles/<path:filename>")
@login_required("admin", "faculty", "student")
def profile_picture(filename):
    return send_from_directory(profile_upload_dir(), filename)


@bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("main.home"))


@bp.route("/student/register", methods=["GET", "POST"])
def student_register():
    db = get_db()
    otp_value = None
    registration_mobile = ""

    if request.method == "POST":
        action = request.form.get("action", "register")
        mobile_number = request.form.get("mobile_number", "").strip()
        registration_mobile = mobile_number

        if action == "send_otp":
            if not is_valid_mobile(mobile_number):
                flash("Mobile number must be 10 digits.", "error")
            else:
                existing = db.execute("SELECT id FROM users WHERE mobile_number = ?", (mobile_number,)).fetchone()
                if existing:
                    flash("This mobile number is already linked to an account.", "error")
                else:
                    otp_value = build_otp()
                    db.execute("INSERT INTO otp_codes (mobile_number, otp_code, expires_at) VALUES (?, ?, ?)", (mobile_number, otp_value, otp_expiry(5)))
                    db.commit()
                    session["pending_student_mobile"] = mobile_number
                    session["pending_student_otp"] = otp_value
            flash("OTP generated. In demo mode, the OTP is displayed on the screen.", "success")
        else:
            form = request.form
            registration_mobile = form.get("mobile_number", "").strip()
            required_fields = ["full_name", "father_name", "institution_type", "branch", "semester", "year", "dob", "class_roll_number", "roll_number", "registration_number", "gmail", "email", "mobile_number", "password", "otp_code"]
            if any(not form.get(field, "").strip() for field in required_fields):
                flash("All fields are required. The account cannot be created with an incomplete form.", "error")
                return render_template("student_register.html", otp_value=session.get("pending_student_otp"), registration_mobile=registration_mobile)

            validation_errors = validate_profile_form(form, "student")
            if validation_errors:
                flash(validation_errors[0], "error")
                return render_template("student_register.html", otp_value=session.get("pending_student_otp"), registration_mobile=registration_mobile)
            profile_picture, picture_error = save_profile_picture(request.files.get("profile_picture"), required=True)
            if picture_error:
                flash(picture_error, "error")
                return render_template("student_register.html", otp_value=session.get("pending_student_otp"), registration_mobile=registration_mobile)

            otp_row = db.execute(
                """
                SELECT * FROM otp_codes
                WHERE mobile_number = ? AND otp_code = ? AND is_used = 0
                ORDER BY id DESC LIMIT 1
                """,
                (form["mobile_number"].strip(), form["otp_code"].strip()),
            ).fetchone()
            if not otp_row:
                if (
                    session.get("pending_student_mobile") == form["mobile_number"].strip()
                    and session.get("pending_student_otp") == form["otp_code"].strip()
                ):
                    otp_row = {
                        "id": None,
                        "expires_at": otp_expiry(5),
                    }
                else:
                    flash("Invalid OTP.", "error")
                    return render_template("student_register.html", otp_value=session.get("pending_student_otp"), registration_mobile=registration_mobile)

            if datetime.strptime(otp_row["expires_at"], "%Y-%m-%d %H:%M:%S") < datetime.now():
                flash("OTP expired. Please request a new one.", "error")
                return render_template("student_register.html", otp_value=session.get("pending_student_otp"), registration_mobile=registration_mobile)

            db.execute(
                """
                INSERT INTO users (
                    role, profile_picture, institution_type, institution_name, class_roll_number, roll_number, registration_number,
                    full_name, father_name, branch, semester, year, dob, mobile_number, gmail, email,
                    password_hash, is_mobile_verified
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "student",
                    profile_picture,
                    form["institution_type"].strip(),
                    form.get("institution_name", "").strip(),
                    form["class_roll_number"].strip(),
                    form["roll_number"].strip(),
                    form["registration_number"].strip(),
                    form["full_name"].strip(),
                    form["father_name"].strip(),
                    form["branch"].strip(),
                    form["semester"].strip(),
                    form["year"].strip(),
                    form["dob"].strip(),
                    form["mobile_number"].strip(),
                    form["gmail"].strip(),
                    form["email"].strip(),
                    generate_password_hash(form["password"]),
                    1,
                ),
            )
            if otp_row["id"] is not None:
                db.execute("UPDATE otp_codes SET is_used = 1 WHERE id = ?", (otp_row["id"],))
            db.commit()
            session.pop("pending_student_mobile", None)
            session.pop("pending_student_otp", None)
            flash("Student account created. Please login with roll number and password.", "success")
            return redirect(url_for("main.login"))

    return render_template("student_register.html", otp_value=otp_value or session.get("pending_student_otp"), registration_mobile=registration_mobile or session.get("pending_student_mobile", ""))


@bp.route("/dashboard")
@login_required("admin", "faculty", "student")
def dashboard():
    user = current_user()
    db = get_db()
    selected_branch = request.args.get("branch", "").strip()
    selected_year_group = request.args.get("year_group", "").strip()
    selected_faculty_department = request.args.get("faculty_department", "").strip()
    selected_recent_branch = request.args.get("recent_branch", "").strip()
    available_branches = branch_options(user)
    if user["role"] == "faculty":
        selected_branch = user["branch"]

    if user["role"] == "student":
        attendance = db.execute(
            """
            SELECT attendance.attendance_date, attendance.status, attendance.remarks, users.full_name AS marked_by_name
            FROM attendance
            JOIN users ON users.id = attendance.marked_by
            WHERE attendance.student_id = ?
            ORDER BY attendance.attendance_date DESC
            """,
            (user["id"],),
        ).fetchall()
        return render_template("student_dashboard.html", attendance=attendance, edit_requests=edit_request_status_for_user(user), approval_history=approval_history_for_user(user))

    student_query = """
        SELECT id, profile_picture, institution_name, class_roll_number, roll_number, full_name, branch, semester, year
        FROM users
        WHERE role = 'student'
    """
    params = []
    if user["role"] == "faculty":
        student_query += " AND " + faculty_data_clause("users")
        params.extend([user["institution_type"], user["institution_name"], user["branch"]])
    if selected_branch:
        student_query += " AND branch = ?"
        params.append(selected_branch)
    student_query, params = add_semester_group_filter(student_query, params, "users.semester", selected_year_group)
    student_query += " ORDER BY " + STUDENT_SORT_SQL
    students = db.execute(student_query, tuple(params)).fetchall()

    faculty_query = """
        SELECT id, profile_picture, full_name, branch, institution_name, email, mobile_number
        FROM users
        WHERE role = 'faculty'
    """
    faculty_params = []
    if user["role"] == "faculty":
        faculty_query += " AND " + faculty_scope_clause("users")
        faculty_params.extend([user["institution_type"], user["institution_name"], user["branch"]])
    elif selected_faculty_department:
        faculty_query += " AND branch = ?"
        faculty_params.append(selected_faculty_department)
    else:
        faculty_query += " AND 1 = 0"
    faculty_query += " ORDER BY full_name"
    faculties = db.execute(faculty_query, tuple(faculty_params)).fetchall()

    recent_attendance = []
    if user["role"] == "faculty":
        selected_recent_branch = user["branch"]
    if selected_recent_branch:
        recent_query = """
            SELECT attendance.attendance_date, attendance.status, users.class_roll_number, users.roll_number, users.full_name
            FROM attendance
            JOIN users ON users.id = attendance.student_id
            WHERE users.branch = ?
        """
        recent_params = [selected_recent_branch]
        if user["role"] == "faculty":
            recent_query += " AND " + faculty_data_clause("users")
            recent_params.extend([user["institution_type"], user["institution_name"], user["branch"]])
        recent_query, recent_params = add_semester_group_filter(recent_query, recent_params, "users.semester", selected_year_group)
        recent_query += " ORDER BY attendance.created_at DESC LIMIT 10"
        recent_attendance = db.execute(recent_query, tuple(recent_params)).fetchall()

    return render_template("staff_dashboard.html", students=students, grouped_students=grouped_students(students), faculties=faculties, recent_attendance=recent_attendance, branches=available_branches, selected_branch=selected_branch, selected_year_group=selected_year_group, edit_requests=edit_request_status_for_user(user), faculty_departments=faculty_department_options(), selected_faculty_department=selected_faculty_department, selected_recent_branch=selected_recent_branch)


@bp.route("/students/add", methods=["GET", "POST"])
@login_required("admin", "faculty")
def add_student():
    db = get_db()
    if request.method == "POST":
        form = request.form
        user = current_user()
        form_data = form.to_dict(flat=True)
        if user["role"] == "faculty":
            form_data["institution_type"] = user["institution_type"]
            form_data["institution_name"] = user["institution_name"]
            form_data["branch"] = user["branch"]
        errors = validate_profile_form(form_data, "student")
        if not form.get("password", "").strip():
            errors.append("Password is required.")
        if errors:
            flash(errors[0], "error")
            return render_template("add_student.html")
        profile_picture, picture_error = save_profile_picture(request.files.get("profile_picture"), required=True)
        if picture_error:
            flash(picture_error, "error")
            return render_template("add_student.html")
        institution_type = form_data["institution_type"].strip()
        institution_name = form_data.get("institution_name", "").strip()
        branch = form_data["branch"].strip()
        db.execute(
            """
            INSERT INTO users (
                role, profile_picture, institution_type, institution_name, class_roll_number, roll_number, registration_number,
                full_name, father_name, branch, semester, year, dob, mobile_number, gmail, email,
                password_hash, is_mobile_verified, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "student", profile_picture, institution_type, institution_name, form["class_roll_number"].strip(), form["roll_number"].strip(),
                form["registration_number"].strip(), form["full_name"].strip(), form["father_name"].strip(), branch,
                form["semester"].strip(), form["year"].strip(), form["dob"].strip(), form["mobile_number"].strip(),
                form["gmail"].strip(), form["email"].strip(), generate_password_hash(form["password"]), 1, user["id"],
            ),
        )
        db.commit()
        flash("Student added successfully.", "success")
        return redirect(url_for("main.dashboard"))
    return render_template("add_student.html")


@bp.route("/faculty/add", methods=["GET", "POST"])
@login_required("admin")
def add_faculty():
    db = get_db()
    if request.method == "POST":
        form = request.form
        errors = validate_profile_form(form, "faculty")
        if not form.get("password", "").strip():
            errors.append("Password is required.")
        if errors:
            flash(errors[0], "error")
            return render_template("add_faculty.html")
        profile_picture, picture_error = save_profile_picture(request.files.get("profile_picture"), required=True)
        if picture_error:
            flash(picture_error, "error")
            return render_template("add_faculty.html")
        user = current_user()
        db.execute(
            """
            INSERT INTO users (
                role, profile_picture, institution_type, institution_name, full_name, father_name, branch, semester, year, dob,
                mobile_number, gmail, email, password_hash, is_mobile_verified, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "faculty", profile_picture, form["institution_type"].strip(), form.get("institution_name", "").strip(), form["full_name"].strip(),
                form.get("father_name", "").strip(), form["branch"].strip(), "", "", "", form["mobile_number"].strip(),
                form["email"].strip(), form["email"].strip(), generate_password_hash(form["password"]), 1, user["id"],
            ),
        )
        db.commit()
        flash("Faculty account created successfully.", "success")
        return redirect(url_for("main.dashboard"))
    return render_template("add_faculty.html")


@bp.route("/student/profile/edit", methods=["GET", "POST"])
@login_required("student")
def student_profile_edit():
    user = current_user()
    student = get_db().execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    if request.method == "POST":
        form_data = request.form.to_dict(flat=True)
        profile_picture, picture_error = save_profile_picture(request.files.get("profile_picture"), required=not bool(student["profile_picture"]))
        if picture_error:
            flash(picture_error, "error")
            return render_template("student_profile_edit.html", student=student)
        if profile_picture:
            form_data["profile_picture"] = profile_picture
        errors = validate_profile_form(form_data, "student", editing_user_id=user["id"])
        if errors:
            flash(errors[0], "error")
            return render_template("student_profile_edit.html", student=student)
        create_pending_change(user["id"], "student", user["id"], "faculty", profile_payload(form_data, "student"))
        flash("The profile update request has been sent for faculty approval.", "success")
        return redirect(url_for("main.dashboard"))
    return render_template("student_profile_edit.html", student=student)


@bp.route("/students/<int:user_id>/edit", methods=["GET", "POST"])
@login_required("admin", "faculty")
def edit_student(user_id):
    db = get_db()
    student = db.execute("SELECT * FROM users WHERE id = ? AND role = 'student'", (user_id,)).fetchone()
    if not student:
        flash("Student not found.", "error")
        return redirect(url_for("main.dashboard"))
    viewer = current_user()
    if viewer["role"] == "faculty" and (
        student["institution_type"] != viewer["institution_type"]
        or student["institution_name"] != viewer["institution_name"]
        or student["branch"] != viewer["branch"]
    ):
        flash("Faculty can edit students only within their own college or school.", "error")
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        form_data = request.form.to_dict(flat=True)
        profile_picture, picture_error = save_profile_picture(request.files.get("profile_picture"), required=not bool(student["profile_picture"]))
        if picture_error:
            flash(picture_error, "error")
            return render_template("edit_student.html", student=student)
        if profile_picture:
            form_data["profile_picture"] = profile_picture
        if viewer["role"] == "faculty":
            form_data["institution_type"] = viewer["institution_type"]
            form_data["institution_name"] = viewer["institution_name"]
            form_data["branch"] = viewer["branch"]
        errors = validate_profile_form(form_data, "student", editing_user_id=user_id)
        if errors:
            flash(errors[0], "error")
            return render_template("edit_student.html", student=student)
        update_user_record(user_id, form_data, "student")
        flash("Student details updated successfully.", "success")
        return redirect(url_for("main.dashboard"))
    return render_template("edit_student.html", student=student)


@bp.route("/students/<int:user_id>/delete", methods=["POST"])
@login_required("admin", "faculty")
def delete_student(user_id):
    db = get_db()
    student = db.execute("SELECT * FROM users WHERE id = ? AND role = 'student'", (user_id,)).fetchone()
    if not student:
        flash("Student not found.", "error")
        return redirect(url_for("main.dashboard"))
    user = current_user()
    if user["role"] == "faculty" and (
        student["institution_type"] != user["institution_type"]
        or student["institution_name"] != user["institution_name"]
        or student["branch"] != user["branch"]
    ):
        flash("You can delete students only from your own branch.", "error")
        return redirect(url_for("main.dashboard"))
    db.execute("DELETE FROM attendance WHERE student_id = ?", (user_id,))
    db.execute("DELETE FROM pending_changes WHERE target_user_id = ?", (user_id,))
    db.execute("DELETE FROM student_applications WHERE student_id = ?", (user_id,))
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    flash("Student deleted successfully.", "success")
    return redirect(url_for("main.dashboard"))


@bp.route("/applications/new", methods=["GET", "POST"])
@login_required("student")
def submit_application():
    user = current_user()
    if request.method == "POST":
        application_date = request.form.get("application_date", "").strip()
        reason = request.form.get("reason", "").strip()
        if not application_date or not reason:
            flash("Application date and reason are required.", "error")
            return render_template("submit_application.html")

        uploaded_file = request.files.get("application_file")
        file_data, error = validate_application_file(uploaded_file)
        if error:
            flash(error, "error")
            return render_template("submit_application.html")

        stored_name = original_name = file_type = None
        if file_data:
            stored_name, original_name, file_type = file_data

        db = get_db()
        db.execute(
            """
            INSERT INTO student_applications (
                student_id, application_date, reason, file_name, original_file_name, file_type
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user["id"], application_date, reason, stored_name, original_name, file_type),
        )
        db.commit()
        flash("Application submitted successfully.", "success")
        return redirect(url_for("main.applications"))

    return render_template("submit_application.html")


@bp.route("/applications")
@login_required("admin", "faculty", "student")
def applications():
    user = current_user()
    db = get_db()
    query = """
        SELECT
            student_applications.*,
            users.full_name,
            users.roll_number,
            users.branch,
            users.semester,
            users.year,
            users.institution_name
        FROM student_applications
        JOIN users ON users.id = student_applications.student_id
        WHERE 1=1
    """
    params = []
    if user["role"] == "student":
        query += " AND student_applications.student_id = ?"
        params.append(user["id"])
    elif user["role"] == "faculty":
        query += " AND " + faculty_data_clause("users")
        params.extend([user["institution_type"], user["institution_name"], user["branch"]])
    query += " ORDER BY student_applications.application_date DESC, student_applications.created_at DESC"
    rows = db.execute(query, tuple(params)).fetchall()
    return render_template("applications.html", applications=rows)


@bp.route("/applications/<int:application_id>/download")
@login_required("admin", "faculty", "student")
def download_application(application_id):
    user = current_user()
    db = get_db()
    application = db.execute(
        """
        SELECT student_applications.*, users.branch
        FROM student_applications
        JOIN users ON users.id = student_applications.student_id
        WHERE student_applications.id = ?
        """,
        (application_id,),
    ).fetchone()
    if not application or not application["file_name"]:
        flash("Application file not found.", "error")
        return redirect(url_for("main.applications"))
    if user["role"] == "student" and application["student_id"] != user["id"]:
        flash("You cannot access this file.", "error")
        return redirect(url_for("main.applications"))
    if user["role"] == "faculty":
        student = db.execute("SELECT * FROM users WHERE id = ?", (application["student_id"],)).fetchone()
        if not student or (
            student["institution_type"] != user["institution_type"]
            or student["institution_name"] != user["institution_name"]
            or student["branch"] != user["branch"]
        ):
            flash("You cannot access this file.", "error")
            return redirect(url_for("main.applications"))
    return send_from_directory(
        application_upload_dir(),
        application["file_name"],
        as_attachment=True,
        download_name=application["original_file_name"] or application["file_name"],
    )


@bp.route("/faculty/<int:user_id>/edit", methods=["GET", "POST"])
@login_required("admin", "faculty")
def edit_faculty(user_id):
    db = get_db()
    faculty = db.execute("SELECT * FROM users WHERE id = ? AND role = 'faculty'", (user_id,)).fetchone()
    if not faculty:
        flash("Faculty not found.", "error")
        return redirect(url_for("main.dashboard"))
    viewer = current_user()
    if viewer["role"] != "admin" and viewer["id"] != faculty["id"]:
        flash("Only admin can edit other faculty accounts.", "error")
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        form_data = request.form.to_dict(flat=True)
        profile_picture, picture_error = save_profile_picture(request.files.get("profile_picture"), required=not bool(faculty["profile_picture"]))
        if picture_error:
            flash(picture_error, "error")
            return render_template("edit_faculty.html", faculty=faculty)
        if profile_picture:
            form_data["profile_picture"] = profile_picture
        errors = validate_profile_form(form_data, "faculty", editing_user_id=user_id)
        if errors:
            flash(errors[0], "error")
            return render_template("edit_faculty.html", faculty=faculty)
        if viewer["role"] == "faculty":
            create_pending_change(user_id, "faculty", viewer["id"], "admin", profile_payload(form_data, "faculty"))
            flash("The faculty profile update request has been sent for admin approval.", "success")
        else:
            update_user_record(user_id, form_data, "faculty")
            flash("Faculty details updated successfully.", "success")
        return redirect(url_for("main.dashboard"))
    return render_template("edit_faculty.html", faculty=faculty)


@bp.route("/approvals")
@login_required("admin", "faculty")
def approvals():
    user = current_user()
    request_type = request.args.get("type", "").strip()
    requests = pending_requests_for_user(user)
    history = approval_history_for_user(user)
    if request_type in {"student", "faculty"}:
        requests = [item for item in requests if item["target_role"] == request_type]
        history = [item for item in history if item["target_role"] == request_type]
    return render_template("approvals.html", requests=requests, history=history, selected_request_type=request_type)


@bp.route("/approvals/<int:change_id>/<action>", methods=["POST"])
@login_required("admin", "faculty")
def process_approval(change_id, action):
    db = get_db()
    user = current_user()
    request_type = request.form.get("type", "").strip()
    redirect_args = {"type": request_type} if request_type in {"student", "faculty"} else {}
    change = db.execute("SELECT * FROM pending_changes WHERE id = ?", (change_id,)).fetchone()
    if not change or change["status"] != "pending":
        flash("Approval request not found.", "error")
        return redirect(url_for("main.approvals", **redirect_args))
    if not approver_can_process(user, change):
        flash("You cannot process this request.", "error")
        return redirect(url_for("main.approvals", **redirect_args))
    if action == "approve":
        update_user_record(change["target_user_id"], json.loads(change["payload"]), change["target_role"])
        db = get_db()
        db.execute("UPDATE pending_changes SET status = 'approved', resolved_at = CURRENT_TIMESTAMP WHERE id = ?", (change_id,))
        db.commit()
        flash("Request approved and changes applied.", "success")
    elif action == "reject":
        db.execute("UPDATE pending_changes SET status = 'rejected', resolved_at = CURRENT_TIMESTAMP WHERE id = ?", (change_id,))
        db.commit()
        flash("Request rejected.", "success")
    else:
        flash("Invalid action.", "error")
    return redirect(url_for("main.approvals", **redirect_args))


@bp.route("/attendance", methods=["GET", "POST"])
@login_required("admin", "faculty")
def attendance():
    db = get_db()
    user = current_user()
    selected_date = request.values.get("attendance_date", today_iso())
    selected_branch = request.values.get("branch", "").strip()
    selected_year_group = request.values.get("year_group", "").strip()
    available_branches = branch_options(user)
    if user["role"] == "faculty":
        selected_branch = user["branch"]
    query = """
        SELECT
            users.id, users.institution_name, users.class_roll_number, users.roll_number, users.full_name,
            users.branch, users.semester, users.year, attendance.status, attendance.remarks
        FROM users
        LEFT JOIN attendance
            ON attendance.student_id = users.id AND attendance.attendance_date = ?
        WHERE users.role = 'student'
    """
    params = [selected_date]
    if user["role"] == "faculty":
        query += " AND " + faculty_data_clause("users")
        params.extend([user["institution_type"], user["institution_name"], user["branch"]])
    if selected_branch:
        query += " AND users.branch = ?"
        params.append(selected_branch)
    query, params = add_semester_group_filter(query, params, "users.semester", selected_year_group)
    query += " ORDER BY " + STUDENT_SORT_SQL
    students = db.execute(query, tuple(params)).fetchall()
    if request.method == "POST":
        for student in students:
            status = request.form.get(f"status_{student['id']}", "Absent")
            remarks = request.form.get(f"remarks_{student['id']}", "").strip()
            db.execute(
                """
                INSERT INTO attendance (student_id, attendance_date, status, marked_by, remarks)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(student_id, attendance_date)
                DO UPDATE SET status = excluded.status, marked_by = excluded.marked_by, remarks = excluded.remarks
                """,
                (student["id"], selected_date, status, user["id"], remarks),
            )
        db.commit()
        flash("Attendance saved successfully.", "success")
        return redirect(url_for("main.attendance", attendance_date=selected_date, branch=selected_branch, year_group=selected_year_group))
    return render_template("attendance.html", students=students, grouped_students=grouped_students(students), selected_date=selected_date, branches=available_branches, selected_branch=selected_branch, selected_year_group=selected_year_group)


@bp.route("/attendance/report")
@login_required("admin", "faculty", "student")
def attendance_report():
    user = current_user()
    db = get_db()
    from_date = request.args.get("from_date", one_year_ago_iso())
    to_date = request.args.get("to_date", today_iso())
    selected_branch = request.args.get("branch", "").strip()
    available_branches = branch_options(user)
    if user["role"] == "faculty":
        selected_branch = user["branch"]
    query = """
        SELECT
            attendance.attendance_date,
            users.institution_name,
            users.class_roll_number,
            users.roll_number,
            users.full_name,
            users.branch,
            users.semester,
            users.year,
            attendance.status,
            attendance.remarks,
            marker.full_name AS marked_by_name
        FROM attendance
        JOIN users ON users.id = attendance.student_id
        JOIN users AS marker ON marker.id = attendance.marked_by
        WHERE attendance.attendance_date BETWEEN ? AND ?
    """
    params = [from_date, to_date]
    if user["role"] == "student":
        query += " AND attendance.student_id = ?"
        params.append(user["id"])
    elif user["role"] == "faculty":
        query += " AND " + faculty_data_clause("users")
        params.extend([user["institution_type"], user["institution_name"], user["branch"]])
    if selected_branch:
        query += " AND users.branch = ?"
        params.append(selected_branch)
    query += """
        ORDER BY attendance.attendance_date DESC,
                 CASE
                     WHEN COALESCE(NULLIF(users.class_roll_number, ''), '') GLOB '[0-9]*'
                     THEN CAST(users.class_roll_number AS INTEGER)
                     ELSE 999999999
                 END,
                 users.class_roll_number,
                 CASE
                     WHEN COALESCE(NULLIF(users.roll_number, ''), '') GLOB '[0-9]*'
                     THEN CAST(users.roll_number AS INTEGER)
                     ELSE 999999999
                 END,
                 users.roll_number,
                 users.full_name
    """
    rows = db.execute(query, tuple(params)).fetchall()
    if request.args.get("export") == "xlsx":
        workbook = attendance_workbook(rows)
        return send_file(workbook, as_attachment=True, download_name=f"attendance-report-{from_date}-to-{to_date}.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return render_template("attendance_report.html", rows=rows, from_date=from_date, to_date=to_date, branches=available_branches, selected_branch=selected_branch)
