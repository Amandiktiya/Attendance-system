# Attendance Hub

Flask based attendance management app with:

- `admin`, `faculty`, `student` login flow
- student self-registration with demo mobile OTP verification
- student profile fields for branch, semester, year, DOB, father name, roll number, registration number, Gmail, email, and mobile
- admin/faculty student creation
- date-wise attendance marking with past 1 year selection
- student attendance view only access
- Excel export for filtered attendance report

## Run

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

## Default admin

- Email: `admin@example.com`
- Password: `admin123`

For deployment, set environment variables on the hosting service so the deployed
database gets the same admin login:

```text
ADMIN_EMAIL=amandiktiya@gmail.com
ADMIN_PASSWORD=your-new-password
ADMIN_NAME=Aman Admin
```

## Notes

- Student login uses `roll number + password`.
- Admin and faculty login use `email + password`.
- OTP verification runs in demo mode and shows the generated OTP on the registration screen.
