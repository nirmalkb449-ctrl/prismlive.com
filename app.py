
import os
import sqlite3
import hashlib
import secrets
import smtplib
import threading

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode, urlparse

from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, send_from_directory
)
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import generate_password_hash, check_password_hash

from db import get_db, init_db

load_dotenv()

# =========================================================
# APPLICATION CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

app = Flask(
    __name__,
    static_url_path="/static",
    static_folder="static"
)

secret_key = os.environ.get("SECRET_KEY")

if not secret_key:
    if os.environ.get("RENDER") == "true":
        raise RuntimeError(
            "SECRET_KEY must be configured in production."
        )
    secret_key = "local-development-only-change-this"

app.config.update(
    SECRET_KEY=secret_key,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        os.environ.get(
            "COOKIE_SECURE",
            "1" if os.environ.get("RENDER") == "true" else "0"
        ) == "1"
    ),
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    MAX_CONTENT_LENGTH=2 * 1024 * 1024
)

socketio = SocketIO(
    app,
    cors_allowed_origins=None,
    async_mode="threading",
    logger=False,
    engineio_logger=False
)

# Track individual Socket.IO connections.
online_users = {}
presence_lock = threading.Lock()


# =========================================================
# DATABASE HELPERS
# =========================================================

def initialize_database():
    """Initialize the existing database and subscription plans."""

    init_db()

    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS subscription_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price REAL NOT NULL CHECK(price > 0),
                duration TEXT NOT NULL,
                description TEXT,
                features TEXT,
                active INTEGER NOT NULL DEFAULT 1
            )
        """)

        count = db.execute(
            "SELECT COUNT(*) FROM subscription_plans"
        ).fetchone()[0]

        if count == 0:
            db.executemany("""
                INSERT INTO subscription_plans
                (name, price, duration, description, features, active)
                VALUES (?, ?, ?, ?, ?, 1)
            """, [
                (
                    "Basic",
                    472,
                    "year",
                    "Essential PrismLive membership.",
                    "Basic access\nStandard features\nUser profile\nIncluding all taxes"
                ),
                (
                    "Premium",
                    872,
                    "year",
                    "Enjoy the complete PrismLive experience.",
                    "All Basic features\nPremium access\nAdvanced features\nPriority support\n Including all taxes"
                )
            ])


# =========================================================
# USER AUTHENTICATION HELPERS
# =========================================================

def current_user():
    user_id = session.get("user_id")

    if not user_id:
        return None

    with get_db() as db:
        row = db.execute("""
            SELECT id, first_name, last_name, email,
                   role, is_active
            FROM users
            WHERE id = ?
        """, (user_id,)).fetchone()

    if not row:
        session.clear()
        return None

    return dict(row)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):

        user = current_user()

        if not user:
            flash("Please log in to continue.", "info")
            return redirect(
                url_for("login", next=request.path)
            )

        if not user.get("is_active"):
            session.clear()
            flash("Your account is inactive.", "error")
            return redirect(url_for("login"))

        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):

        user = current_user()

        if not user or not user.get("is_active"):
            session.clear()
            flash("Please log in with an active account.", "error")
            return redirect(url_for("login"))

        if user.get("role") not in ("admin", "superadmin"):
            flash("Administrator access is required.", "error")
            return redirect(url_for("home"))

        return view(*args, **kwargs)

    return wrapped


def superadmin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):

        user = current_user()

        if not user or not user.get("is_active"):
            session.clear()
            flash("Please log in with an active account.", "error")
            return redirect(url_for("login"))

        if user.get("role") != "superadmin":
            flash("Only the Super Admin can perform this action.", "error")
            return redirect(url_for("admin_dashboard"))

        return view(*args, **kwargs)

    return wrapped


def is_admin_user():
    user = current_user()
    return bool(
        user and user.get("role") in ("admin", "superadmin")
    )


def is_superadmin_user():
    user = current_user()
    return bool(user and user.get("role") == "superadmin")


@app.context_processor
def inject_template_context():
    return {
        "current_user": current_user(),
        "logged_in": bool(session.get("user_id"))
    }


# =========================================================
# GENERAL PAGES
# =========================================================

@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200


@app.route("/")
def index():
    return redirect(url_for("home"))


@app.route("/home")
def home():
    return render_template("prismlive_home_page.html")


@app.route("/about")
def about():
    return render_template("prismlive_about_page.html")


@app.route("/media")
def media():
    return render_template("prismlive_media_page.html")


# =========================================================
# EMAIL CONFIGURATION
# =========================================================

def send_password_reset_email(recipient, reset_url):

    host = os.environ.get("MAIL_SERVER", "").strip()
    username = os.environ.get("MAIL_USERNAME", "").strip()
    password = os.environ.get("MAIL_PASSWORD", "")
    sender = os.environ.get(
        "MAIL_DEFAULT_SENDER", username
    ).strip()

    port = int(os.environ.get("MAIL_PORT", "587"))
    use_ssl = os.environ.get("MAIL_USE_SSL", "0") == "1"
    use_tls = os.environ.get("MAIL_USE_TLS", "1") == "1"

    if not all([host, username, password, sender]):
        raise RuntimeError("Email SMTP configuration is incomplete.")

    message = EmailMessage()
    message["Subject"] = "Reset your PrismLive password"
    message["From"] = sender
    message["To"] = recipient

    message.set_content(
        "Hello,\n\n"
        "We received a request to reset your PrismLive password.\n\n"
        "Use this secure one-time link within 30 minutes:\n\n"
        f"{reset_url}\n\n"
        "If you did not request this, ignore this email.\n\n"
        "PrismLive"
    )

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=20) as server:
            server.login(username, password)
            server.send_message(message)

    else:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.ehlo()

            if use_tls:
                server.starttls()
                server.ehlo()

            server.login(username, password)
            server.send_message(message)


# =========================================================
# REGISTRATION
# =========================================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        first_name = request.form.get(
            "first_name", ""
        ).strip()[:80]

        last_name = request.form.get(
            "last_name", ""
        ).strip()[:80]

        email = request.form.get(
            "email", ""
        ).strip().lower()[:254]

        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        interest = request.form.get(
            "interest", ""
        ).strip()[:100]

        terms = request.form.get("terms")

        if not all([first_name, last_name, email, password]):
            flash("Please fill in all required fields.", "error")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("register"))

        if not interest:
            flash("Please select your interest.", "error")
            return redirect(url_for("register"))

        if (
            "@" not in email
            or "." not in email.rsplit("@", 1)[-1]
        ):
            flash("Enter a valid email address.", "error")
            return redirect(url_for("register"))

        if not terms:
            flash("Please accept the terms.", "error")
            return redirect(url_for("register"))

        try:
            with get_db() as db:
                db.execute("""
                    INSERT INTO users
                    (first_name, last_name, email, interest, password_hash)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    first_name,
                    last_name,
                    email,
                    interest,
                    generate_password_hash(password)
                ))

            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            flash("An account with this email already exists.", "error")
            return redirect(url_for("register"))

    return render_template("prismlive_register_page.html")


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get(
            "email", ""
        ).strip().lower()

        password = request.form.get("password", "")

        with get_db() as db:
            user = db.execute("""
                SELECT *
                FROM users
                WHERE email = ? COLLATE NOCASE
            """, (email,)).fetchone()

        if (
            user
            and user["is_active"]
            and check_password_hash(user["password_hash"], password)
        ):

            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["user_name"] = user["first_name"]

            if user["role"] in ("admin", "superadmin"):
                return redirect(url_for("admin_dashboard"))

            target = request.args.get("next", "")

            # Only allow safe local redirects.
            if target.startswith("/") and not target.startswith("//"):
                parsed = urlparse(target)

                if not parsed.scheme and not parsed.netloc:
                    return redirect(target)

            return redirect(url_for("home"))

        flash("Invalid email or password.", "error")
        return redirect(url_for("login"))

    return render_template("prismlive_login_page.html")


