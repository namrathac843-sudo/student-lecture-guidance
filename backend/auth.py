"""
Authentication endpoints.

POST /api/auth/register
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
POST /api/auth/forgot-password
POST /api/auth/reset-password

SECURITY NOTE
-------------
Credentials and account approvals are checked by the Flask server. User
records persist in the configured JSON data file. Bearer tokens are
short-lived, memory-only sessions and are invalidated when Flask restarts.
"""


from flask import Blueprint, current_app, g, request

from .utils import (
    DB, add_notification, err, find_user_by_email, find_user_by_usn,
    hash_password, is_valid_email_format, is_valid_usn, issue_token,
    log_activity, new_id, norm_email, now_iso, ok,
    password_problem, public_user, require_auth, revoke_token,
    token_from_request, verify_password, _lock,
)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


# ---------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------

def _validate_email_for_role(cfg, role, email, usn):
    """
    Apply the college email rules.

    Rules
    -----
    Student:
        If REQUIRE_STUDENT_USN_EMAIL is true, the email MUST be
        <USN>@<COLLEGE_EMAIL_DOMAIN>, and the USN part must equal the
        USN the student typed.
    Lecturer / Alumni:
        If STRICT_EMAIL_FOR_ALL_ROLES is true, they must also use the
        college domain. Otherwise any valid email is accepted.
    Admin:
        Never allowed through public registration.

    Returns (True, "") or (False, "message").
    """
    domain = cfg["COLLEGE_EMAIL_DOMAIN"].lower()
    email_l = norm_email(email)

    if not is_valid_email_format(email_l):
        return False, "Please enter a valid email address."

    if role == "student":
        if cfg["REQUIRE_STUDENT_USN_EMAIL"]:
            expected_local = (usn or "").strip().lower()
            if not expected_local:
                return False, "USN is required before the email can be validated."
            expected = f"{expected_local}@{domain}"
            if email_l != expected:
                return False, (
                    f"Your college email must be exactly '{expected}' "
                    "(your USN followed by the college domain)."
                )
    else:
        if cfg["STRICT_EMAIL_FOR_ALL_ROLES"]:
            if not email_l.endswith("@" + domain):
                return False, (
                    f"Your email must end with '@{domain}' for this role."
                )

    return True, ""


def _validate_usn_for_role(cfg, role, usn):
    # Only students use the college USN format (for example 1GA21CS001).
    # The optional lecturer field is a faculty/institutional ID and may use
    # a different format, so it is length-limited but not forced into the
    # student USN pattern.
    if role == "student":
        if not usn:
            return False, "USN is required for students."
        if not is_valid_usn(usn, cfg["USN_PATTERN"]):
            return False, (
                "USN format looks invalid. Expected something like "
                "'1GA21CS001'. Ask your administrator to adjust the "
                "USN_PATTERN setting if your college uses another format."
            )
    elif len(usn) > 60:
        return False, "Institutional ID must not exceed 60 characters."
    return True, ""


# ---------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------

@auth_bp.post("/register")
def register():
    cfg = current_app.config
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return err("A valid JSON registration object is required.")

    required = [
        "fullName", "email", "collegeName", "department",
        "role", "password", "confirmPassword",
    ]
    # Reject non-string values before calling string methods or password
    # hashing. This turns malformed API payloads into a clear 400 response
    # instead of an avoidable server error.
    missing = [
        field for field in required
        if not isinstance(data.get(field), str) or not data[field].strip()
    ]
    if missing:
        return err("Please fill in all required fields: " + ", ".join(missing))

    role = data["role"].strip().lower()
    if role not in ("student", "lecturer", "alumni"):
        return err(
            "Public registration is available for students, lecturers and "
            "alumni only. Administrator accounts are created by an existing "
            "administrator."
        )

    email = norm_email(data["email"])
    if len(email) > 254:
        return err("Email address must not exceed 254 characters.")
    raw_usn = data.get("usn", data.get("institutionalId", ""))
    if raw_usn is not None and not isinstance(raw_usn, str):
        return err("USN / institutional ID must be text.")
    usn = (raw_usn or "").strip().upper()

    # --- Email rules ------------------------------------------------
    ok_email, email_msg = _validate_email_for_role(cfg, role, email, usn)
    if not ok_email:
        return err(email_msg)

    # --- USN rules ---------------------------------------------------
    ok_usn, usn_msg = _validate_usn_for_role(cfg, role, usn)
    if not ok_usn:
        return err(usn_msg)

    # --- Password -----------------------------------------------------
    if len(data["password"]) > 128:
        return err("Password must not exceed 128 characters.")
    problem = password_problem(data["password"])
    if problem:
        return err(problem)
    if data["password"] != data["confirmPassword"]:
        return err("Password and Confirm Password do not match.")

    # Normalize optional list inputs so malformed values cannot poison
    # directory rendering code which expects arrays of strings.
    def clean_list(key):
        value = data.get(key, [])
        if not isinstance(value, list):
            return []
        return [item.strip()[:80] for item in value if isinstance(item, str) and item.strip()][:30]

    def clean_text(key, limit=160):
        value = data.get(key, "")
        return value.strip()[:limit] if isinstance(value, str) else ""

    # --- Build the record ---------------------------------------------
    user = {
        "id": new_id("user"),
        "fullName": clean_text("fullName", 120),
        "email": email,
        "collegeName": clean_text("collegeName", 160),
        "department": clean_text("department", 120),
        "usn": usn,
        "role": role,
        "passwordHash": hash_password(data["password"]),
        "status": "Active",
        "approvalStatus": "Pending",
        "approvedBy": None,
        "approvedAt": None,
        "avatar": "",
        "bio": "",
        "phone": "",
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
    }

    if role == "student":
        user.update({
            "semester": clean_text("semester", 20),
            "skills": clean_list("skills"),
            "interests": clean_text("interests", 500),
            "certifications": [],
            "courses": [],
            "mentorAvailability": False,
        })
    elif role == "lecturer":
        user.update({
            "designation": clean_text("designation", 120),
            "experienceYears": clean_text("experienceYears", 10),
            "expertise": clean_list("expertise"),
            "skills": clean_list("skills"),
            "mentorAvailability": True,
        })
    elif role == "alumni":
        user.update({
            "graduationDepartment": clean_text("graduationDepartment", 120),
            "graduationYear": clean_text("graduationYear", 10),
            "company": clean_text("company", 160),
            "jobDesignation": clean_text("jobDesignation", 120),
            "expertise": clean_list("expertise"),
            "skills": clean_list("skills"),
            "mentorAvailability": True,
        })

    # Check uniqueness and insert under one lock. Without this atomic
    # section, two simultaneous registrations could claim the same email
    # or institutional ID.
    with _lock:
        if find_user_by_email(email):
            return err("An account with this email already exists.", 409)
        if usn and find_user_by_usn(usn):
            return err("An account with this USN already exists.", 409)
        DB["users"][user["id"]] = user

    log_activity(user["id"], "registered", f"{user['fullName']} joined as {role}")
    add_notification(
        user["id"], "account",
        "Welcome to the platform",
        "Your registration request was received. You can log in after an administrator or lecturer approves your account.",
        link="profile.html",
    )

    return ok({
        "user": public_user(user),
        "message": "Registration submitted. Your account must be approved before you can log in.",
        "approvalStatus": "Pending",
    }), 201


