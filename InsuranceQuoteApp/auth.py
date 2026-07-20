"""Authentication and user management for the Gravity Insurance app.

Users are stored in users.json next to this file. New registrations start
with status "pending" and must be approved by an admin before they can
log in. Sessions are HMAC-signed cookie tokens; the signing secret is
persisted in auth_secret.key so sessions survive restarts.

A default admin account (username: admin, password: admin123) is created
automatically the first time the user store is touched - change its
password by editing users.json (delete the user and re-register, then
promote) or keep it private.
"""

import hashlib
import hmac
import json
import secrets
import time
from html import escape
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
USERS_JSON_PATH = BASE_DIR / "users.json"
SECRET_KEY_PATH = BASE_DIR / "auth_secret.key"

SESSION_COOKIE_NAME = "gi_session"
SESSION_DURATION_SECONDS = 60 * 60 * 24 * 7  # 7 days

PUBLIC_PATHS = {"/login", "/register", "/style.css", "/favicon.ico"}

DEFAULT_ADMIN = {
        "username": "admin",
        "fullName": "Administrator",
        "mobileNumber": "",
        "role": "admin",
        "status": "approved",
}
DEFAULT_ADMIN_PASSWORD = "admin123"


# ---------------------------------------------------------------- storage

def _hash_password(password, salt):
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 120_000)
        return digest.hex()


def load_users():
        if not USERS_JSON_PATH.exists():
                users = []
                _seed_default_admin(users)
                save_users(users)
                return users
        with USERS_JSON_PATH.open("r", encoding="utf-8") as file:
                return json.load(file)


def save_users(users):
        with USERS_JSON_PATH.open("w", encoding="utf-8") as file:
                json.dump(users, file, indent=4)


def _seed_default_admin(users):
        salt = secrets.token_hex(16)
        admin = dict(DEFAULT_ADMIN)
        admin["salt"] = salt
        admin["passwordHash"] = _hash_password(DEFAULT_ADMIN_PASSWORD, salt)
        admin["createdAt"] = time.strftime("%Y-%m-%d %H:%M")
        users.append(admin)


def get_user(username):
        username = str(username or "").strip().lower()
        for user in load_users():
                if user.get("username", "").lower() == username:
                        return user
        return None


# ----------------------------------------------------------- registration

def register_user(username, password, full_name, mobile_number):
        username = str(username or "").strip().lower()
        if not username or not username.replace("_", "").replace(".", "").isalnum():
                return False, "Username may only contain letters, numbers, dots and underscores."
        if len(username) < 3:
                return False, "Username must be at least 3 characters."
        if len(password or "") < 6:
                return False, "Password must be at least 6 characters."
        if not str(full_name or "").strip():
                return False, "Full name is required."

        users = load_users()
        if any(user.get("username", "").lower() == username for user in users):
                return False, "That username is already taken."

        salt = secrets.token_hex(16)
        users.append(
                {
                        "username": username,
                        "fullName": str(full_name).strip(),
                        "mobileNumber": str(mobile_number or "").strip(),
                        "role": "user",
                        "status": "pending",
                        "salt": salt,
                        "passwordHash": _hash_password(password, salt),
                        "createdAt": time.strftime("%Y-%m-%d %H:%M"),
                }
        )
        save_users(users)
        return True, ""


def authenticate(username, password):
        """Returns (user, error_message). user is None on failure."""
        user = get_user(username)
        if user is None:
                return None, "Invalid username or password."
        expected = user.get("passwordHash", "")
        actual = _hash_password(str(password or ""), user.get("salt", "00"))
        if not hmac.compare_digest(expected, actual):
                return None, "Invalid username or password."
        status = user.get("status", "pending")
        if status == "pending":
                return None, "Your account is awaiting admin approval."
        if status != "approved":
                return None, "Your account has been rejected. Contact the administrator."
        return user, ""


def set_user_status(username, status):
        if status not in ("approved", "pending", "rejected"):
                return False
        users = load_users()
        for user in users:
                if user.get("username") == username:
                        if user.get("role") == "admin" and status != "approved":
                                return False
                        user["status"] = status
                        save_users(users)
                        return True
        return False


def delete_user(username):
        users = load_users()
        remaining = [u for u in users if not (u.get("username") == username and u.get("role") != "admin")]
        if len(remaining) == len(users):
                return False
        save_users(remaining)
        return True


# -------------------------------------------------------------- sessions

def _get_secret():
        if SECRET_KEY_PATH.exists():
                return SECRET_KEY_PATH.read_bytes()
        secret = secrets.token_bytes(32)
        SECRET_KEY_PATH.write_bytes(secret)
        return secret


