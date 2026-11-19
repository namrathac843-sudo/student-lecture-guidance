"""
Application factory.

Creates the Flask app, registers every REST blueprint, and serves the
frontend single-page files from ./frontend.

Why a factory?
  It keeps imports clean and lets tests create a fresh app instance
  without side effects.
"""

import os

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from .config import Config
from .utils import load_db_state, save_db_state, load_cloud_db_state, save_cloud_db_state

# Absolute path to the frontend folder, computed once.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")


def create_app(test_config=None):
    # static_folder=None because WE serve the frontend manually below.
    app = Flask(__name__, static_folder=None)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    # Allow the Firebase Hosting frontend to call this Render API.
    allowed_origins = {
        origin.strip().rstrip("/")
        for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    }

    @app.after_request
    def add_cors_headers(response):
        origin = request.headers.get("Origin", "").rstrip("/")
        if origin and (origin in allowed_origins or "*" in allowed_origins):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Accept"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        return response

    # Prepare local storage and restore server-side user/account records.
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(app.config["DATA_FILE"])), exist_ok=True)
    load_db_state(app.config["DATA_FILE"])
    cloud_enabled = load_cloud_db_state()
    if cloud_enabled:
        save_cloud_db_state()  # migrate existing local JSON records on first setup

    # ---------------------------------------------------------------
    # Register API blueprints
    # ---------------------------------------------------------------
    from .auth import auth_bp
    from .users import users_bp
    from .projects import projects_bp
    from .mentors import mentors_bp
    from .sessions import sessions_bp
    from .messages import messages_bp
    from .notifications import notifications_bp
    from .approvals import approvals_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(mentors_bp)
    app.register_blueprint(sessions_bp)
    app.register_blueprint(messages_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(approvals_bp)

    # ---------------------------------------------------------------
    # Health check (useful for the frontend "backend online?" badge)
    # ---------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return jsonify({"success": True, "data": {"status": "ok", "approvalWorkflow": True}})

    @app.get("/api/config/public")
    def public_config():
        return jsonify({"success": True, "data": {
            "collegeEmailDomain": app.config["COLLEGE_EMAIL_DOMAIN"],
            "usnPattern": app.config["USN_PATTERN"],
            "requireStudentUsnEmail": app.config["REQUIRE_STUDENT_USN_EMAIL"],
            "strictEmailForAllRoles": app.config["STRICT_EMAIL_FOR_ALL_ROLES"],
        }})

    # ---------------------------------------------------------------
    # Frontend delivery
    # ---------------------------------------------------------------
    @app.get("/")
    def serve_index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.get("/<path:filename>")
    def serve_frontend(filename):
        """
        Serves any file inside ./frontend (html, css, js, images).

        send_from_directory already blocks path traversal ('../'),
        so this is safe.
        """
        return send_from_directory(FRONTEND_DIR, filename)

    @app.after_request
    def persist_api_state(response):
        if request.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            try:
                save_db_state(app.config["DATA_FILE"])
                save_cloud_db_state()
            except OSError:
                app.logger.exception("Could not persist application state")
        return response

    # ---------------------------------------------------------------
    # JSON error handlers so the API never returns an HTML error page
    # ---------------------------------------------------------------
    @app.errorhandler(HTTPException)
    def handle_http_exception(exc):
        if request.path.startswith("/api/"):
            return jsonify({
                "success": False,
                "error": exc.description or exc.name,
                "code": exc.code,
            }), exc.code
        # For non-API paths fall back to the default HTML behaviour.
        return exc

    @app.errorhandler(413)
    def handle_too_large(exc):
        limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return jsonify({
            "success": False,
            "error": f"File is too large. The limit is {limit_mb} MB.",
        }), 413

    @app.errorhandler(Exception)
    def handle_unexpected(exc):
        if request.path.startswith("/api/"):
            app.logger.exception("Unhandled API error")
            return jsonify({
                "success": False,
                "error": "Internal server error. Check the Flask console for details.",
            }), 500
        raise exc

    return app