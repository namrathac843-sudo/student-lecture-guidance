# Project Guidance & Mentoring Platform

A Flask-backed project guidance and mentoring platform with a server-enforced account approval workflow.

## 1. Requirements

- Python 3.10 or newer
- pip
- A modern browser

## 2. Install and configure

```bash
python -m venv .venv
```

Activate the environment:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# Linux / macOS
source .venv/bin/activate
```

Install dependencies and create your environment file:

```bash
pip install -r requirements.txt
copy .env.example .env      # Windows
# cp .env.example .env      # Linux / macOS
```

Edit `.env` and set `SECRET_KEY` to a unique random value. Set `COLLEGE_EMAIL_DOMAIN` and `USN_PATTERN` to match your institution. The default student email format is `<USN>@gat.ac.in`.

The application database defaults to `instance/pgmp-data.json`. For a shared database across computers, configure Firebase Firestore below. The local JSON file remains a backup/local fallback. Do not commit `.env`, the `instance/` directory, or your Firebase service-account key.

## 3. Create the first administrator

Administrator accounts cannot be created through public registration. Create the first one from the server terminal:

```bash
python create_admin.py --email admin@gat.ac.in --name "Platform Administrator"
```

The command securely prompts for the password twice. There is no default administrator password in the source code.

Then start Flask:

```bash
python app.py
```

Open:

- Public site: `http://127.0.0.1:5000/`
- Regular sign in: `http://127.0.0.1:5000/login.html`
- Administrator sign in: `http://127.0.0.1:5000/admin-login.html`

## 4. Account approval rules

1. Students, lecturers and alumni register through the public registration page. Registration creates a `Pending` account; no bearer token is issued.
2. Pending users cannot sign in and cannot call protected APIs.
3. Administrators can approve or reject pending students, lecturers and alumni from the Admin Console.
4. Approved lecturers can approve or reject student accounts only. The server rejects any attempt by a lecturer to review a lecturer/alumni account.
5. Once approved, a user can sign in using the regular login page. Admins use the separate administrator login page.
6. Approval decisions, account records and activity are saved to the JSON database. Bearer sessions expire after 12 hours by default and are cleared on server restart.

## 5. Configure Firebase Spark (shared cloud database)

