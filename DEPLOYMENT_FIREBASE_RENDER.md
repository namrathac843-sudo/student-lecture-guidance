# Deployment: Firebase Hosting (frontend) + Render (Flask backend)

This project uses **Firebase Hosting for the static frontend** and **Render for the Flask API**.
The Firebase Spark plan has free Hosting quotas; Render has a free web-service option with
important sleep and storage limits. This is suitable for a college demo, not guaranteed
always-on production hosting.

## A. Prepare the project

1. Extract this ZIP.
2. Create a GitHub repository and push the complete project folder.
3. Do not upload `.env`, `serviceAccountKey.json`, `instance/`, or real user data.
4. In `frontend/js/deploy-config.js`, you will later add the Render backend URL.

## B. Deploy the backend to Render

1. Sign in at https://render.com and choose **New → Web Service**.
2. Connect the GitHub repository containing this project.
3. Set **Root Directory** to the project root (the folder containing `app.py`).
4. Set **Build Command** to `pip install -r requirements.txt`.
5. Set **Start Command** to `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`.
6. Choose the **Free** instance for a demo and create the service.
7. In the Render service's **Environment** settings, add:
   - `SECRET_KEY` = a long random secret (do not use the example value)
   - `APP_TIMEZONE` = `Asia/Kolkata`
   - `CORS_ALLOWED_ORIGINS` = your Firebase Hosting URL, for example `https://YOUR_PROJECT_ID.web.app,https://YOUR_PROJECT_ID.firebaseapp.com`
   - `COLLEGE_EMAIL_DOMAIN` = your institution's email domain
   - `USN_PATTERN` = the required USN regex for your institution
   - `REQUIRE_STUDENT_USN_EMAIL` = `true` (only if this matches your college's policy)
   - `FIREBASE_ENABLED` = `true` only after Firestore setup below
8. Deploy and copy the backend URL, e.g. `https://YOUR-SERVICE.onrender.com`.
9. Open `https://YOUR-SERVICE.onrender.com/api/health`. It should return JSON with `"status":"ok"`.

### Important: persistent data

Render's free web service has an **ephemeral filesystem** and can spin down after 15 minutes idle.
The JSON database and uploaded files on local disk can disappear after a restart/redeploy/spin-down.
Do not rely on the local JSON file for real student accounts or project submissions.

For this app's shared records, configure Firestore as described in section C. Uploaded project documents
still use the Render local uploads folder in this version, so uploads are not durable on the free
Render instance. Do not upload important documents until a durable file-storage integration is added.

## C. Configure Firestore for shared records (recommended)

1. Open https://console.firebase.google.com and create a Firebase project.
2. In **Build → Firestore Database**, create a database.
3. Open **Project settings → Service accounts → Firebase Admin SDK** and generate a private key.
4. In Render → your service → **Environment → Secret Files**, add a file named
   `serviceAccountKey.json` and paste the downloaded JSON contents.
5. Add Render environment variables:
   - `FIREBASE_ENABLED` = `true`
   - `FIREBASE_CREDENTIALS` = `/etc/secrets/serviceAccountKey.json`
6. Redeploy the backend.
7. Keep the service-account JSON private. Never place it in `frontend/`, GitHub, or this ZIP.

Firestore has free quotas on the Spark plan, not unlimited usage. If a quota is exceeded,
the affected service may stop serving requests until the next usage period unless billing is changed.

## D. Deploy the frontend to Firebase Hosting

1. Install Node.js LTS from https://nodejs.org.
2. Open a terminal in the extracted project root.
3. Install the Firebase CLI:
   ```bash
   npm install -g firebase-tools
   ```
4. Sign in:
   ```bash
   firebase login
   ```
5. In `frontend/js/deploy-config.js`, replace the empty value with your Render URL:
   ```javascript
   window.PGMP_API_BASE_URL = 'https://YOUR-SERVICE.onrender.com';
   ```
   Do not add a trailing slash.
6. In the project root, initialize Hosting if needed:
   ```bash
   firebase use --add
   ```
   Select the Firebase project you created. The included `firebase.json` already sets
   the Hosting public directory to `frontend`; if the CLI asks to overwrite it, keep the
   existing configuration.
7. Deploy:
   ```bash
   firebase deploy --only hosting
   ```
8. Open the `web.app` URL printed by Firebase.

## E. Final connection check

1. Open the Firebase-hosted site.
2. Register a test account and approve it through the admin/lecturer workflow.
3. Sign in and submit a project with deadline = today. It should be accepted.
4. Try yesterday's date. The browser should block it, and the backend also rejects it
   with HTTP 400 if someone bypasses the browser.
5. Try tomorrow's date. It should be accepted.
6. If the browser console reports CORS, confirm `CORS_ALLOWED_ORIGINS` exactly matches
   the Firebase site origin (no trailing slash), then redeploy Render.

## Free-tier notes

- Firebase Hosting Spark currently includes up to 10 GB stored Hosting content and
  up to 360 MB/day transfer, subject to Firebase's terms and quotas.
- Render free web services sleep after 15 minutes without inbound traffic; the first
  request after sleeping can take about a minute.
- Render free web services do not provide persistent disks. The Firestore integration
  protects supported database records, but this project's uploaded files remain local.
- Free quotas and product terms can change. Check the linked provider pricing/docs before
  a public launch.