# =========================================================
# FORGOT PASSWORD
# =========================================================

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():

    if request.method == "POST":

        email = request.form.get(
            "email", ""
        ).strip().lower()[:254]

        with get_db() as db:

            user = db.execute("""
                SELECT id, email
                FROM users
                WHERE email = ? COLLATE NOCASE
                AND is_active = 1
            """, (email,)).fetchone()

            if user:

                raw_token = secrets.token_urlsafe(32)

                token_hash = hashlib.sha256(
                    raw_token.encode("utf-8")
                ).hexdigest()

                expires_at = (
                    datetime.now(timezone.utc)
                    + timedelta(minutes=30)
                ).isoformat()

                db.execute("""
                    DELETE FROM password_reset_tokens
                    WHERE user_id = ?
                """, (user["id"],))

                db.execute("""
                    INSERT INTO password_reset_tokens
                    (user_id, token_hash, expires_at)
                    VALUES (?, ?, ?)
                """, (
                    user["id"],
                    token_hash,
                    expires_at
                ))

                reset_url = url_for(
                    "reset_password",
                    token=raw_token,
                    _external=True
                )

                try:
                    send_password_reset_email(
                        user["email"],
                        reset_url
                    )

                except Exception:
                    app.logger.exception(
                        "Password reset email failed"
                    )

                    db.execute("""
                        DELETE FROM password_reset_tokens
                        WHERE token_hash = ?
                    """, (token_hash,))

                    flash(
                        "Unable to send the reset email. Please try again later.",
                        "error"
                    )

                    return redirect(url_for("forgot_password"))

        flash(
            "If an active account uses that email, a reset link has been sent.",
            "success"
        )

        return redirect(url_for("forgot_password"))

    return render_template("prismlive_forgot_password_page.html")


