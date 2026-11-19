"""Role-aware account approval endpoints.

GET  /api/approvals/pending
POST /api/approvals/<user_id>  {"decision": "Approved" | "Rejected"}

Administrators may approve/reject pending lecturer, student and alumni
registrations. Lecturers may approve/reject students only.
"""

from flask import Blueprint, g, request

from .utils import (
    DB, _lock, add_notification, err, log_activity, now_iso, ok,
    public_user, require_auth,
)

approvals_bp = Blueprint("approvals", __name__, url_prefix="/api/approvals")


@approvals_bp.get("/summary")
@require_auth
def approval_summary():
    if g.user.get("role") != "admin":
        return err("Administrator access is required.", 403)
    users = list(DB["users"].values())
    return ok({
        "totalUsers": len(users),
        "pending": sum(1 for user in users if user.get("role") != "admin" and user.get("approvalStatus") == "Pending"),
        "approved": sum(1 for user in users if user.get("approvalStatus") == "Approved"),
        "rejected": sum(1 for user in users if user.get("approvalStatus") == "Rejected"),
        "students": sum(1 for user in users if user.get("role") == "student"),
        "lecturers": sum(1 for user in users if user.get("role") == "lecturer"),
        "alumni": sum(1 for user in users if user.get("role") == "alumni"),
    })


@approvals_bp.get("/pending")
@require_auth
def pending_accounts():
    role = g.user.get("role")
    if role == "admin":
        pending = [
            user for user in DB["users"].values()
            if user.get("role") in ("student", "lecturer", "alumni")
            and user.get("approvalStatus", "Pending") == "Pending"
        ]
    elif role == "lecturer":
        pending = [
            user for user in DB["users"].values()
            if user.get("role") == "student"
            and user.get("approvalStatus", "Pending") == "Pending"
        ]
    else:
        return err("Only administrators and lecturers can view pending account requests.", 403)

    pending.sort(key=lambda user: user.get("createdAt", ""))
    return ok({
        "users": [public_user(user) for user in pending],
        "count": len(pending),
        "canApproveRoles": ["student", "lecturer", "alumni"] if role == "admin" else ["student"],
    })


@approvals_bp.post("/<user_id>")
@require_auth
def decide_account(user_id):
    data = request.get_json(silent=True) or {}
    decision = (data.get("decision") or "").strip().title()
    if decision not in ("Approved", "Rejected"):
        return err("Decision must be Approved or Rejected.")

    with _lock:
        target = DB["users"].get(user_id)
        if not target:
            return err("Account not found.", 404)
        if target.get("role") == "admin":
            return err("Administrator accounts cannot be approved through this workflow.", 403)
        if target.get("id") == g.user.get("id"):
            return err("You cannot approve or reject your own account.", 403)
        if target.get("approvalStatus", "Pending") != "Pending":
            return err("This account request has already been reviewed.", 409)
        if g.user.get("role") == "lecturer" and target.get("role") != "student":
            return err("Lecturers are allowed to approve student accounts only.", 403)
        if g.user.get("role") not in ("admin", "lecturer"):
            return err("You do not have permission to review accounts.", 403)

        target["approvalStatus"] = decision
        target["approvedBy"] = g.user["id"]
        target["approvedAt"] = now_iso()
        target["updatedAt"] = now_iso()
        if decision == "Approved":
            target["status"] = "Active"
        else:
            target["status"] = "Rejected"

    verb = "approved" if decision == "Approved" else "rejected"
    log_activity(g.user["id"], f"{verb} account", target.get("email", ""))
    add_notification(
        target["id"],
        "account",
        "Account approved" if decision == "Approved" else "Account not approved",
        (
            "Your account has been approved. You can now sign in."
            if decision == "Approved"
            else "Your account request was not approved. Please contact the platform administrator."
        ),
        link="login.html",
    )
    return ok({"user": public_user(target), "message": f"Account {decision.lower()} successfully."})
