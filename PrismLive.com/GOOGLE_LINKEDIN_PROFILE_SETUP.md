# PrismLive profile menu and Google / LinkedIn sign-in setup

## What changed in this update

- Removed the separate **Log out** navigation item from the homepage.
- Moved the existing profile icon/dropdown into the homepage navbar on the right.
- Kept the existing profile dropdown actions and its existing Sign Out POST form.
- Connected the Google and LinkedIn buttons on the login page to Flask OAuth routes.
- Added an `oauth_accounts` table to map provider identities to PrismLive users.
- Preserved the existing email/password login, registration, logout, password reset, and existing user records.
- Social sign-in creates a local PrismLive account for a new, provider-verified email.
- For security, it does not automatically merge a social identity into an existing email/password account. A user with an existing PrismLive account must continue using that account's existing login method.

Only the following files were changed for this feature:
- `templates/prismlive_home_page.html`
- `templates/prismlive_login_page.html`
- `app.py`
- `db.py`
- `requirements.txt`
- `.env.example`

All other project source files are included unchanged.

## 1. Install dependencies

From the project folder:

```powershell
py -m venv venv
.\\venv\\Scripts\\activate
pip install -r requirements.txt
```

## 2. Configure Google OAuth

1. Open https://console.cloud.google.com/.
2. Create or select a Google Cloud project.
3. Configure the OAuth consent screen / Google Auth Platform.
4. Create an OAuth Client ID with application type **Web application**.
5. Add these Authorized redirect URIs (must match exactly):

Local:
```text
http://127.0.0.1:10000/auth/google/callback
```

Render:
```text
https://YOUR-RENDER-SERVICE.onrender.com/auth/google/callback
```

6. Copy the Client ID and Client Secret into `.env`.

```env
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
```

Google's official OpenID Connect guide:
https://developers.google.com/identity/openid-connect/openid-connect

## 3. Configure LinkedIn OAuth

1. Open https://www.linkedin.com/developers/.
2. Create or select your LinkedIn application.
3. Add your website details and app logo as required.
4. Enable the **Sign In with LinkedIn using OpenID Connect** product and request the `openid`, `profile`, and `email` scopes.
5. Add these Authorized redirect URLs (must match exactly):

Local:
```text
http://127.0.0.1:10000/auth/linkedin/callback
```

Render:
```text
https://YOUR-RENDER-SERVICE.onrender.com/auth/linkedin/callback
```

6. Copy the Client ID and Client Secret into `.env`.

```env
LINKEDIN_CLIENT_ID=your-linkedin-client-id
LINKEDIN_CLIENT_SECRET=your-linkedin-client-secret
```

LinkedIn's official OIDC guide:
https://learn.microsoft.com/linkedin/consumer/integrations/self-serve/sign-in-with-linkedin-v2

## 4. Configure local environment

Copy `.env.example` to `.env`, then set a long random `SECRET_KEY` and the provider credentials. Never commit `.env` or expose client secrets in frontend HTML or JavaScript.

For local development:
```env
COOKIE_SECURE=0
```

For Render:
- Set `COOKIE_SECURE=1`.
- Add `SECRET_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `LINKEDIN_CLIENT_ID`, and `LINKEDIN_CLIENT_SECRET` under Render Dashboard → Environment.
- Use the exact HTTPS callback URLs from your Render service.

## 5. Run and test

```powershell
python db.py
python app.py
```

Open:
- Homepage: http://127.0.0.1:10000/home
- Login: http://127.0.0.1:10000/login

Test:
1. Sign in with the existing email/password method.
2. Confirm the separate Logout navbar link is gone.
3. Click the profile icon and test the dropdown and Sign Out.
4. Sign out and test Google.
5. Sign out and test LinkedIn.
6. Confirm a new social user is added to the local users table and provider mapping is added to `oauth_accounts`.

## Notes

- The provider buttons will redirect back to login with a setup message until valid credentials are configured.
- OAuth cannot be fully tested until you create real Google and LinkedIn developer credentials and register the exact callback URLs.
- The social identity is not automatically merged into an existing account. This avoids silently linking accounts based only on matching email addresses.
- The existing SQLite database is not included in the ZIP so that private account data is not distributed. `init_db()` creates the new OAuth mapping table safely while preserving existing users when you run the updated application.
