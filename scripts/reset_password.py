from werkzeug.security import generate_password_hash
import sqlite3

USER_ID = 8
NEW_PASS = 'student123'

conn = sqlite3.connect('attendance.db')
cur = conn.cursor()
cur.execute('UPDATE users SET password_hash = ? WHERE id = ?', (generate_password_hash(NEW_PASS), USER_ID))
conn.commit()
print(f"Password for user {USER_ID} set to '{NEW_PASS}' (hashed in DB).")
