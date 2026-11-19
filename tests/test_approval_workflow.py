import pytest

from backend import create_app
from backend.utils import DB, _lock, hash_password, new_id, now_iso


@pytest.fixture()
def app(tmp_path):
    database = tmp_path / "pgmp-test.json"
    uploads = tmp_path / "uploads"
    app = create_app({
        "TESTING": True,
        "DATA_FILE": str(database),
        "UPLOAD_FOLDER": str(uploads),
        "REQUIRE_STUDENT_USN_EMAIL": True,
        "COLLEGE_EMAIL_DOMAIN": "gat.ac.in",
        "USN_PATTERN": r"^[0-9]{1}[A-Z]{2}[0-9]{2}[A-Z]{2}[0-9]{3}$",
        "MAX_CONTENT_LENGTH": 10 * 1024 * 1024,
        "TOKEN_TTL_SECONDS": 3600,
    })
    with app.app_context():
        stamp = now_iso()
        admin = {
            "id": "test-admin",
            "fullName": "Test Admin",
            "email": "admin@gat.ac.in",
            "collegeName": "Global Academy of Technology",
            "department": "Administration",
            "role": "admin",
            "passwordHash": hash_password("AdminPass123"),
            "status": "Active",
            "approvalStatus": "Approved",
            "createdAt": stamp,
            "updatedAt": stamp,
        }
        with _lock:
            DB["users"][admin["id"]] = admin
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, role, email, **extra):
    payload = {
        "fullName": f"Test {role.title()}",
        "email": email,
        "collegeName": "Global Academy of Technology",
        "department": "Computer Science and Engineering",
        "role": role,
        "password": "SecurePass123",
        "confirmPassword": "SecurePass123",
    }
    payload.update(extra)
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["user"]


def login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_unapproved_user_cannot_login_and_approval_unlocks_access(client):
    student = register(client, "student", "1ga21cs001@gat.ac.in", usn="1GA21CS001")
    assert student["approvalStatus"] == "Pending"
    assert login(client, student["email"], "SecurePass123").status_code == 403

    admin_login = login(client, "admin@gat.ac.in", "AdminPass123")
    assert admin_login.status_code == 200
    admin_token = admin_login.get_json()["data"]["token"]

    approve = client.post(
        f"/api/approvals/{student['id']}",
        json={"decision": "Approved"},
        headers=auth(admin_token),
    )
    assert approve.status_code == 200
    assert approve.get_json()["data"]["user"]["approvalStatus"] == "Approved"

    student_login = login(client, student["email"], "SecurePass123")
    assert student_login.status_code == 200
    student_token = student_login.get_json()["data"]["token"]
    assert client.get("/api/auth/me", headers=auth(student_token)).status_code == 200


def test_lecturer_can_approve_students_only(client):
    lecturer = register(client, "lecturer", "lecturer@gat.ac.in")
    second_lecturer = register(client, "lecturer", "lecturer2@gat.ac.in")
    student = register(client, "student", "1ga21cs002@gat.ac.in", usn="1GA21CS002")

    assert login(client, lecturer["email"], "SecurePass123").status_code == 403
    admin_token = login(client, "admin@gat.ac.in", "AdminPass123").get_json()["data"]["token"]
    assert client.post(
        f"/api/approvals/{lecturer['id']}", json={"decision": "Approved"}, headers=auth(admin_token)
    ).status_code == 200

    lecturer_token = login(client, lecturer["email"], "SecurePass123").get_json()["data"]["token"]
    queue = client.get("/api/approvals/pending", headers=auth(lecturer_token))
    assert queue.status_code == 200
    assert [item["role"] for item in queue.get_json()["data"]["users"]] == ["student"]

    forbidden = client.post(
        f"/api/approvals/{second_lecturer['id']}",
        json={"decision": "Approved"},
        headers=auth(lecturer_token),
    )
    assert forbidden.status_code == 403

    approved = client.post(
        f"/api/approvals/{student['id']}",
        json={"decision": "Approved"},
        headers=auth(lecturer_token),
    )
    assert approved.status_code == 200


def test_approval_state_persists_after_app_restart(app, client):
    student = register(client, "student", "1ga21cs003@gat.ac.in", usn="1GA21CS003")
    admin_token = login(client, "admin@gat.ac.in", "AdminPass123").get_json()["data"]["token"]
    response = client.post(
        f"/api/approvals/{student['id']}",
        json={"decision": "Approved"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200

    # Recreating the app from the same data path simulates a server restart.
    restarted = create_app({
        "TESTING": True,
        "DATA_FILE": app.config["DATA_FILE"],
        "UPLOAD_FOLDER": app.config["UPLOAD_FOLDER"],
        "REQUIRE_STUDENT_USN_EMAIL": True,
        "COLLEGE_EMAIL_DOMAIN": "gat.ac.in",
        "USN_PATTERN": app.config["USN_PATTERN"],
        "MAX_CONTENT_LENGTH": app.config["MAX_CONTENT_LENGTH"],
    })
    response = login(restarted.test_client(), student["email"], "SecurePass123")
    assert response.status_code == 200


def test_approval_endpoint_rejects_non_reviewer(client):
    student = register(client, "student", "1ga21cs004@gat.ac.in", usn="1GA21CS004")
    assert login(client, student["email"], "SecurePass123").status_code == 403
    response = client.get("/api/approvals/pending")
    assert response.status_code == 401


def test_public_registration_cannot_create_admin(client):
    response = client.post("/api/auth/register", json={
        "fullName": "Attacker",
        "email": "attacker@gat.ac.in",
        "collegeName": "Global Academy of Technology",
        "department": "Computer Science",
        "role": "admin",
        "password": "SecurePass123",
        "confirmPassword": "SecurePass123",
    })
    assert response.status_code == 400
    assert "Administrator accounts" in response.get_json()["error"]


def test_rejected_account_remains_blocked(client):
    student = register(client, "student", "1ga21cs005@gat.ac.in", usn="1GA21CS005")
    admin_token = login(client, "admin@gat.ac.in", "AdminPass123").get_json()["data"]["token"]
    response = client.post(
        f"/api/approvals/{student['id']}",
        json={"decision": "Rejected"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200
    blocked = login(client, student["email"], "SecurePass123")
    assert blocked.status_code == 403


def test_malformed_registration_payload_returns_client_error(client):
    response = client.post("/api/auth/register", json={
        "fullName": 123,
        "email": ["not", "an", "email"],
        "collegeName": "College",
        "department": "Department",
        "role": "student",
        "password": "SecurePass123",
        "confirmPassword": "SecurePass123",
    })
    assert response.status_code == 400


def test_lecturer_institutional_id_does_not_use_student_usn_pattern(client):
    response = client.post("/api/auth/register", json={
        "fullName": "Faculty Member",
        "email": "faculty@gat.ac.in",
        "collegeName": "Global Academy of Technology",
        "department": "Computer Science and Engineering",
        "role": "lecturer",
        "usn": "FAC-2026-17",
        "password": "SecurePass123",
        "confirmPassword": "SecurePass123",
    })
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["data"]["user"]["usn"] == "FAC-2026-17"