# =========================================================
# RESET PASSWORD
# =========================================================

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):

    token_hash = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    now = datetime.now(timezone.utc)

    if request.method == "POST":

        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
            return redirect(url_for("reset_password", token=token))

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("reset_password", token=token))

        with get_db() as db:

            record = db.execute("""
                SELECT t.user_id, t.expires_at
                FROM password_reset_tokens t
                JOIN users u ON u.id = t.user_id
                WHERE t.token_hash = ?
                AND u.is_active = 1
            """, (token_hash,)).fetchone()

            if not record:
                flash("Invalid or already-used reset link.", "error")
                return redirect(url_for("forgot_password"))

            try:
                expiry = datetime.fromisoformat(record["expires_at"])

                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)

            except ValueError:
                expiry = now - timedelta(seconds=1)

            if expiry <= now:
                db.execute("""
                    DELETE FROM password_reset_tokens
                    WHERE token_hash = ?
                """, (token_hash,))

                flash("Reset link expired. Request a new one.", "error")
                return redirect(url_for("forgot_password"))

            db.execute("""
                UPDATE users
                SET password_hash = ?
                WHERE id = ?
            """, (
                generate_password_hash(password),
                record["user_id"]
            ))

            db.execute("""
                DELETE FROM password_reset_tokens
                WHERE user_id = ?
            """, (record["user_id"],))

        session.clear()

        flash("Password reset successfully. Please log in.", "success")
        return redirect(url_for("login"))

    with get_db() as db:
        record = db.execute("""
            SELECT expires_at
            FROM password_reset_tokens
            WHERE token_hash = ?
        """, (token_hash,)).fetchone()

    if not record:
        flash("Invalid reset link. Request a new one.", "error")
        return redirect(url_for("forgot_password"))

    try:
        expiry = datetime.fromisoformat(record["expires_at"])

        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

    except ValueError:
        expiry = now - timedelta(seconds=1)

    if expiry <= now:
        with get_db() as db:
            db.execute("""
                DELETE FROM password_reset_tokens
                WHERE token_hash = ?
            """, (token_hash,))

        flash("Reset link expired. Request a new one.", "error")
        return redirect(url_for("forgot_password"))

    return render_template(
        "prismlive_reset_password_page.html",
        token=token
    )


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout", methods=["POST"])
def logout():

    session.clear()

    flash("You have been logged out successfully.", "info")

    return redirect(url_for("home"))


