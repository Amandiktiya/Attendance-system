from functools import wraps

from flask import flash, redirect, session, url_for

from .database import get_db


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def login_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                flash("Please login first.", "error")
                return redirect(url_for("main.login"))
            if roles and user["role"] not in roles:
                flash("You do not have access to this page.", "error")
                return redirect(url_for("main.dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return decorator
