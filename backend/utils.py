"""
Shared backend helpers:

  * the application state store (a JSON-backed dict, guarded by a lock)
  * ID generation and timestamps
  * token handling for the demo API
  * current-user resolution and role decorators
  * input validators (email, USN, password)
  * file-upload validation (extension + MIME + magic bytes + size)
  * notification creation

DB records are persisted to DATA_FILE after API requests. Bearer tokens
remain memory-only and expire; users sign in again after a server restart.
"""

import json
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import wraps

from flask import g, request
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# =====================================================================
# 1. Application store (persisted JSON document)
# =====================================================================

_lock = threading.RLock()

DB_DEFAULTS = {
    "users": {},
    "projects": {},
    "mentoring_requests": {},
    "sessions": {},
    "messages": {},
    "notifications": {},
    "password_resets": {},
    "activity": [],
}
DB = {key: (list(value) if isinstance(value, list) else dict(value))
      for key, value in DB_DEFAULTS.items()}

# Bearer tokens are deliberately memory-only. A server restart invalidates
# old sessions; users simply sign in again. Password hashes and user records
# are persisted separately in the application data file.
TOKENS = {}


def load_db_state(path):
    """Load the JSON database, keeping the DB object identity stable."""
    with _lock:
        loaded = {}
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    candidate = json.load(handle)
                if isinstance(candidate, dict):
                    loaded = candidate
            except (OSError, json.JSONDecodeError):
                # Do not silently overwrite a corrupt database. The caller
                # gets a clear startup error rather than losing user records.
                raise RuntimeError(
                    f"Could not read the application database at {path}. "
                    "Restore a valid backup before starting the server."
                )
        DB.clear()
        for key, default in DB_DEFAULTS.items():
            value = loaded.get(key, default)
            if isinstance(default, dict) and not isinstance(value, dict):
                value = {}
            if isinstance(default, list) and not isinstance(value, list):
                value = []
            DB[key] = value

        # Existing accounts created before approval workflow existed are
        # intentionally unapproved (except a previously configured admin).
        for user in DB["users"].values():
            if not isinstance(user, dict):
                continue
            if user.get("role") == "admin":
                user.setdefault("approvalStatus", "Approved")
            else:
                user.setdefault("approvalStatus", "Pending")
        TOKENS.clear()