# =========================================================
# CONTACT FORM
# =========================================================

@app.route("/contact", methods=["GET", "POST"])
def contact():

    if request.method == "POST":

        name = request.form.get("name", "").strip()[:120]
        email = request.form.get("email", "").strip().lower()[:254]
        subject = request.form.get("subject", "").strip()[:200]
        message = request.form.get("message", "").strip()[:5000]

        if not all([name, email, subject, message]):
            flash("Please complete all required fields.", "error")
            return redirect(url_for("contact"))

        if "@" not in email:
            flash("Enter a valid email address.", "error")
            return redirect(url_for("contact"))

        with get_db() as db:
            db.execute("""
                INSERT INTO contact_messages
                (name, email, subject, message)
                VALUES (?, ?, ?, ?)
            """, (name, email, subject, message))

        flash("Your message has been submitted.", "success")
        return redirect(url_for("contact"))

    return render_template("prismlive_contact_page.html")


# =========================================================
# GEMINI AI CHATBOT
# =========================================================

@app.post("/api/ai-chat")
def ai_chat_api():

    import requests

    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        return jsonify({
            "error": "AI assistant is not configured."
        }), 503

    payload = request.get_json(silent=True) or {}

    message = str(payload.get("message", "")).strip()
    history = payload.get("history", [])

    if not message:
        return jsonify({"error": "Enter a message."}), 400

    if len(message) > 4000:
        return jsonify({"error": "Message is too long."}), 413

    contents = []

    if isinstance(history, list):
        for item in history[-10:]:

            if (
                isinstance(item, dict)
                and item.get("role") in ("user", "model")
                and isinstance(item.get("text"), str)
            ):
                contents.append({
                    "role": item["role"],
                    "parts": [{"text": item["text"][:4000]}]
                })

    contents.append({
        "role": "user",
        "parts": [{"text": message}]
    })

    model = os.environ.get(
        "GEMINI_MODEL",
        "gemini-2.5-flash"
    )

    try:

        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            params={"key": api_key},
            json={
                "systemInstruction": {
                    "parts": [{
                        "text": (
                            "You are Prism AI, a friendly assistant on PrismLive. "
                            "Give useful concise answers. Do not claim to access "
                            "private user data or complete website actions. "
                            "Never request passwords, OTPs or payment secrets."
                        )
                    }]
                },
                "contents": contents,
                "generationConfig": {
                    "temperature": 0.7,
                    "maxOutputTokens": 900
                }
            },
            timeout=35
        )

        response.raise_for_status()

        data = response.json()

        answer = "".join(
            part.get("text", "")
            for part in data.get(
                "candidates", [{}]
            )[0].get("content", {}).get("parts", [])
        ).strip()

        if not answer:
            return jsonify({
                "error": "The AI returned an empty response."
            }), 502

        return jsonify({"reply": answer})

    except requests.Timeout:
        return jsonify({
            "error": "AI request timed out."
        }), 504

    except requests.RequestException:
        app.logger.exception("Gemini API request failed")

        return jsonify({
            "error": "Could not connect to the AI service."
        }), 502


# =========================================================
# SUBSCRIPTION MANAGEMENT
# =========================================================

@app.get("/subscription")
@login_required
def subscription():

    with get_db() as db:
        plans = db.execute("""
            SELECT id, name, price, duration, description, features
            FROM subscription_plans
            WHERE active = 1
            ORDER BY id
        """).fetchall()

    return render_template(
        "prismlive_subscription_page.html",
        plans=plans,
        is_admin=is_superadmin_user()
    )


