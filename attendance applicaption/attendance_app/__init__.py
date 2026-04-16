from flask import Flask

from .database import init_app
from .routes import bp


def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="attendance-app-dev-secret",
        DATABASE="attendance.db",
        OTP_EXPIRY_MINUTES=5,
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        APPLICATION_UPLOAD_FOLDER="uploads/applications",
        PROFILE_UPLOAD_FOLDER="uploads/profiles",
    )

    init_app(app)
    app.register_blueprint(bp)
    return app
