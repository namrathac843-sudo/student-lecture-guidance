import pytest

from backend import create_app
from backend.utils import DB, _lock, hash_password, now_iso


@pytest.fixture()
def setup(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATA_FILE": str(tmp_path / "db.json"),
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "TOKEN_TTL_SECONDS": 3600,
    })
    client = app.test_client()
    with app.app_context():
        stamp = now_iso()
        users = [
            ("admin", "admin@test.edu", "admin", "Administration", None),
            ("lecturer-1", "l1@test.edu", "lecturer", "Engineering", None),
            ("lecturer-2", "l2@test.edu", "lecturer", "Other", None),
            ("student-1", "s1@test.edu", "student", "Engineering", "1GA21CS001"),
            ("student-2", "s2@test.edu", "student", "Engineering", "1GA21CS002"),
        ]
        with _lock:
            for uid, email, role, dept, usn in users:
                DB["users"][uid] = {
                    "id": uid, "fullName": uid.title(), "email": email,
                    "role": role, "department": dept, "usn": usn,
                    "passwordHash": hash_password("SecurePass123"),
                    "status": "Active", "approvalStatus": "Approved",
                    "createdAt": stamp, "updatedAt": stamp,
                }
    def token(email):
        r = client.post("/api/auth/login", json={"email": email, "password": "SecurePass123"})
        assert r.status_code == 200, r.get_json()
        return r.get_json()["data"]["token"]
    tokens = {email: token(email) for _, email, *_ in users}
    with app.app_context():
        DB["projects"]["p1"] = {
            "id": "p1", "title": "Smart Farm", "description": "Test project",
            "department": "Engineering", "studentId": "student-1",
            "teamMembers": ["1GA21CS002"], "status": "Submitted",
            "guideId": "", "progress": 0, "createdAt": stamp,
            "updatedAt": stamp, "history": [],
        }
    return app, client, tokens


def h(token):
    return {"Authorization": f"Bearer {token}"}


def test_all_lecturers_see_all_submitted_projects(setup):
    app, client, tokens = setup
    response = client.get("/api/projects", headers=h(tokens["l2@test.edu"]))
    assert response.status_code == 200
    assert [p["id"] for p in response.get_json()["data"]["projects"]] == ["p1"]


def test_accept_notifies_every_team_member_and_admin_and_assigns_guide(setup):
    app, client, tokens = setup
    response = client.post("/api/projects/p1/review", json={"action": "accept"},
                           headers=h(tokens["l1@test.edu"]))
    assert response.status_code == 200, response.get_json()
    project = response.get_json()["data"]["project"]
    assert project["status"] == "Accepted"
    assert project["guideId"] == "lecturer-1"
    assert project["acceptedBy"] == "lecturer-1"
    assert project["acceptedAt"]
    with app.app_context():
        notices = list(DB["notifications"].values())
        recipients = {n["userId"] for n in notices}
        assert {"student-1", "student-2", "admin"}.issubset(recipients)
    # A second lecturer cannot accept it.
    denied = client.post("/api/projects/p1/review", json={"action": "accept"},
                         headers=h(tokens["l2@test.edu"]))
    assert denied.status_code == 409


def test_rejection_saves_reason_and_notifies_team_and_admin(setup):
    app, client, tokens = setup
    response = client.post("/api/projects/p1/review",
                           json={"action": "reject", "feedback": "Please improve the scope."},
                           headers=h(tokens["l2@test.edu"]))
    assert response.status_code == 200
    project = response.get_json()["data"]["project"]
    assert project["status"] == "Rejected"
    assert project["rejectionReason"] == "Please improve the scope."
    assert project["rejectedBy"] == "lecturer-2"
    assert project["rejectedAt"]
    with app.app_context():
        notices = list(DB["notifications"].values())
        assert {"student-1", "student-2", "admin", "lecturer-2"}.issubset(
            {n["userId"] for n in notices}
        )


def test_lecturer_capacity_is_four_and_does_not_hide_projects(setup):
    app, client, tokens = setup
    with app.app_context():
        for i in range(4):
            DB["projects"][f"accepted-{i}"] = {
                "id": f"accepted-{i}", "title": f"Accepted {i}",
                "studentId": "student-1", "guideId": "lecturer-1",
                "status": "Accepted", "department": "Engineering",
                "createdAt": now_iso(), "history": [],
            }
    response = client.get("/api/projects", headers=h(tokens["l1@test.edu"]))
    data = response.get_json()["data"]
    assert len(data["projects"]) == 5
    assert data["acceptanceCapacity"] == {"limit": 4, "accepted": 4, "remaining": 0}
    denied = client.post("/api/projects/p1/review", json={"action": "accept"},
                         headers=h(tokens["l1@test.edu"]))
    assert denied.status_code == 409