# Only one Super Admin subscription-management route.

@app.get("/admin/subscriptions")
@superadmin_required
def admin_subscriptions():

    with get_db() as db:
        plans = db.execute("""
            SELECT id, name, price, duration,
                   description, features, active
            FROM subscription_plans
            ORDER BY id
        """).fetchall()

    return render_template(
        "admin_subscriptions.html",
        plans=plans
    )


@app.post("/admin/subscriptions/edit/<int:plan_id>")
@superadmin_required
def edit_subscription(plan_id):

    name = request.form.get("name", "").strip()[:80]
    description = request.form.get("description", "").strip()[:500]
    features = request.form.get("features", "").strip()[:3000]
    duration = request.form.get("duration", "").strip()
    active = request.form.get("active", "1").strip()

    try:
        price = float(request.form.get("price", ""))

        if not (0.01 <= price <= 1000000):
            raise ValueError

    except (ValueError, TypeError):
        flash("Enter a valid subscription price.", "error")
        return redirect(url_for("admin_subscriptions"))

    if not name or not description or not features:
        flash("Complete all subscription fields.", "error")
        return redirect(url_for("admin_subscriptions"))

    if duration not in ("month", "year") or active not in ("0", "1"):
        flash("Invalid subscription duration or status.", "error")
        return redirect(url_for("admin_subscriptions"))

    with get_db() as db:
        cursor = db.execute("""
            UPDATE subscription_plans
            SET name = ?,
                price = ?,
                duration = ?,
                description = ?,
                features = ?,
                active = ?
            WHERE id = ?
        """, (
            name,
            price,
            duration,
            description,
            features,
            int(active),
            plan_id
        ))

    if cursor.rowcount == 0:
        flash("Subscription plan not found.", "error")
    else:
        flash("Subscription updated successfully.", "success")

    return redirect(url_for("admin_subscriptions"))


@app.post("/subscription/select/<int:plan_id>")
@login_required
def select_subscription(plan_id):

    with get_db() as db:
        plan = db.execute("""
            SELECT id, name, price, duration
            FROM subscription_plans
            WHERE id = ? AND active = 1
        """, (plan_id,)).fetchone()

    if not plan:
        flash("Subscription plan is unavailable.", "error")
        return redirect(url_for("subscription"))

    session["selected_plan_id"] = plan["id"]

    return redirect(url_for(
        "payment",
        plan_id=plan["id"]
    ))


# =========================================================
# UPI PAYMENT
# =========================================================

@app.route("/payment", methods=["GET", "POST"])
@login_required
def payment():

    upi_id = os.environ.get("UPI_ID", "").strip()

    payee = os.environ.get(
        "UPI_PAYEE_NAME",
        "PrismLive"
    ).strip()

    upi_link = ""
    amount = ""

    plan_id = (
        request.form.get("plan_id")
        if request.method == "POST"
        else request.args.get("plan_id")
    )

    try:
        plan_id = int(plan_id)
    except (TypeError, ValueError):
        flash("Please select a valid subscription.", "error")
        return redirect(url_for("subscription"))

    with get_db() as db:
        plan = db.execute("""
            SELECT id, name, price
            FROM subscription_plans
            WHERE id = ? AND active = 1
        """, (plan_id,)).fetchone()

    if not plan:
        flash("Subscription plan is unavailable.", "error")
        return redirect(url_for("subscription"))

    amount = f"{float(plan['price']):.2f}"

    if request.method == "POST":

        if not upi_id or "@" not in upi_id:
            flash("UPI payment is not configured.", "error")

        else:
            upi_link = "upi://pay?" + urlencode({
                "pa": upi_id,
                "pn": payee,
                "am": amount,
                "cu": "INR",
                "tn": f"PrismLive {plan['name']} subscription"
            })

    return render_template(
        "prismlive_payment_page.html",
        upi_id=upi_id,
        payee=payee,
        amount=amount,
        upi_link=upi_link,
        plan=plan
    )