def _sign(payload):
        return hmac.new(_get_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_session_token(username):
        expires = int(time.time()) + SESSION_DURATION_SECONDS
        payload = f"{username}|{expires}"
        return f"{payload}|{_sign(payload)}"


def get_session_user(token):
        """Validate a session token; returns the user dict or None."""
        parts = str(token or "").split("|")
        if len(parts) != 3:
                return None
        username, expires_text, signature = parts
        payload = f"{username}|{expires_text}"
        if not hmac.compare_digest(_sign(payload), signature):
                return None
        try:
                if int(expires_text) < time.time():
                        return None
        except ValueError:
                return None
        user = get_user(username)
        if user is None or user.get("status") != "approved":
                return None
        return user


def parse_cookie_header(cookie_header):
        cookies = {}
        for chunk in str(cookie_header or "").split(";"):
                if "=" in chunk:
                        key, _, value = chunk.partition("=")
                        cookies[key.strip()] = value.strip()
        return cookies


def build_session_cookie(token):
        return (
                f"{SESSION_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; "
                f"Max-Age={SESSION_DURATION_SECONDS}"
        )


def build_logout_cookie():
        return f"{SESSION_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


# -------------------------------------------------------------- rendering

AUTH_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title} - Gravity Insurance</title>
    <link rel="stylesheet" href="/style.css" />
</head>
<body>
    <div class="page-shell auth-shell">
        <header class="hero auth-hero">
            <p class="eyebrow">Insurance Dashboard</p>
            <h1>Gravity Insurance</h1>
            <p class="subheading">{title}</p>
        </header>
        <div class="card auth-card">
            {content}
        </div>
    </div>
</body>
</html>"""


def _alert(kind, message):
        if not message:
                return ""
        return f'<div class="auth-alert auth-alert-{kind}">{escape(message)}</div>'


def render_login_page(error="", message=""):
        content = (
                "<h2>Login</h2>"
                + _alert("error", error)
                + _alert("success", message)
                + '<form class="add-customer-form auth-form" method="post" action="/login">'
                '<label>Username <input type="text" name="username" required autofocus /></label>'
                '<label>Password <input type="password" name="password" required /></label>'
                '<button class="save-customer-btn" type="submit">Login</button>'
                "</form>"
                '<p class="auth-switch">New here? <a href="/register">Create an account</a></p>'
        )
        return AUTH_PAGE_TEMPLATE.format(title="Login", content=content)


def render_register_page(error=""):
        content = (
                "<h2>Register</h2>"
                + _alert("error", error)
                + '<p class="auth-note">New accounts need admin approval before you can log in.</p>'
                '<form class="add-customer-form auth-form" method="post" action="/register">'
                '<label>Full Name <input type="text" name="fullName" required autofocus /></label>'
                '<label>Mobile Number <input type="tel" name="mobileNumber" /></label>'
                '<label>Username <input type="text" name="username" minlength="3" required /></label>'
                '<label>Password <input type="password" name="password" minlength="6" required /></label>'
                '<button class="save-customer-btn" type="submit">Register</button>'
                "</form>"
                '<p class="auth-switch">Already have an account? <a href="/login">Login</a></p>'
        )
        return AUTH_PAGE_TEMPLATE.format(title="Register", content=content)


def _user_action_form(username, action, label, css_class, confirm_text):
        return (
                f'<form class="action-form" method="post" action="/admin/user-action" '
                f"onsubmit=\"return confirm('{confirm_text}')\">"
                f'<input type="hidden" name="username" value="{escape(username)}" />'
                f'<input type="hidden" name="action" value="{action}" />'
                f'<button class="{css_class}" type="submit">{label}</button>'
                "</form>"
        )


def build_users_admin_content():
        users = load_users()
        pending = [u for u in users if u.get("status") == "pending"]
        others = [u for u in users if u.get("status") != "pending"]

        def row(user):
                username = user.get("username", "")
                status = user.get("status", "pending")
                actions = []
                if user.get("role") != "admin":
                        if status != "approved":
                                actions.append(_user_action_form(username, "approve", "Approve", "save-customer-btn", "Approve this user?"))
                        if status == "pending":
                                actions.append(_user_action_form(username, "reject", "Reject", "delete-btn", "Reject this user?"))
                        actions.append(_user_action_form(username, "delete", "Delete", "delete-btn", "Delete this user permanently?"))
                action_html = f'<div class="row-actions">{"".join(actions)}</div>' if actions else "&mdash;"
                return (
                        f"<tr><td>{escape(user.get('fullName', ''))}</td>"
                        f"<td>{escape(username)}</td>"
                        f"<td>{escape(user.get('mobileNumber', ''))}</td>"
                        f"<td>{escape(user.get('role', 'user'))}</td>"
                        f'<td><span class="status-badge status-{escape(status)}">{escape(status.title())}</span></td>'
                        f"<td>{escape(user.get('createdAt', ''))}</td>"
                        f"<td>{action_html}</td></tr>"
                )

        header = (
                "<thead><tr><th>Name</th><th>Username</th><th>Mobile</th><th>Role</th>"
                "<th>Status</th><th>Registered</th><th>Action</th></tr></thead>"
        )
        pending_rows = "".join(row(u) for u in pending) or '<tr><td colspan="7">No pending registrations.</td></tr>'
        other_rows = "".join(row(u) for u in others) or '<tr><td colspan="7">No users.</td></tr>'

        return (
                '<div class="card"><h2>Pending Approvals</h2>'
                f"<div class=\"table-scroll\"><table>{header}<tbody>{pending_rows}</tbody></table></div></div>"
                '<div class="card"><h2>All Users</h2>'
                f"<div class=\"table-scroll\"><table>{header}<tbody>{other_rows}</tbody></table></div></div>"
        )
