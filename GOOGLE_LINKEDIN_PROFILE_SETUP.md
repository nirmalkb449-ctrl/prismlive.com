# PrismLive homepage profile menu and Google / LinkedIn sign-in

## What changed
- Removed the standalone Log out link from the homepage navbar.
- The signed-in user's circular profile avatar now sits inside the navbar.
- Sign out remains available inside the profile dropdown, preserving the existing POST /logout behavior.
- Logged-out visitors still see Log in and Join Prism Live.
- Existing email/password registration, login, password reset, database schema and session handling remain in place.
- Google and LinkedIn OAuth routes are added; social sign-in uses the same existing `users` table and Flask session.

## Files changed
1. `templates/prismlive_home_page.html`
2. `templates/prismlive_login_page.html`
3. `static/css/profile-menu.css`
4. `app.py`
5. `requirements.txt`
6. `.env.example`

No other application templates, database schema files, or existing password authentication routes were changed.

## Configure Google OAuth
1. Open https://console.cloud.google.com/.
2. Create/select a Google Cloud project.
3. Configure the OAuth consent screen (External if appropriate for your audience).
4. Create an OAuth Client ID of type Web application.
5. Add these Authorized redirect URIs:
   - Local: `http://127.0.0.1:10000/auth/google/callback`
   - Render: `https://YOUR-RENDER-DOMAIN/auth/google/callback`
6. Copy the client ID and client secret into `.env` locally and Render Environment.

## Configure LinkedIn OAuth
1. Open https://www.linkedin.com/developers/apps.
2. Create/select your LinkedIn app.
3. Request/enable the **Sign In with LinkedIn using OpenID Connect** product.
4. Add the same redirect URLs in the app's OAuth 2.0 settings:
   - Local: `http://127.0.0.1:10000/auth/linkedin/callback`
   - Render: `https://YOUR-RENDER-DOMAIN/auth/linkedin/callback`
5. Copy the client ID and client secret into `.env` locally and Render Environment.
6. Ensure the app has the `openid`, `profile`, and `email` scopes available/approved.

## Local environment
Add these values to `.env` (never commit `.env`):
```env
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
LINKEDIN_CLIENT_ID=your-linkedin-client-id
LINKEDIN_CLIENT_SECRET=your-linkedin-client-secret
```

Then run:
```bash
pip install -r requirements.txt
python db.py
python app.py
```

Open `http://127.0.0.1:10000/login` and test each provider.

## Render deployment
In Render Dashboard → your PrismLive service → Environment, add the four variables above. Use the exact public Render domain in each provider's redirect URI, then redeploy.

## Account matching and safety
- Social sign-in requires a provider-returned verified email.
- If that email already belongs to an active PrismLive account, the existing account is signed in; no duplicate user is created.
- Otherwise a new user row is created in the existing SQLite users table.
- A random, unknown password hash is stored for a social-only account; the existing forgot-password process can be used to set a normal password later.
- No client secrets are stored in HTML or JavaScript.
- OAuth buttons will return a configuration message until provider credentials and callback URLs are set.