# =========================================================
# USER CHAT
# =========================================================

@app.route("/chat")
@login_required
def chat():

    user_id = session["user_id"]

    with get_db() as db:
        users = db.execute("""
            SELECT id, first_name, last_name, email
            FROM users
            WHERE id != ?
            AND is_active = 1
            ORDER BY first_name, last_name
        """, (user_id,)).fetchall()

    return render_template(
        "prismlive_chat_page.html",
        users=[dict(user) for user in users],
        my_user_id=user_id
    )


@app.get("/api/users")
@login_required
def api_users():

    user_id = session["user_id"]

    with get_db() as db:
        users = db.execute("""
            SELECT id, first_name, last_name, email
            FROM users
            WHERE id != ?
            AND is_active = 1
            ORDER BY first_name, last_name
        """, (user_id,)).fetchall()

    with presence_lock:
        online_ids = set(online_users.keys())

    return jsonify([
        dict(user, online=(user["id"] in online_ids))
        for user in users
    ])


@app.get("/api/messages/<int:recipient_id>")
@login_required
def api_messages(recipient_id):

    user_id = session["user_id"]

    with get_db() as db:

        recipient = db.execute("""
            SELECT id FROM users
            WHERE id = ? AND is_active = 1
        """, (recipient_id,)).fetchone()

        if not recipient or recipient_id == user_id:
            return jsonify({"error": "User not found"}), 404

        rows = db.execute("""
            SELECT m.id, m.sender_id, m.recipient_id,
                   m.message, m.created_at,
                   u.first_name || ' ' || u.last_name AS sender_name
            FROM chat_messages m
            JOIN users u ON u.id = m.sender_id
            WHERE
                (m.sender_id = ? AND m.recipient_id = ?)
                OR
                (m.sender_id = ? AND m.recipient_id = ?)
            ORDER BY m.id ASC
            LIMIT 500
        """, (
            user_id,
            recipient_id,
            recipient_id,
            user_id
        )).fetchall()

    return jsonify([dict(row) for row in rows])


# =========================================================
# ADMIN DASHBOARD
# =========================================================

@app.get("/admin")
@admin_required
def admin_dashboard():

    user = current_user()

    with get_db() as db:

        stats = {
            "users": db.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0],

            "admins": db.execute("""
                SELECT COUNT(*) FROM users
                WHERE role IN ('admin', 'superadmin')
            """).fetchone()[0],

            "messages": db.execute(
                "SELECT COUNT(*) FROM contact_messages"
            ).fetchone()[0],

            "pending": db.execute("""
                SELECT COUNT(*) FROM admin_change_requests
                WHERE status = 'pending'
            """).fetchone()[0]
        }

        requests_rows = db.execute("""
            SELECT r.*,
                   u.first_name || ' ' || u.last_name AS requester_name,
                   reviewer.first_name || ' ' || reviewer.last_name AS reviewer_name
            FROM admin_change_requests r
            JOIN users u ON u.id = r.requested_by
            LEFT JOIN users reviewer ON reviewer.id = r.reviewed_by
            ORDER BY
                CASE r.status WHEN 'pending' THEN 0 ELSE 1 END,
                r.id DESC
            LIMIT 100
        """).fetchall()

        if user["role"] == "superadmin":

            users_rows = db.execute("""
                SELECT id, first_name, last_name, email,
                       role, is_active, created_at
                FROM users
                ORDER BY id DESC
                LIMIT 200
            """).fetchall()

            contact_rows = db.execute("""
                SELECT id, name, email, subject,
                       message, created_at
                FROM contact_messages
                ORDER BY id DESC
                LIMIT 100
            """).fetchall()

        else:
            users_rows = []
            contact_rows = []

    return render_template(
        "admin_dashboard.html",
        user=user,
        stats=stats,
        change_requests=[dict(row) for row in requests_rows],
        users=[dict(row) for row in users_rows],
        contacts=[dict(row) for row in contact_rows]
    )


