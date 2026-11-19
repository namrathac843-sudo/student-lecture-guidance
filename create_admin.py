"""Create the first administrator account without a public registration route."""

import argparse
import getpass
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from backend import create_app  # noqa: E402
from backend.utils import DB, _lock, hash_password, is_valid_email_format, new_id, now_iso, save_db_state


def main():
    parser = argparse.ArgumentParser(description="Create a Project Guidance platform administrator.")
    parser.add_argument("--email", help="Administrator email address")
    parser.add_argument("--name", default="Platform Administrator", help="Administrator display name")
    args = parser.parse_args()

    email = (args.email or input("Administrator email: ")).strip().lower()
    if not is_valid_email_format(email):
        print("A valid email address is required.", file=sys.stderr)
        return 2

    password = getpass.getpass("New administrator password (minimum 8 characters, letters and numbers): ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        return 2
    if len(password) < 8 or not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        print("Password must contain at least 8 characters, one letter and one number.", file=sys.stderr)
        return 2

    app = create_app()
    with app.app_context():
        with _lock:
            if any((user.get("email") or "").lower() == email for user in DB["users"].values()):
                print("An account with that email already exists.", file=sys.stderr)
                return 2
            stamp = now_iso()
            user = {
                "id": new_id("user"),
                "fullName": args.name.strip() or "Platform Administrator",
                "email": email,
                "collegeName": "",
                "department": "Administration",
                "usn": "",
                "role": "admin",
                "passwordHash": hash_password(password),
                "status": "Active",
                "approvalStatus": "Approved",
                "approvedBy": "system-bootstrap",
                "approvedAt": stamp,
                "avatar": "",
                "bio": "",
                "phone": "",
                "createdAt": stamp,
                "updatedAt": stamp,
            }
            DB["users"][user["id"]] = user
        save_db_state(app.config["DATA_FILE"])
    print(f"Administrator created: {email}")
    print("Open /admin-login.html to sign in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
