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

## Notes

- Student login uses `roll number + password`.
- Admin and faculty login use `email + password`.
- OTP feature is wired as a demo flow and shows OTP on screen. Real SMS provider integration can be added later.