# =========================================================
# ADMIN CHANGE REQUESTS
# =========================================================

@app.post("/admin/request-change")
@admin_required
def admin_request_change():

    title = request.form.get("title", "").strip()[:160]
    details = request.form.get("details", "").strip()[:5000]

    if not title or not details:
        flash("Enter a title and details.", "error")
        return redirect(url_for("admin_dashboard"))

    with get_db() as db:
        db.execute("""
            INSERT INTO admin_change_requests
            (requested_by, title, details)
            VALUES (?, ?, ?)
        """, (
            session["user_id"],
            title,
            details
        ))

    flash("Change request submitted for Super Admin review.", "success")

    return redirect(url_for("admin_dashboard"))


@app.post("/admin/requests/<int:request_id>/decision")
@superadmin_required
def admin_decide_request(request_id):

    decision = request.form.get(
        "decision", ""
    ).strip().lower()

    note = request.form.get(
        "reviewer_note", ""
    ).strip()[:1000]

    if decision not in ("approved", "rejected"):
        flash("Invalid decision.", "error")
        return redirect(url_for("admin_dashboard"))

    with get_db() as db:

        row = db.execute("""
            SELECT id, status
            FROM admin_change_requests
            WHERE id = ?
        """, (request_id,)).fetchone()

        if not row:
            flash("Request not found.", "error")

        elif row["status"] != "pending":
            flash("This request has already been reviewed.", "info")

        else:
            db.execute("""
                UPDATE admin_change_requests
                SET status = ?,
                    reviewed_by = ?,
                    reviewer_note = ?,
                    reviewed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                decision,
                session["user_id"],
                note,
                request_id
            ))

            flash(f"Request marked {decision}.", "success")

    return redirect(url_for("admin_dashboard"))


# =========================================================
# SUPER ADMIN USER MANAGEMENT
# =========================================================

@app.post("/admin/users/<int:target_id>/role")
@superadmin_required
def admin_set_user_role(target_id):

    role = request.form.get(
        "role", "user"
    ).strip().lower()

    if role not in ("user", "admin"):
        flash("Invalid role.", "error")
        return redirect(url_for("admin_dashboard"))

    if target_id == session.get("user_id"):
        flash("You cannot change your own role.", "error")
        return redirect(url_for("admin_dashboard"))

    with get_db() as db:

        target = db.execute("""
            SELECT id, role FROM users
            WHERE id = ?
        """, (target_id,)).fetchone()

        if not target:
            flash("User not found.", "error")

        elif target["role"] == "superadmin":
            flash("Super Admin role cannot be changed here.", "error")

        else:
            db.execute("""
                UPDATE users SET role = ?
                WHERE id = ?
            """, (role, target_id))

            flash("User role updated.", "success")

    return redirect(url_for("admin_dashboard"))


@app.post("/admin/users/<int:target_id>/active")
@superadmin_required
def admin_set_user_active(target_id):

    active = (
        1 if request.form.get("active") == "1" else 0
    )

    if target_id == session.get("user_id"):
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("admin_dashboard"))

    with get_db() as db:

        target = db.execute("""
            SELECT id, role FROM users
            WHERE id = ?
        """, (target_id,)).fetchone()

        if not target:
            flash("User not found.", "error")

        elif target["role"] == "superadmin":
            flash("Super Admin cannot be deactivated here.", "error")

        else:
            db.execute("""
                UPDATE users SET is_active = ?
                WHERE id = ?
            """, (active, target_id))

            flash("Account status updated.", "success")

    return redirect(url_for("admin_dashboard"))


# =========================================================
# LEGACY IMAGE ROUTE
# =========================================================

@app.get("/images/<path:filename>")
def legacy_images(filename):

    return send_from_directory(
        BASE_DIR / "static" / "images",
        filename
    )


# =========================================================
# LEGACY HTML NAVIGATION
# =========================================================

PAGE_MAP = {
    "index.html": "home",
    "prismlive_home_page.html": "home",
    "prismlive_login_page.html": "login",
    "prismlive_register_page.html": "register",
    "prismlive_chat_page.html": "chat",
    "prismlive_contact_page.html": "contact",
    "prismlive_payment_page.html": "payment"
}


@app.get("/<path:page>.html")
def legacy_page(page):

    filename = page + ".html"

    endpoint = PAGE_MAP.get(filename)

    if endpoint:
        return redirect(url_for(endpoint))

    candidate = BASE_DIR / "templates" / filename

    if (
        candidate.is_file()
        and filename.startswith("prismlive_")
    ):
        return render_template(filename)

    return render_template("404.html"), 404


# =========================================================
# SOCKET.IO REAL-TIME CHAT
# =========================================================

@socketio.on("connect")
def socket_connect(auth=None):

    user = current_user()

    if not user or not user.get("is_active"):
        return False

    user_id = user["id"]

    from flask import request as flask_request

    sid = flask_request.sid

    with presence_lock:

        first_connection = user_id not in online_users

        online_users.setdefault(user_id, set()).add(sid)

    join_room(f"user_{user_id}")

    if first_connection:
        socketio.emit(
            "presence_update",
            {
                "user_id": user_id,
                "online": True
            }
        )


@socketio.on("disconnect")
def socket_disconnect(reason=None):

    from flask import request as flask_request

    user_id = session.get("user_id")

    if not user_id:
        return

    sid = flask_request.sid

    with presence_lock:

        connections = online_users.get(user_id)

        if connections:
            connections.discard(sid)

            if not connections:
                online_users.pop(user_id, None)

                socketio.emit(
                    "presence_update",
                    {
                        "user_id": user_id,
                        "online": False
                    }
                )


@socketio.on("send_message")
def socket_send_message(data):

    user = current_user()

    if not user or not user.get("is_active"):
        emit("chat_error", {
            "message": "Please log in again."
        })
        return

    if not isinstance(data, dict):
        emit("chat_error", {
            "message": "Invalid message."
        })
        return

    user_id = user["id"]

    try:
        recipient_id = int(data.get("recipient_id", 0))
    except (TypeError, ValueError):
        recipient_id = 0

    message = str(data.get("message", "")).strip()

    if (
        not message
        or len(message) > 2000
        or recipient_id == user_id
    ):
        emit("chat_error", {
            "message": "Select a user and enter a message under 2,000 characters."
        })
        return

    with get_db() as db:

        recipient = db.execute("""
            SELECT id FROM users
            WHERE id = ? AND is_active = 1
        """, (recipient_id,)).fetchone()

        if not recipient:
            emit("chat_error", {
                "message": "Recipient not found."
            })
            return

        cursor = db.execute("""
            INSERT INTO chat_messages
            (sender_id, recipient_id, message)
            VALUES (?, ?, ?)
        """, (
            user_id,
            recipient_id,
            message
        ))

        message_id = cursor.lastrowid

        saved = db.execute("""
            SELECT created_at
            FROM chat_messages
            WHERE id = ?
        """, (message_id,)).fetchone()

    payload = {
        "id": message_id,
        "sender_id": user_id,
        "recipient_id": recipient_id,
        "message": message,
        "created_at": saved["created_at"],
        "sender_name": (
            f"{user['first_name']} {user['last_name']}"
        )
    }

    socketio.emit(
        "new_message",
        payload,
        room=f"user_{recipient_id}"
    )

    emit("new_message", payload)


# =========================================================
# APPLICATION STARTUP
# =========================================================

initialize_database()


if __name__ == "__main__":

    port = int(os.environ.get("PORT", 10000))

    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=os.environ.get("FLASK_DEBUG") == "1",
        allow_unsafe_werkzeug=True
    )
