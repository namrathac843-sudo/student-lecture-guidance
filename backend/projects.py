"""
Project endpoints.

GET    /api/projects                       list (role-filtered)
POST   /api/projects                       create (JSON metadata)
POST   /api/projects/<id>/upload           upload a file (multipart)
GET    /api/projects/<id>
PUT    /api/projects/<id>                  edit (owner / guide / admin)
POST   /api/projects/<id>/review           accept / reject / request changes
GET    /api/projects/<id>/download         download the stored file
GET    /api/projects/stats/summary         chart data

Files are written to UPLOAD_FOLDER/<project_id>/<safe_name>.
Project records are stored in the existing JSON-backed application store (and optional Firestore sync).
"""

import os
from datetime import date
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, g, request, send_file

from .utils import (
    DB, add_notification, err, log_activity, new_id, now_iso, ok,
    require_auth, require_roles, sanitize_filename, validate_upload, _lock,
)

projects_bp = Blueprint("projects", __name__, url_prefix="/api/projects")


def _validate_deadline(value):
    """Deadlines may be blank, today, or a future calendar date; never past."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        return "Deadline must be a valid date."
    try:
        deadline = date.fromisoformat(value)
    except ValueError:
        return "Deadline must use YYYY-MM-DD format."
    # Render hosts commonly use UTC. Compare against the college timezone.
    try:
        from datetime import datetime
        today = datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Kolkata"))).date()
    except Exception:
        today = date.today()
    if deadline < today:
        return "Past deadlines are not allowed. Choose today or a future date."
    return None

VALID_STATUSES = [
    "Draft", "Submitted", "Under Review",
    "Accepted", "Changes Requested", "Rejected", "Completed", "Pending",
]

# Which reviewer action moves a project to which status.
REVIEW_ACTIONS = {
    "start_review": "Under Review",
    "accept": "Accepted",
    "reject": "Rejected",
    "request_changes": "Changes Requested",
}


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _can_view(user, project):
    if user["role"] == "admin":
        return True
    if project.get("studentId") == user["id"]:
        return True
    if project.get("guideId") == user["id"]:
        return True
    if user["role"] == "lecturer":
        # All lecturers may inspect every student submission.
        return True
    if user["role"] == "alumni":
        # Alumni only see projects they are mentoring.
        return any(
            r.get("mentorId") == user["id"]
            and r.get("studentId") == project.get("studentId")
            and r.get("status") == "Accepted"
            for r in DB["mentoring_requests"].values()
        )
    return False


def _can_edit(user, project):
    return (
        user["role"] == "admin"
        or project.get("studentId") == user["id"]
        or project.get("guideId") == user["id"]
    )


def _push_history(project, actor, status, note):
    project.setdefault("history", []).append({
        "status": status,
        "by": actor["id"] if actor else None,
        "byName": actor["fullName"] if actor else "System",
        "note": note,
        "at": now_iso(),
    })


def _project_view(project):
    """Attach safe student/team identities for dashboard rendering."""
    result = dict(project)
    owner = DB["users"].get(project.get("studentId"))
    result["student"] = {
        key: owner.get(key) for key in ("id", "fullName", "email", "usn", "department")
    } if owner else None
    members = []
    for student_id in _team_student_ids(project):
        user = DB["users"].get(student_id)
        if user:
            members.append({key: user.get(key) for key in ("id", "fullName", "email", "usn", "department")})
    result["team"] = members
    guide = DB["users"].get(project.get("guideId"))
    result["guide"] = {
        key: guide.get(key) for key in ("id", "fullName", "email", "department")
    } if guide else None
    return result


def _team_student_ids(project):
    """Resolve team members supplied as IDs, USNs, emails, or structured records."""
    ids = {project.get("studentId")} if project.get("studentId") else set()
    for member in project.get("teamMembers", []) or []:
        if isinstance(member, dict):
            candidate = member.get("userId") or member.get("studentId") or member.get("id")
            usn = member.get("usn")
            email = member.get("email")
            name = member.get("name") or member.get("fullName")
        else:
            candidate, usn, email, name = member, member, member, member
        user = DB["users"].get(str(candidate)) if candidate else None
        if not user:
            target = str(usn or "").strip().lower()
            for item in DB["users"].values():
                if target and target in {
                    str(item.get("usn", "")).lower(),
                    str(item.get("email", "")).lower(),
                    str(item.get("fullName", "")).lower(),
                }:
                    user = item
                    break
        if user and user.get("role") == "student":
            ids.add(user["id"])
    return sorted(ids)


def _add_project_notification(user_id, title, message, project, status):
    # Idempotency: a repeated upload/review request cannot create the same
    # project/status notification twice for the same recipient.
    for existing in DB["notifications"].values():
        meta = existing.get("meta") or {}
        if (existing.get("userId") == user_id
                and meta.get("projectId") == project["id"]
                and meta.get("status") == status
                and existing.get("title") == title):
            return existing
    return add_notification(
        user_id, "project", title, message,
        link=f"projects.html?projectId={project['id']}",
        meta={"projectId": project["id"], "status": status},
    )


def _notify_project_team(project, title, message, status):
    for student_id in _team_student_ids(project):
        _add_project_notification(student_id, title, message, project, status)


def _notify_admins(title, message, project, status):
    for admin in DB["users"].values():
        if admin.get("role") == "admin" and admin.get("status") != "Deactivated":
            _add_project_notification(admin["id"], title, message, project, status)


def _notify_all_lecturers_new_submission(project):
    message = f"'{project['title']}' is a new project request awaiting your review."
    for lecturer in DB["users"].values():
        if lecturer.get("role") == "lecturer" and lecturer.get("approvalStatus") == "Approved" and lecturer.get("status") != "Deactivated":
            _add_project_notification(
                lecturer["id"], "New project submitted", message, project, project["status"]
            )
    for admin in DB["users"].values():
        if admin.get("role") == "admin" and admin.get("status") != "Deactivated":
            _add_project_notification(
                admin["id"], "New project submitted", message, project, project["status"]
            )


# ---------------------------------------------------------------------
# List / create
# ---------------------------------------------------------------------

@projects_bp.get("")
@require_auth
def list_projects():
    user = g.user
    all_projects = list(DB["projects"].values())

    if user["role"] == "student":
        visible = [p for p in all_projects if p.get("studentId") == user["id"]]
    elif user["role"] == "lecturer":
        # Every lecturer can see every submitted/reviewed project, regardless
        # of department or assigned guide. Drafts are not review requests.
        reviewable_statuses = {
            "Pending", "Submitted", "Under Review", "Changes Requested", "Accepted", "Rejected", "Completed"
        }
        visible = [p for p in all_projects if p.get("status") in reviewable_statuses]
    elif user["role"] == "alumni":
        mentee_ids = {
            r["studentId"] for r in DB["mentoring_requests"].values()
            if r.get("mentorId") == user["id"] and r.get("status") == "Accepted"
        }
        visible = [p for p in all_projects if p.get("studentId") in mentee_ids]
    else:  # admin
        visible = all_projects

    # Query filters
    status = request.args.get("status")
    if status:
        visible = [p for p in visible if p.get("status") == status]

    department = request.args.get("department")
    if department:
        visible = [p for p in visible if p.get("department") == department]

    q = (request.args.get("q") or "").strip().lower()
    if q:
        visible = [
            p for p in visible
            if q in p.get("title", "").lower()
            or q in p.get("description", "").lower()
        ]

    sort = request.args.get("sort", "newest")
    if sort == "oldest":
        visible.sort(key=lambda p: p.get("createdAt", ""))
    elif sort == "title":
        visible.sort(key=lambda p: p.get("title", "").lower())
    elif sort == "progress":
        visible.sort(key=lambda p: p.get("progress", 0), reverse=True)
    else:
        visible.sort(key=lambda p: p.get("createdAt", ""), reverse=True)

    capacity = None
    if user["role"] == "lecturer":
        used = sum(1 for p in DB["projects"].values()
                   if p.get("guideId") == user["id"] and p.get("status") == "Accepted")
        capacity = {"limit": 4, "accepted": used, "remaining": max(0, 4 - used)}
    return ok({
        "projects": [_project_view(p) for p in visible],
        "count": len(visible),
        "acceptanceCapacity": capacity,
    })


@projects_bp.post("")
@require_roles("student", "admin")
def create_project():
    data = request.get_json(silent=True) or {}

    if not (data.get("title") or "").strip():
        return err("A project title is required.")
    if not (data.get("description") or "").strip():
        return err("A project description is required.")

    deadline_error = _validate_deadline(data.get("deadline", ""))
    if deadline_error:
        return err(deadline_error, 400)

    student_id = data.get("studentId") or g.user["id"]
    if g.user["role"] == "student" and student_id != g.user["id"]:
        return err("You can only create projects for your own account.", 403)

    # A student submission is a project request, not a local-only draft.
    # Prevent accidental duplicate active requests for the same title.
    normalized_title = data["title"].strip().casefold()
    with _lock:
        if g.user["role"] == "student" and any(
            item.get("studentId") == student_id
            and (item.get("title") or "").strip().casefold() == normalized_title
            and item.get("status") not in ("Rejected", "Completed")
            for item in DB["projects"].values()
        ):
            return err("You already have an active request with this project title.", 409)

    initial_status = "Pending" if g.user["role"] == "student" else "Draft"
    project = {
        "id": new_id("project"),
        "title": data["title"].strip(),
        "description": data["description"].strip(),
        "technologies": data.get("technologies", ""),
        "category": data.get("category", ""),
        "department": data.get("department", g.user.get("department", "")),
        "semester": data.get("semester", g.user.get("semester", "")),
        "teamMembers": data.get("teamMembers", []),
        "studentId": student_id,
        "guideId": "",
        "deadline": data.get("deadline", ""),
        "status": initial_status,
        "progress": 0,
        "feedback": "",
        "fileName": "",
        "storedPath": "",
        "fileSize": 0,
        "createdAt": now_iso(),
        "submittedAt": now_iso() if initial_status == "Pending" else "",
        "updatedAt": now_iso(),
        "history": [],
    }
    _push_history(project, g.user, initial_status, "Project request submitted" if initial_status == "Pending" else "Project created")

    with _lock:
        # Re-check and insert in one critical section so two concurrent
        # submissions cannot create duplicate active requests.
        if g.user["role"] == "student" and any(
            item.get("studentId") == student_id
            and (item.get("title") or "").strip().casefold() == normalized_title
            and item.get("status") not in ("Rejected", "Completed")
            for item in DB["projects"].values()
        ):
            return err("You already have an active request with this project title.", 409)
        DB["projects"][project["id"]] = project

    log_activity(g.user["id"], "created project request" if initial_status == "Pending" else "created project", project["title"])

    if initial_status == "Pending":
        _notify_all_lecturers_new_submission(project)

    # Notify the chosen guide.
    if project["guideId"] and project["guideId"] in DB["users"]:
        add_notification(
            project["guideId"], "project",
            "New project assigned to you",
            f"{g.user['fullName']} created the project '{project['title']}'.",
            link="projects.html",
            meta={"projectId": project["id"]},
        )

    return ok({"project": project, "message": "Project created."}), 201


# ---------------------------------------------------------------------
# Single project
# ---------------------------------------------------------------------

@projects_bp.get("/<project_id>")
@require_auth
def get_project(project_id):
    project = DB["projects"].get(project_id)
    if not project:
        return err("Project not found.", 404)
    if not _can_view(g.user, project):
        return err("You are not allowed to view this project.", 403)
    return ok({"project": _project_view(project)})


@projects_bp.put("/<project_id>")
@require_auth
def update_project(project_id):
    project = DB["projects"].get(project_id)
    if not project:
        return err("Project not found.", 404)
    if not _can_edit(g.user, project):
        return err("You are not allowed to edit this project.", 403)

    data = request.get_json(silent=True) or {}

    # A student may not edit while the project is Accepted/Rejected
    # unless the lecturer requested changes.
    if g.user["role"] == "student" and project["status"] in ("Accepted", "Rejected", "Completed"):
        return err(
            f"This project is {project['status']} and can no longer be edited.",
            409,
        )

    if "deadline" in data:
        deadline_error = _validate_deadline(data.get("deadline", ""))
        if deadline_error:
            return err(deadline_error, 400)

    editable = {
        "title", "description", "category", "department", "semester",
        "teamMembers", "deadline", "technologies",
    }
    if g.user["role"] in ("lecturer", "admin"):
        editable |= {"progress", "feedback", "status"}

    if g.user["role"] == "lecturer" and project.get("guideId") != g.user["id"]:
        return err("Lecturers can update progress only for projects allocated to them.", 403)
    if "status" in data and data["status"] not in VALID_STATUSES:
        return err("Invalid project status.", 400)
    if g.user["role"] == "lecturer" and "status" in data and data["status"] != "Completed":
        return err("Lecturers may change an allocated project's status only to Completed. Use the review action for requests.", 403)
    if g.user["role"] == "lecturer" and "progress" in data and project.get("status") != "Accepted":
        return err("Progress can be updated only while the project is active.", 409)
    if data.get("status") == "Completed":
        if g.user["role"] not in ("lecturer", "admin"):
            return err("Only the assigned lecturer or an administrator can complete a project.", 403)
        if project.get("status") != "Accepted" or (g.user["role"] == "lecturer" and project.get("guideId") != g.user["id"]):
            return err("Only an active project assigned to you can be marked Completed.", 409)

    changed = []
    old_status = project.get("status")
    with _lock:
        # Re-read inside the critical section so concurrent updates do not
        # overwrite a completion or a guide reassignment.
        project = DB["projects"].get(project_id)
        if not project:
            return err("Project not found.", 404)
        if g.user["role"] == "lecturer" and project.get("guideId") != g.user["id"]:
            return err("Lecturers can update only their own allocated projects.", 403)
        if data.get("status") == "Completed" and project.get("status") != "Accepted":
            return err("Only an active Accepted project can be marked Completed.", 409)
        if "progress" in data and g.user["role"] == "lecturer" and project.get("status") != "Accepted":
            return err("Progress can be updated only while the project is active.", 409)
        for key, value in data.items():
            if key in editable and project.get(key) != value:
                project[key] = value
                changed.append(key)
        try:
            project["progress"] = max(0, min(100, int(project.get("progress", 0))))
        except (TypeError, ValueError):
            project["progress"] = 0
        if project.get("status") == "Completed":
            project["progress"] = 100
            project["completedAt"] = project.get("completedAt") or now_iso()
        project["updatedAt"] = now_iso()
        if changed:
            _push_history(project, g.user, project.get("status", old_status),
                          "Project marked Completed" if project.get("status") == "Completed" else "Project details/progress updated")

    if changed:
        log_activity(g.user["id"], "updated project", f"{project['title']}: {', '.join(changed)}")
        if "progress" in changed or "status" in changed:
            if project.get("status") == "Completed" and old_status != "Completed":
                _notify_project_team(project, "Project completed", f"Your project '{project['title']}' has been marked Completed by {g.user.get('fullName', 'your guide')}. The lecturer allocation slot is now available.", "Completed")
            elif project.get("status") == "Accepted":
                _notify_project_team(project, "Project progress updated", f"Progress for '{project['title']}' is now {project.get('progress', 0)}%.", f"Progress:{project.get('progress', 0)}")

    return ok({
        "project": _project_view(project),
        "message": "Project updated. Allocation slot released." if project.get("status") == "Completed" and old_status != "Completed" else ("Project updated." if changed else "No changes were needed."),
    })


# ---------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------

@projects_bp.post("/<project_id>/upload")
@require_auth
def upload_file(project_id):
    project = DB["projects"].get(project_id)
    if not project:
        return err("Project not found.", 404)
    if not _can_edit(g.user, project):
        return err("You are not allowed to upload files to this project.", 403)

    if "file" not in request.files:
        return err("No file part named 'file' was found in the request.")

    cfg = current_app.config
    valid, info = validate_upload(request.files["file"], cfg)
    if not valid:
        return err(info, 400)

    # One folder per project keeps files isolated.
    project_dir = os.path.join(cfg["UPLOAD_FOLDER"], project_id)
    os.makedirs(project_dir, exist_ok=True)

    # Prefix with a short unique token so re-uploads never overwrite
    # each other and the original name is still recognisable.
    safe_name = info["safeName"]
    stored_name = f"{new_id('f')[:10]}_{safe_name}"
    absolute_path = os.path.join(project_dir, stored_name)

    # Final safety check: the resolved path must stay inside project_dir.
    if not os.path.abspath(absolute_path).startswith(os.path.abspath(project_dir)):
        return err("Unsafe file path detected. Upload rejected.", 400)

    try:
        request.files["file"].save(absolute_path)
    except OSError as exc:
        current_app.logger.exception("Upload failed")
        return err(f"Could not save the file on the server: {exc}", 500)

    with _lock:
        project["fileName"] = info["originalName"]
        project["storedName"] = stored_name
        project["storedPath"] = absolute_path
        project["fileSize"] = info["size"]
        project["updatedAt"] = now_iso()

        # Uploading a file moves a Draft into Submitted.
        if project["status"] in ("Draft", "Changes Requested"):
            project["status"] = "Submitted"
            _push_history(project, g.user, "Submitted", "File uploaded for review")

    log_activity(g.user["id"], "uploaded file", f"{info['originalName']} ({info['size']} bytes)")

    # Notify every approved lecturer about every real submission.
    _notify_all_lecturers_new_submission(project)

    return ok({
        "project": project,
        "file": {
            "name": info["originalName"],
            "size": info["size"],
            "mime": info["mime"],
        },
        "message": "File uploaded successfully.",
    })


# ---------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------

@projects_bp.get("/<project_id>/download")
@require_auth
def download_file(project_id):
    project = DB["projects"].get(project_id)
    if not project:
        return err("Project not found.", 404)

    # Explicit authorisation check BEFORE touching the filesystem.
    if not _can_view(g.user, project):
        return err("You are not authorised to download this file.", 403)

    stored_path = project.get("storedPath")
    if not stored_path:
        return err("This project has no uploaded file.", 404)

    if not os.path.isfile(stored_path):
        return err(
            "The file record exists but the file is missing from the server "
            "(it may have been deleted or Flask was restarted on a different machine).",
            404,
        )

    log_activity(g.user["id"], "downloaded file", project.get("fileName", ""))

    return send_file(
        stored_path,
        as_attachment=True,
        download_name=project.get("fileName") or "project-file",
    )


# ---------------------------------------------------------------------
# Review workflow
# ---------------------------------------------------------------------

@projects_bp.post("/<project_id>/review")
@require_roles("lecturer", "admin")
def review_project(project_id):
    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "").strip()
    feedback = (data.get("feedback") or data.get("reason") or "").strip()
    if action not in REVIEW_ACTIONS:
        return err("Unknown review action. Use one of: " + ", ".join(REVIEW_ACTIONS.keys()))
    if action in ("reject", "request_changes") and not feedback:
        return err("A rejection reason is required." if action == "reject" else "Feedback is required when requesting changes.")
    if action == "accept" and g.user.get("role") != "lecturer":
        return err("Only a lecturer can accept a project and become its guide.", 403)

    with _lock:
        project = DB["projects"].get(project_id)
        if not project:
            return err("Project not found.", 404)
        if g.user["role"] == "lecturer":
            # Lecturer can review all submissions, but an already accepted
            # project is immutable to other lecturers.
            if project.get("status") == "Accepted" and project.get("guideId") != g.user["id"]:
                return err("This project has already been accepted by another lecturer.", 409)
            if project.get("status") == "Rejected":
                return err("This project has already been rejected.", 409)
        old_status = project.get("status", "Draft")
        if old_status in ("Accepted", "Rejected", "Completed"):
            return err(f"This project is already {old_status.lower()}.", 409)
        if g.user["role"] == "lecturer" and old_status not in ("Pending", "Submitted", "Under Review", "Changes Requested"):
            return err("Lecturers can review only pending project requests.", 409)

        if action == "accept" and g.user.get("role") == "lecturer":
            if old_status not in ("Pending", "Submitted", "Under Review", "Changes Requested"):
                return err("Only pending project requests can be accepted.", 409)
            assigned = sum(
                1 for item in DB["projects"].values()
                if item.get("guideId") == g.user["id"] and item.get("status") == "Accepted"
            )
            if assigned >= 4:
                return err("You already guide four projects. You cannot accept another project.", 409)

        new_status = REVIEW_ACTIONS[action]
        stamp = now_iso()
        if action == "accept":
            project["guideId"] = g.user["id"]
            project["guideName"] = g.user.get("fullName", "")
            project["acceptedBy"] = g.user["id"]
            project["acceptedByName"] = g.user.get("fullName", "")
            project["acceptedAt"] = stamp
            project["feedback"] = ""
        elif action == "reject":
            project["rejectedBy"] = g.user["id"]
            project["rejectedByName"] = g.user.get("fullName", "")
            project["rejectedAt"] = stamp
            project["rejectionReason"] = feedback
            project["feedback"] = feedback
        elif feedback:
            project["feedback"] = feedback

        project["status"] = new_status
        project["reviewedBy"] = g.user["id"]
        project["reviewedAt"] = stamp
        project["updatedAt"] = stamp
        _push_history(project, g.user, new_status, feedback or action)

    log_activity(g.user["id"], "reviewed project", f"{project['title']}: {old_status} -> {new_status}")
    if action == "accept":
        student_message = (
            f"Congratulations! Your project '{project['title']}' has been ACCEPTED by "
            f"{g.user.get('fullName')}. {g.user.get('fullName')} is assigned as your project guide. "
            "You can now view your guide details and track your project progress."
        )
        admin_message = f"Project '{project['title']}' has been accepted by {g.user.get('fullName')}."
        _notify_project_team(project, "Project accepted", student_message, new_status)
        _notify_admins("Project accepted", admin_message, project, new_status)
        add_notification(g.user["id"], "project", "Project acceptance recorded",
                         f"You accepted '{project['title']}' and are now its guide.",
                         link=f"projects.html?projectId={project_id}",
                         meta={"projectId": project_id, "status": new_status})
    elif action == "reject":
        student_message = (
            f"Your project '{project['title']}' has been REJECTED by {g.user.get('fullName')}. "
            "Please check the rejection reason and contact your lecturer for further guidance. "
            f"Reason: {feedback}"
        )
        admin_message = f"Project '{project['title']}' has been rejected by {g.user.get('fullName')}."
        _notify_project_team(project, "Project rejected", student_message, new_status)
        _notify_admins("Project rejected", admin_message, project, new_status)
        add_notification(g.user["id"], "project", "Project rejection recorded",
                         f"You rejected '{project['title']}'. Reason: {feedback}",
                         link=f"projects.html?projectId={project_id}",
                         meta={"projectId": project_id, "status": new_status})
    else:
        _notify_project_team(project, "Project status updated",
                             f"'{project['title']}' is now '{new_status}'. {feedback}",
                             new_status)
    return ok({"project": _project_view(project), "message": f"Project moved from '{old_status}' to '{new_status}'."})


# ---------------------------------------------------------------------
# Chart statistics
# ---------------------------------------------------------------------

@projects_bp.get("/stats/summary")
@require_auth
def stats_summary():
    """
    Aggregated numbers for the dashboards and Chart.js.

    Everything is computed from real stored records. When there are no
    records the client shows an empty state instead of fake numbers.
    """
    user = g.user
    projects = list(DB["projects"].values())

    if user["role"] == "student":
        projects = [p for p in projects if p.get("studentId") == user["id"]]
    elif user["role"] == "lecturer":
        projects = [
            p for p in projects
            if p.get("status") in ("Pending", "Submitted", "Under Review", "Changes Requested")
            or p.get("guideId") == user["id"]
            or p.get("rejectedBy") == user["id"]
        ]

    by_status = {s: 0 for s in VALID_STATUSES}
    for p in projects:
        by_status[p.get("status", "Draft")] = by_status.get(p.get("status", "Draft"), 0) + 1

    # Submissions grouped by month (ISO yyyy-mm).
    over_time = {}
    for p in projects:
        month = (p.get("createdAt") or "")[:7]
        if month:
            over_time[month] = over_time.get(month, 0) + 1

    progress_points = [
        {"title": p.get("title", "Untitled"), "progress": int(p.get("progress", 0))}
        for p in sorted(projects, key=lambda x: x.get("createdAt", ""), reverse=True)[:10]
    ]

    users_by_role = {"student": 0, "lecturer": 0, "alumni": 0, "admin": 0}
    for u in DB["users"].values():
        users_by_role[u.get("role", "student")] = users_by_role.get(u.get("role", "student"), 0) + 1

    return ok({
        "totalProjects": len(projects),
        "byStatus": by_status,
        "overTime": [{"month": m, "count": c} for m, c in sorted(over_time.items())],
        "progress": progress_points,
        "usersByRole": users_by_role,
        "totalUsers": len(DB["users"]),
    })