def save_db_state(path):
    """Atomically persist the complete application state."""
    if not path:
        return
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with _lock:
        fd, temp_path = tempfile.mkstemp(prefix=".pgmp-", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(DB, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


# =====================================================================
# Optional Firebase Firestore cloud persistence
# =====================================================================

_firestore_client = None
_firestore_checked = False


def _get_firestore():
    """Return Firestore when enabled; otherwise keep the local JSON mode."""
    global _firestore_client, _firestore_checked
    if _firestore_checked:
        return _firestore_client
    _firestore_checked = True
    if os.environ.get("FIREBASE_ENABLED", "false").lower() != "true":
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
        if not firebase_admin._apps:
            credential_path = os.environ.get("FIREBASE_CREDENTIALS", "").strip()
            if credential_path:
                firebase_admin.initialize_app(credentials.Certificate(credential_path))
            else:
                firebase_admin.initialize_app()
        _firestore_client = firestore.client()
        return _firestore_client
    except Exception as exc:
        _firestore_checked = False
        raise RuntimeError(
            "Firebase is enabled but could not initialize. Check the service-account "
            "path and firebase-admin installation. Details: " + str(exc)
        ) from exc


def load_cloud_db_state():
    """Load all shared collections from Firestore."""
    client = _get_firestore()
    if client is None:
        return False
    with _lock:
        for key, default in DB_DEFAULTS.items():
            docs = list(client.collection("pgmp_" + key).stream())
            if docs:
                if isinstance(default, dict):
                    DB[key] = {doc.id: doc.to_dict() for doc in docs}
                else:
                    records = [doc.to_dict() for doc in docs]
                    records.sort(key=lambda item: item.get("_cloudOrder", 0))
                    for item in records:
                        item.pop("_cloudOrder", None)
                    DB[key] = records
            # Empty remote collections do not erase existing local JSON data;
            # the startup sync below uploads those records on first connection.
        for user in DB["users"].values():
            if user.get("role") == "admin":
                user.setdefault("approvalStatus", "Approved")
            else:
                user.setdefault("approvalStatus", "Pending")
    return True


def save_cloud_db_state():
    """Write the current collections to Firestore in safe-sized batches."""
    client = _get_firestore()
    if client is None:
        return False
    with _lock:
        snapshot = {
            key: (dict(value) if isinstance(value, dict) else list(value))
            for key, value in DB.items()
        }
    for key, value in snapshot.items():
        collection = client.collection("pgmp_" + key)
        if isinstance(value, dict):
            desired = {str(doc_id): record for doc_id, record in value.items()}
        else:
            desired = {
                "entry_" + str(index).zfill(8): {**record, "_cloudOrder": index}
                for index, record in enumerate(value)
            }
        existing_ids = {doc.id for doc in collection.stream()}
        operations = (
            [("set", doc_id, record) for doc_id, record in desired.items()]
            + [("delete", doc_id, None) for doc_id in existing_ids - set(desired)]
        )
        for offset in range(0, len(operations), 450):
            batch = client.batch()
            for operation, doc_id, record in operations[offset:offset + 450]:
                ref = collection.document(doc_id)
                if operation == "set":
                    batch.set(ref, record)
                else:
                    batch.delete(ref)
            batch.commit()
    return True


# =====================================================================
# 2. Small utilities
# =====================================================================

def new_id(prefix: str) -> str:
    """Return a short, unique, prefixed identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string (second precision)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm_email(value: str) -> str:
    """Lower-case and strip an email address for comparisons."""
    return (value or "").strip().lower()


def ok(data=None, **extra):
    """Successful JSON response body."""
    payload = {"success": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return payload


def err(message: str, status: int = 400, **extra):
    """Error JSON response: returns a (body, status) tuple for Flask."""
    payload = {"success": False, "error": message}
    payload.update(extra)
    return payload, status


# =====================================================================
# 3. Serialisation
# =====================================================================

# Fields that must never leave the server.
_SECRET_FIELDS = {"passwordHash", "password_hash", "password", "resetToken", "reset_token"}


def public_user(user: dict):
    """Strip secrets before returning a user object to the client."""
    if not user:
        return None
    return {k: v for k, v in user.items() if k not in _SECRET_FIELDS}


def public_users(user_list):
    return [public_user(u) for u in user_list]


# =====================================================================
# 4. Authentication helpers
# =====================================================================

def hash_password(raw: str) -> str:
    """PBKDF2-SHA256 hash with a random salt (Werkzeug default)."""
    return generate_password_hash(raw, method="pbkdf2:sha256")


def verify_password(stored_hash: str, raw: str) -> bool:
    try:
        return check_password_hash(stored_hash, raw)
    except Exception:
        return False


def issue_token(user_id: str, ttl_seconds: int = 43200) -> str:
    token = uuid.uuid4().hex + uuid.uuid4().hex[:8]
    with _lock:
        TOKENS[token] = {
            "userId": user_id,
            "expiresAt": time.time() + max(300, int(ttl_seconds)),
        }
    return token


def revoke_token(token: str) -> None:
    with _lock:
        TOKENS.pop(token, None)


def token_from_request():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def current_user():
    """Resolve the caller from an unexpired Bearer token, or None."""
    token = token_from_request()
    if not token:
        return None
    with _lock:
        record = TOKENS.get(token)
        if not record:
            return None
        # Handle legacy in-process token values safely during hot reloads.
        if isinstance(record, str):
            user_id = record
        else:
            if record.get("expiresAt", 0) <= time.time():
                TOKENS.pop(token, None)
                return None
            user_id = record.get("userId")
        return DB["users"].get(user_id)


def require_auth(fn):
    """Decorator: 401 for no session, 403 until account approval."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return err("Authentication required. Please log in again.", 401)
        if user.get("status") == "Deactivated":
            return err("This account has been deactivated.", 403)
        if user.get("role") != "admin" and user.get("approvalStatus") != "Approved":
            return err("Your account is awaiting approval. You cannot access the platform yet.", 403)
        g.user = user
        return fn(*args, **kwargs)
    return wrapper

def require_roles(*roles):
    """Decorator: 403 unless the caller's role is in `roles`."""
    def decorator(fn):
        @wraps(fn)
        @require_auth
        def wrapper(*args, **kwargs):
            if g.user.get("role") not in roles:
                return err(
                    "You do not have permission to perform this action.", 403
                )
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# =====================================================================
# 5. Validators
# =====================================================================

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def is_valid_email_format(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip()))


def is_valid_usn(usn: str, pattern: str) -> bool:
    return bool(re.match(pattern, (usn or "").strip().upper()))


def password_problem(password: str):
    """
    Return a human-readable problem string, or None if the password is OK.
    Rules: >= 8 chars, at least one letter, at least one digit.
    """
    if not password or len(password) < 8:
        return "Password must be at least 8 characters long."
    if not re.search(r"[A-Za-z]", password):
        return "Password must contain at least one letter."
    if not re.search(r"[0-9]", password):
        return "Password must contain at least one number."
    return None


def missing_fields(data: dict, fields):
    """Return the list of required keys that are missing or blank."""
    missing = []
    for f in fields:
        value = data.get(f)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(f)
    return missing


# =====================================================================
# 6. File-upload validation
# =====================================================================

# Magic-byte signatures for the formats we accept.
_SIGNATURES = {
    b"%PDF": "pdf",
    b"PK\x03\x04": "zip",       # docx / pptx / xlsx are ZIP containers
    b"\xd0\xcf\x11\xe0": "ole",  # legacy doc / ppt / xls
}


def sniff_signature(head: bytes) -> str:
    """Return a rough family name for the first bytes of a file."""
    for sig, name in _SIGNATURES.items():
        if head.startswith(sig):
            return name
    return "unknown"


def sanitize_filename(name: str) -> str:
    """
    Produce a filesystem-safe filename.

    secure_filename() strips directory separators, '..', and non-ASCII
    characters, which prevents path traversal.
    """
    base = os.path.basename(name or "")
    safe = secure_filename(base)
    if not safe:
        safe = "upload"
    # Keep a sensible length so we do not blow up the filesystem limit.
    return safe[:120]


def validate_upload(file_storage, config):
    """
    Validate an uploaded FileStorage.

    Returns (True, info_dict) on success or (False, message) on failure.

    Checks performed:
      1. A filename exists and has an extension.
      2. The extension is on the allow-list.
      3. The declared MIME type is plausible (when provided).
      4. The file's magic bytes match the claimed extension family.
      5. The size is within the configured limit.
    """
    if file_storage is None or not file_storage.filename:
        return False, "No file was provided."

    original = file_storage.filename
    if "." not in original:
        return False, "The file must have an extension (for example .pdf)."

    ext = original.rsplit(".", 1)[1].lower()
    if ext not in config["ALLOWED_EXTENSIONS"]:
        allowed = ", ".join(sorted(config["ALLOWED_EXTENSIONS"]))
        return False, f"Unsupported file type '.{ext}'. Allowed types: {allowed}."

    # --- MIME check (best-effort: browsers are inconsistent) -------
    mime = (file_storage.mimetype or "").lower()
    if mime and mime not in config["ALLOWED_MIMETYPES"]:
        return False, f"The browser reported an unsupported content type: {mime}."

    # --- Size + signature check ------------------------------------
    stream = file_storage.stream
    try:
        start = stream.tell()
    except Exception:
        start = 0

    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    head = stream.read(8)
    stream.seek(0)

    if size == 0:
        return False, "The uploaded file is empty."

    if size > config["MAX_CONTENT_LENGTH"]:
        limit_mb = config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return False, f"The file is too large. Maximum allowed size is {limit_mb} MB."

    family = sniff_signature(head)

    # Files that legitimately start with plain text: csv (and some xls exports)
    text_like = ext == "csv"

    compatible = {
        "pdf": {"pdf"},
        "ppt": {"ole"},
        "doc": {"ole"},
        "xls": {"ole"},
        "pptx": {"zip"},
        "docx": {"zip"},
        "xlsx": {"zip"},
    }
    expected = compatible.get(ext, set())

    if expected and family not in expected:
        if not (text_like and family == "unknown"):
            return False, (
                f"The file content does not look like a real .{ext} file. "
                "Please upload a genuine document."
            )

    safe_name = sanitize_filename(original)

    return True, {
        "originalName": original,
        "safeName": safe_name,
        "extension": ext,
        "size": size,
        "mime": mime,
        "signatureFamily": family,
        "stream": stream,
        "start": start,
    }


# =====================================================================
# 7. Notifications
# =====================================================================

def add_notification(user_id, ntype, title, message, link=None, meta=None):
    """
    Create a notification for a user.

    This is called by real actions (project reviewed, session scheduled,
    message sent, ...). Nothing here invents fake events.
    """
    if not user_id or user_id not in DB["users"]:
        return None
    notif = {
        "id": new_id("notif"),
        "userId": user_id,
        "type": ntype,
        "title": title,
        "message": message,
        "link": link,
        "meta": meta or {},
        "read": False,
        "createdAt": now_iso(),
    }
    with _lock:
        DB["notifications"][notif["id"]] = notif
    return notif


# =====================================================================
# 8. Lookups used by several blueprints
# =====================================================================

def find_user_by_email(email):
    target = norm_email(email)
    for user in DB["users"].values():
        if norm_email(user.get("email")) == target:
            return user
    return None


def find_user_by_usn(usn):
    target = (usn or "").strip().upper()
    if not target:
        return None
    for user in DB["users"].values():
        if (user.get("usn") or "").strip().upper() == target:
            return user
    return None


def mentor_directory():
    """Every approved, active user who can act as a mentor."""
    return [
        u for u in DB["users"].values()
        if u.get("role") in ("lecturer", "alumni")
        and u.get("status", "Active") == "Active"
        and u.get("approvalStatus") == "Approved"
    ]


def log_activity(user_id, action, detail=None):
    """
    Append an entry to a small in-memory activity feed used by the
    admin dashboard's "Recent platform activity" panel.
    """
    DB.setdefault("activity", [])
    DB["activity"].insert(0, {
        "id": new_id("act"),
        "userId": user_id,
        "action": action,
        "detail": detail or "",
        "createdAt": now_iso(),
    })
    # Keep the feed bounded.
    del DB["activity"][200:]