# ---------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------

@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return err("A valid JSON login object is required.")
    if not isinstance(data.get("email"), str) or not isinstance(data.get("password"), str):
        return err("Email and password are required.")
    email = norm_email(data.get("email"))
    password = data.get("password")

    if not email or not password or len(password) > 128:
        return err("Email and password are required.")

    user = find_user_by_email(email)

    # Same generic message for "no such user" and "wrong password" so we
    # do not leak which emails are registered.
    if not user or not verify_password(user.get("passwordHash", ""), password):
        return err("Invalid email or password.", 401)

    if user.get("status") == "Deactivated":
        return err(
            "This account has been deactivated. Please contact an administrator.",
            403,
        )

    approval = user.get("approvalStatus", "Approved" if user.get("role") == "admin" else "Pending")
    if user.get("role") != "admin" and approval == "Pending":
        return err(
            "Your registration is awaiting approval. You will be able to log in after an administrator or lecturer approves it.",
            403,
            approvalStatus="Pending",
        )
    if user.get("role") != "admin" and approval != "Approved":
        return err(
            "Your account was not approved. Please contact the platform administrator.",
            403,
            approvalStatus=approval,
        )

    token = issue_token(user["id"], current_app.config.get("TOKEN_TTL_SECONDS", 43200))
    log_activity(user["id"], "logged in", user["email"])

    return ok({
        "token": token,
        "user": public_user(user),
    })


@auth_bp.post("/logout")
@require_auth
def logout():
    token = token_from_request()
    revoke_token(token)
    log_activity(g.user["id"], "logged out", "")
    return ok({"message": "You have been logged out."})


@auth_bp.get("/me")
@require_auth
def me():
    return ok({"user": public_user(g.user)})


@auth_bp.post("/change-password")
@require_auth
def change_password():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return err("A valid JSON password-change object is required.")
    current = data.get("currentPassword")
    new_password = data.get("newPassword")
    confirm = data.get("confirmPassword")
    if not all(isinstance(value, str) and value for value in (current, new_password, confirm)):
        return err("Current password, new password and confirmation are required.")
    if len(new_password) > 128:
        return err("Password must not exceed 128 characters.")
    if not verify_password(g.user.get("passwordHash", ""), current):
        return err("Current password is incorrect.", 401)
    problem = password_problem(new_password)
    if problem:
        return err(problem)
    if new_password != confirm:
        return err("New password and confirmation do not match.")
    with _lock:
        g.user["passwordHash"] = hash_password(new_password)
        g.user["updatedAt"] = now_iso()
    log_activity(g.user["id"], "changed password", "")
    return ok({"message": "Your password has been changed successfully."})


# ---------------------------------------------------------------------
# Forgot / reset password
# ---------------------------------------------------------------------
# No mail provider is configured in this project. We intentionally do not
# return reset tokens in API responses because that would let anyone take
# over an account by requesting a token for its email address. Password
# changes for signed-in users use /change-password. Configure an email
# provider before enabling external reset links.
# ---------------------------------------------------------------------

@auth_bp.post("/forgot-password")
def forgot_password():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("email"), str):
        return err("Please enter a valid email address.")
    email = norm_email(data.get("email"))
    if not email or len(email) > 254 or not is_valid_email_format(email):
        return err("Please enter a valid email address.")
    # Deliberately avoid account enumeration and never expose reset tokens.
    return ok({
        "message": (
            "For account security, password-reset links are not enabled on "
            "this installation. Please contact the platform administrator."
        )
    })

@auth_bp.post("/reset-password")
def reset_password():
    # No email delivery or reset-token issuance is configured. Reject every
    # token, including tokens possibly left in a database by an older build.
    return err(
        "Password reset is not enabled. Please contact the platform administrator.",
        403,
    )