def test_reject_remains_available_when_lecturer_has_reached_acceptance_limit(setup):
    app, client, tokens = setup
    with app.app_context():
        for i in range(4):
            DB["projects"][f"accepted-{i}"] = {
                "id": f"accepted-{i}", "title": f"Accepted {i}",
                "studentId": "student-1", "guideId": "lecturer-1",
                "status": "Accepted", "department": "Engineering",
                "createdAt": now_iso(), "history": [],
            }
    response = client.post(
        "/api/projects/p1/review",
        json={"action": "reject", "feedback": "Please revise the proposal."},
        headers=h(tokens["l1@test.edu"]),
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["project"]["status"] == "Rejected"


def test_rejected_project_cannot_be_accepted_by_another_lecturer(setup):
    app, client, tokens = setup
    rejected = client.post(
        "/api/projects/p1/review",
        json={"action": "reject", "feedback": "Needs more detail."},
        headers=h(tokens["l1@test.edu"]),
    )
    assert rejected.status_code == 200
    accepted = client.post(
        "/api/projects/p1/review", json={"action": "accept"},
        headers=h(tokens["l2@test.edu"]),
    )
    assert accepted.status_code == 409


def test_student_submission_creates_pending_request_and_notifies_lecturers(setup):
    app, client, tokens = setup
    response = client.post(
        "/api/projects",
        json={"title": "Village Safety IoT", "description": "Detect and alert", "technologies": "ESP32, Python"},
        headers=h(tokens["s1@test.edu"]),
    )
    assert response.status_code == 201, response.get_json()
    project = response.get_json()["data"]["project"]
    assert project["status"] == "Pending"
    assert project["technologies"] == "ESP32, Python"
    assert project["studentId"] == "student-1"
    lecturer_view = client.get("/api/projects", headers=h(tokens["l2@test.edu"]))
    assert any(p["id"] == project["id"] and p["status"] == "Pending" for p in lecturer_view.get_json()["data"]["projects"])
    with app.app_context():
        notices = [n for n in DB["notifications"].values() if n.get("meta", {}).get("projectId") == project["id"]]
        assert {n["userId"] for n in notices} >= {"lecturer-1", "lecturer-2", "admin"}


def test_duplicate_active_student_request_is_rejected(setup):
    app, client, tokens = setup
    payload = {"title": "Duplicate title", "description": "First"}
    first = client.post("/api/projects", json=payload, headers=h(tokens["s1@test.edu"]))
    second = client.post("/api/projects", json=payload, headers=h(tokens["s1@test.edu"]))
    assert first.status_code == 201
    assert second.status_code == 409


def test_completed_project_releases_slot_and_stays_in_history(setup):
    app, client, tokens = setup
    with app.app_context():
        for i in range(5):
            DB["projects"][f"req-{i}"] = {
                "id": f"req-{i}", "title": f"Request {i}", "description": "x",
                "studentId": "student-1", "guideId": "", "status": "Pending",
                "progress": 0, "createdAt": now_iso(), "history": [],
            }
    for i in range(4):
        accepted = client.post(f"/api/projects/req-{i}/review", json={"action": "accept"}, headers=h(tokens["l1@test.edu"]))
        assert accepted.status_code == 200, accepted.get_json()
    cap = client.get("/api/projects", headers=h(tokens["l1@test.edu"])).get_json()["data"]["acceptanceCapacity"]
    assert cap == {"limit": 4, "accepted": 4, "remaining": 0}
    completed = client.put("/api/projects/req-0", json={"status": "Completed", "progress": 100}, headers=h(tokens["l1@test.edu"]))
    assert completed.status_code == 200, completed.get_json()
    assert completed.get_json()["data"]["project"]["status"] == "Completed"
    cap = client.get("/api/projects", headers=h(tokens["l1@test.edu"])).get_json()["data"]["acceptanceCapacity"]
    assert cap == {"limit": 4, "accepted": 3, "remaining": 1}
    next_accept = client.post("/api/projects/req-4/review", json={"action": "accept"}, headers=h(tokens["l1@test.edu"]))
    assert next_accept.status_code == 200, next_accept.get_json()
    with app.app_context():
        assert DB["projects"]["req-0"]["status"] == "Completed"
        assert DB["projects"]["req-0"]["completedAt"]


def test_only_assigned_lecturer_can_update_progress_or_complete(setup):
    app, client, tokens = setup
    accepted = client.post("/api/projects/p1/review", json={"action": "accept"}, headers=h(tokens["l1@test.edu"]))
    assert accepted.status_code == 200
    denied = client.put("/api/projects/p1", json={"progress": 50}, headers=h(tokens["l2@test.edu"]))
    assert denied.status_code == 403
    allowed = client.put("/api/projects/p1", json={"progress": 50}, headers=h(tokens["l1@test.edu"]))
    assert allowed.status_code == 200


def test_simultaneous_acceptance_of_same_request_has_one_winner(setup):
    from concurrent.futures import ThreadPoolExecutor
    app, _client, tokens = setup
    def accept(email):
        with app.test_client() as local_client:
            return local_client.post("/api/projects/p1/review", json={"action": "accept"}, headers=h(tokens[email])).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(accept, ["l1@test.edu", "l2@test.edu"]))
    assert sorted(results) == [200, 409]


