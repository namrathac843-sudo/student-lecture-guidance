"""
Central configuration.

Every value can be overridden through environment variables, which keeps
secrets out of the source code and makes the college-specific rules
(email domain, USN pattern) easy to change.
"""

import os


class Config:
    # --- Security -------------------------------------------------
    _configured_secret = os.environ.get("SECRET_KEY", "").strip()
    SECRET_KEY = (
        _configured_secret
        if _configured_secret and _configured_secret != "replace-with-a-unique-random-secret-before-deployment"
        else os.urandom(32)
    )

    # --- Uploads --------------------------------------------------
    _default_upload = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads"
    )
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or _default_upload
    DATA_FILE = os.environ.get("DATA_FILE") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "instance", "pgmp-data.json"
    )
    TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", "43200"))

    # Maximum size of a single request body (Flask/Werkzeug enforces it).
    MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "10"))
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024

    # --- Allowed file types ---------------------------------------
    # Extensions we accept. NOTE: an extension alone proves nothing,
    # so we also sniff the file signature (magic bytes) in utils.py.
    ALLOWED_EXTENSIONS = {
        "pdf", "ppt", "pptx", "doc", "docx",
        "csv", "xls", "xlsx",
    }

    # MIME types we consider acceptable when the browser sends one.
    ALLOWED_MIMETYPES = {
        "application/pdf",
        "application/msword",
        "application/vnd.ms-powerpoint",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
        "application/csv",
        "application/octet-stream",   # many browsers use this for .csv/.xls
        "text/plain",                 # some browsers send this for .csv
    }

    # --- College-specific rules (see README) ----------------------
    COLLEGE_EMAIL_DOMAIN = os.environ.get("COLLEGE_EMAIL_DOMAIN", "gat.ac.in")
    USN_PATTERN = os.environ.get(
        "USN_PATTERN", r"^[0-9]{1}[A-Z]{2}[0-9]{2}[A-Z]{2}[0-9]{3}$"
    )
    REQUIRE_STUDENT_USN_EMAIL = (
        os.environ.get("REQUIRE_STUDENT_USN_EMAIL", "true").lower() == "true"
    )
    STRICT_EMAIL_FOR_ALL_ROLES = (
        os.environ.get("STRICT_EMAIL_FOR_ALL_ROLES", "false").lower() == "true"
    )

    # --- Project workflow -----------------------------------------
    PROJECT_STATUSES = [
        "Draft",
        "Submitted",
        "Under Review",
        "Accepted",
        "Changes Requested",
        "Rejected",
        "Completed",
        "Pending",
    ]

    # --- Mentoring -------------------------------------------------
    SESSION_PLATFORMS = ["Google Meet", "Zoom", "Other"]
    REQUEST_STATUSES = ["Pending", "Accepted", "Declined", "Cancelled"]