1. Open the [Firebase Console](https://console.firebase.google.com/) and create a Firebase project.
2. In **Build → Firestore Database**, create a database (choose a suitable region; use production mode).
3. Open **Project settings → Service accounts → Firebase Admin SDK → Generate new private key**. Save the downloaded JSON file in this project root as `serviceAccountKey.json`. Keep it private and never send it to GitHub or students.
4. Install dependencies again after adding the Firebase package:

   ```bash
   pip install -r requirements.txt
   ```

5. In `.env`, set:

   ```env
   FIREBASE_ENABLED=true
   FIREBASE_CREDENTIALS=serviceAccountKey.json
   ```

6. Start/restart the Flask application. On first connection, existing records from `instance/pgmp-data.json` are copied to Firestore if the matching cloud collections are empty. Firestore collections are named `pgmp_users`, `pgmp_projects`, `pgmp_sessions`, `pgmp_messages`, `pgmp_notifications`, and related collections.

Firebase's Spark plan has no-cost Firestore quotas, but it is not unlimited. Check the Firebase pricing page for current daily read/write/storage limits and region availability. This integration uses the Firebase Admin SDK on the Flask server; never put a service-account key in frontend JavaScript.

**Important for access from another computer:** Firebase stores the shared data in the cloud, but the Flask application must also be reachable by both computers. Deploy this Flask app to an HTTPS server/college server and configure the same Firebase project there. `http://127.0.0.1:5000` only works on the computer running Flask; it is not a public shared address.

## 6. Lecturer project capacity

When a lecturer accepts a project, the server records that lecturer as its guide. The server prevents a lecturer from accepting or being assigned a fifth non-rejected project. A lecturer's four assigned projects remain visible through the lecturer's project list. The check is performed on the backend, not only by hiding a frontend button.



## 6A. Server-backed project review and notifications

The project review API supports the lecturer workflow:

- `GET /api/projects` returns every project to lecturers, including accepted/rejected records, and includes the lecturer's accepted-project capacity (`acceptanceCapacity`).
- `POST /api/projects/<project_id>/review` accepts `{"action":"accept"}` or `{"action":"reject","feedback":"reason"}`.
- Acceptance is performed under the server lock, assigns the accepting lecturer as guide, records `acceptedBy`, `acceptedByName`, and `acceptedAt`, and rejects a fifth accepted project.
- Rejection requires a reason and records `rejectedBy`, `rejectedByName`, `rejectedAt`, and `rejectionReason`.
- A second lecturer cannot accept an already accepted project. Accepted/rejected decisions cannot be repeated.
- Every approved lecturer and every administrator receives a notification when a problem-statement upload submits a project. Acceptance/rejection notifications go to the project owner and team members that can be resolved to registered student IDs, USNs, email addresses, or names, as well as administrators and the acting lecturer.
- Notification records are stored in the server database and exposed through `/api/notifications`; the notification page and lecturer/admin project-review view use these APIs.
- Lecturer/admin project review tabs show All, Pending, and Accepted/Rejected projects. The lecturer capacity is displayed from server data.
- Every lecturer sees Accept and Reject controls on each pending project. When a lecturer has accepted four projects, only that lecturer’s Accept button is disabled; Reject remains available and the project stays visible.
- The first successful acceptance is committed under the server lock. It assigns that lecturer as the guide and closes the project to acceptance by every other lecturer; the frontend refreshes after each decision and the backend remains authoritative if two lecturers click at nearly the same time.

Team members should be stored as registered student IDs, USNs, email addresses, or exact registered full names to ensure individual notification delivery. Free-text team names that do not match a registered account cannot receive an individual account notification.

## 7. Change institution rules

Relevant `.env` options:

- `COLLEGE_EMAIL_DOMAIN=gat.ac.in`
- `USN_PATTERN=^[0-9]{1}[A-Z]{2}[0-9]{2}[A-Z]{2}[0-9]{3}$`
- `REQUIRE_STUDENT_USN_EMAIL=true`
- `STRICT_EMAIL_FOR_ALL_ROLES=false`
- `MAX_UPLOAD_MB=10`
- `DATA_FILE=instance/pgmp-data.json`
- `TOKEN_TTL_SECONDS=43200`

Restart Flask after changing `.env` values.

## 8. Security and deployment notes

- Authentication and approval decisions are checked on the Flask server. Editing Local Storage cannot create an authenticated server session or approve an account.
- Passwords are stored as salted Werkzeug PBKDF2 hashes. The JSON database contains sensitive account information and must not be publicly served.
- Reset-password emails are not configured. The application does not disclose reset tokens; signed-in users can change their password in Settings. A user who forgot a password must contact the administrator until an email provider is configured.
- Use HTTPS, a strong `SECRET_KEY`, a production WSGI server, private database backups, and an appropriate database service before deploying publicly.
- The existing browser UI still has some demonstration modules that read/write browser Local Storage (notably parts of project/session/message screens). The Firestore backend stores shared server API records, user accounts and project records accessed through the API; however, the remaining browser-local demo screens must be migrated to the API before every screen and every old browser-local record is guaranteed to sync across computers. Uploaded project files are currently stored on the Flask server's local disk, not in Firestore; use a cloud file-storage service if files must also follow users across server instances.

## 9. Run checks

```bash
python -m pytest -q
```

The tests cover registration approval, login blocking, role-specific decisions, authenticated access and data persistence.


## Student project requests and lecturer allocation

The existing **My Projects** page now submits student requests to the Flask API and stores them in the application's existing server-side data store. Student requests start with status `Pending`; the request includes the student ID, title, description, technologies, team details, submission timestamp, and optional uploaded document. Approved lecturers receive a database-backed notification and see the request in the existing **Project Requests & Allocation** area on the same page.

Lecturers can accept or reject pending requests. Acceptance assigns `guideId` / `acceptedBy` to the winning lecturer and is guarded by the backend lock. The active allocation count is calculated from records whose `guideId` matches the lecturer and whose status is exactly `Accepted`; pending, rejected, and completed projects do not consume a slot. The maximum is four. Only the assigned lecturer can update progress or complete an active allocated project. Marking a project `Completed` sets progress to 100, records `completedAt`, notifies the student team, and releases the slot. Completed records remain in the separate **Completed / History** view.

The existing notification centre reads the same server-side notification records and refreshes periodically. Project and notification views poll the API for live changes without a manual page reload.

### Stored project fields added/used

- `technologies`, `submittedAt`, `guideId`, `acceptedBy`, `acceptedByName`, `acceptedAt`
- `rejectedBy`, `rejectedByName`, `rejectedAt`, `rejectionReason`
- `progress`, `completedAt`, `status`, and `history`
- Uploaded document metadata and server path (`fileName`, `storedName`, `storedPath`, `fileSize`)

No SQL migration is required because this repository's existing persistence is JSON-backed (with optional Firestore synchronization). Existing records are read without migration; missing fields are treated as empty/default values.

**Concurrency deployment note:** acceptance and allocation checks are serialized by the application's existing process-wide re-entrant lock, so simultaneous threaded requests handled by one Flask process cannot exceed four active allocations or accept the same project twice. The JSON store is not a multi-process transactional database. If deploying multiple Flask worker processes/instances, move the allocation transaction to a transactional database before scaling out; do not run multiple independent JSON-backed workers for this workflow.


### Files updated for this workflow

- `backend/projects.py` — pending request creation, notifications, allocation guard, progress/completion transitions, authorization, and active capacity calculation.
- `backend/config.py` — Pending and Completed project statuses.
- `frontend/projects.html` — technologies field and status choices in the existing project form.
- `frontend/js/api.js` — server API methods for creating, updating, and uploading project requests.
- `frontend/js/projects.js` — server-backed student submission and live project-request list.
- `frontend/js/project-review.js` — existing lecturer project page enhanced with request, accepted, rejected, and completed sections.
- `frontend/js/dashboard.js` — live server-backed project stats and lecturer allocation counter on the existing dashboard.
- `frontend/js/notifications.js` — periodic refresh of the existing notification centre.
- `tests/test_project_review_workflow.py` — workflow, capacity-release, authorization, duplicate-request, and concurrent-acceptance tests.


## 10. Firebase Hosting + Render deployment

See [`DEPLOYMENT_FIREBASE_RENDER.md`](DEPLOYMENT_FIREBASE_RENDER.md) for the step-by-step deployment guide. The frontend API URL is configured in `frontend/js/deploy-config.js`.


## Project deadline rule

The student project deadline field now allows **today or a future date only**. Past dates are blocked in the browser and independently rejected by the Flask API. The server uses `APP_TIMEZONE` (default `Asia/Kolkata`) so UTC hosting does not incorrectly treat the Indian calendar date as yesterday or tomorrow.
