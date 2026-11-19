"""
Project Guidance and Mentoring Platform
=======================================
Flask entry point.

Run with:
    python app.py

The app serves:
  * the REST API under /api/...
  * the frontend (HTML/CSS/JS) from the ./frontend folder
"""

import os
from dotenv import load_dotenv

# Load variables from .env BEFORE importing the app factory,
# because Config reads os.environ at import time.
load_dotenv()

from backend import create_app  # noqa: E402  (import after load_dotenv on purpose)

app = create_app()

if __name__ == "__main__":
    # Debug mode is opt-in; never expose an interactive debugger by default.
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)