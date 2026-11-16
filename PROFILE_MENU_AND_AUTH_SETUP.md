# PrismLive profile menu and authentication update

## What changed
- Added a consistent signed-in profile control to the bottom of every full-page HTML template. It shows the user's initials, name and email.
- Added dropdown links for My Profile, Account Settings, My Projects, Activity History, Admin Dashboard (admins only), and Sign Out.
- Added responsive styling and accessible menu behavior (click outside / Escape to close).
- Added `/profile`, `/account-settings`, `/my-projects`, and `/activity-history` routes. Account settings currently let a user update their first and last name.
- Added a `user_activity` SQLite table and records for sign-in, sign-out, and account profile updates. The table is created automatically by `init_db()`; existing user data is retained.
- Kept the existing registration, login, logout, protected chat/payment, and email password-reset workflows.
- Fixed a pre-existing Python syntax issue in the AI chat history builder (`app.py`) that prevented the app from starting.

## Run locally (Windows / VS Code)
1. Extract this ZIP.
2. Open the `PrismLive.com` folder in VS Code.
3. Open Terminal and run:
   ```powershell
   py -m venv venv
   .\venv\Scripts\activate
   pip install -r requirements.txt
   copy .env.example .env
   ```
4. Open `.env` and set a long random `SECRET_KEY`. For local development set `COOKIE_SECURE=0`.
5. Start the app:
   ```powershell
   python app.py
   ```
   If the project is configured to run via Flask-SocketIO, use its existing startup instructions in `README.md`.
6. Open `http://127.0.0.1:10000`.

## Create the owner / Super Admin
Run:
```powershell
python create_superadmin.py
```
Enter the owner's name, email, and a strong password (at least 12 characters). Then sign in at `/login`.

## Configure password-reset email (Gmail)
In `.env`, configure:
```env
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=1
MAIL_USE_SSL=0
MAIL_USERNAME=your-gmail-address
MAIL_PASSWORD=your-16-character-google-app-password
MAIL_DEFAULT_SENDER=Prism Live <your-gmail-address>
```
Use a Google App Password, not your normal Gmail password. Restart the local app after editing `.env`.

## Deploy to Render
- Keep the existing `render.yaml`, `Procfile`, and requirements.
- Push the updated project files to the connected GitHub repository.
- In Render, confirm `SECRET_KEY` is set and `COOKIE_SECURE=1`.
- Add the `MAIL_*` environment variables in Render Dashboard → Environment to enable password reset emails.
- Keep the persistent disk mounted at `/var/data` and `DATABASE_PATH=/var/data/prismlive.db`.
- Deploy and verify `/healthz`, `/login`, `/about`, and `/home`.

## Notes
- The profile dropdown appears only after a successful login.
- Public informational pages remain public. Chat and payment retain their existing login protection.
- My Projects is currently a connected account landing page; project-specific records are not added because the supplied database has no project ownership/submission table.
- Existing password reset email sending still requires valid SMTP configuration.
- A full runtime HTTP test could not be performed in this build environment because Flask is not installed here. Python syntax and Jinja template parsing were checked successfully.