def test_four_completed_projects_allow_four_new_allocations(setup):
    app, client, tokens = setup
    with app.app_context():
        for i in range(8):
            DB["projects"][f"batch-{i}"] = {
                "id": f"batch-{i}", "title": f"Batch {i}", "description": "x",
                "studentId": "student-1", "guideId": "", "status": "Pending",
                "progress": 0, "createdAt": now_iso(), "history": [],
            }
    for i in range(4):
        assert client.post(f"/api/projects/batch-{i}/review", json={"action": "accept"}, headers=h(tokens["l1@test.edu"])).status_code == 200
    for i in range(4):
        assert client.put(f"/api/projects/batch-{i}", json={"status": "Completed"}, headers=h(tokens["l1@test.edu"])).status_code == 200
    for i in range(4, 8):
        response = client.post(f"/api/projects/batch-{i}/review", json={"action": "accept"}, headers=h(tokens["l1@test.edu"]))
        assert response.status_code == 200, response.get_json()
    capacity = client.get("/api/projects", headers=h(tokens["l1@test.edu"])).get_json()["data"]["acceptanceCapacity"]
    assert capacity == {"limit": 4, "accepted": 4, "remaining": 0}


def test_simultaneous_different_requests_cannot_exceed_four_active_slots(setup):
    from concurrent.futures import ThreadPoolExecutor
    app, client, tokens = setup
    with app.app_context():
        for i in range(3):
            DB["projects"][f"already-{i}"] = {
                "id": f"already-{i}", "title": f"Already {i}", "studentId": "student-1",
                "guideId": "lecturer-1", "status": "Accepted", "progress": 0,
                "createdAt": now_iso(), "history": [],
            }
        for i in range(2):
            DB["projects"][f"race-{i}"] = {
                "id": f"race-{i}", "title": f"Race {i}", "studentId": "student-2",
                "guideId": "", "status": "Pending", "progress": 0,
                "createdAt": now_iso(), "history": [],
            }
    def accept(project_id):
        with app.test_client() as local_client:
            return local_client.post(f"/api/projects/{project_id}/review", json={"action": "accept"}, headers=h(tokens["l1@test.edu"])).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(accept, ["race-0", "race-1"]))
    assert sorted(results) == [200, 409]
    capacity = client.get("/api/projects", headers=h(tokens["l1@test.edu"])).get_json()["data"]["acceptanceCapacity"]
    assert capacity == {"limit": 4, "accepted": 4, "remaining": 0}


def test_student_cannot_submit_past_deadline(setup):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    _, client, tokens = setup
    yesterday = (datetime.now(ZoneInfo("Asia/Kolkata")).date() - timedelta(days=1)).isoformat()
    response = client.post(
        "/api/projects",
        json={"title": "Past deadline test", "description": "Should be rejected", "deadline": yesterday},
        headers=h(tokens["s1@test.edu"]),
    )
    assert response.status_code == 400
    assert "Past deadlines are not allowed" in response.get_json()["error"]


def test_student_can_submit_today_or_future_deadline(setup):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    _, client, tokens = setup
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    for offset, title in [(0, "Today deadline test"), (3, "Future deadline test")]:
        deadline = (today + timedelta(days=offset)).isoformat()
        response = client.post(
            "/api/projects",
            json={"title": title, "description": "Valid deadline", "deadline": deadline},
            headers=h(tokens["s1@test.edu"]),
        )
        assert response.status_code == 201, response.get_json()
        assert response.get_json()["data"]["project"]["deadline"] == deadline
