"""WSGI entry point for hosted deployment (cPanel / Passenger).

Passenger settings:
    Application startup file: app.py
    Application Entry point:  application

All page rendering and data logic lives in main.py and is shared with the
local development server (py main.py).
"""

import traceback

from flask import Flask, Response, jsonify, redirect, request

import auth
import main as core

app = Flask(__name__)


def current_user():
        return auth.get_session_user(request.cookies.get(auth.SESSION_COOKIE_NAME))


@app.before_request
def require_login():
        if request.path in auth.PUBLIC_PATHS:
                return None
        if current_user() is None:
                return redirect("/login", code=303)
        return None


@app.get("/login")
def login_page():
        if current_user() is not None:
                return redirect("/", code=303)
        return Response(auth.render_login_page(), mimetype="text/html")


@app.post("/login")
def login_submit():
        user, error = auth.authenticate(request.form.get("username"), request.form.get("password"))
        if user is None:
                return Response(auth.render_login_page(error=error), mimetype="text/html")
        response = redirect("/", code=303)
        response.headers.add("Set-Cookie", auth.build_session_cookie(auth.create_session_token(user["username"])))
        return response


@app.get("/register")
def register_page():
        return Response(auth.render_register_page(), mimetype="text/html")


@app.post("/register")
def register_submit():
        ok, error = auth.register_user(
                request.form.get("username"),
                request.form.get("password"),
                request.form.get("fullName"),
                request.form.get("mobileNumber"),
        )
        if not ok:
                return Response(auth.render_register_page(error=error), mimetype="text/html")
        return Response(
                auth.render_login_page(
                        message="Registration received. You can log in after an admin approves your account."
                ),
                mimetype="text/html",
        )


@app.route("/logout", methods=["GET", "POST"])
def logout():
        response = redirect("/login", code=303)
        response.headers.add("Set-Cookie", auth.build_logout_cookie())
        return response


@app.get("/admin/users")
def admin_users():
        user = current_user()
        if user is None or user.get("role") != "admin":
                return Response("Admin access required", status=403, mimetype="text/plain")
        return Response(core.render_admin_users_page(user), mimetype="text/html")


@app.post("/admin/user-action")
def admin_user_action():
        user = current_user()
        if user is None or user.get("role") != "admin":
                return Response("Admin access required", status=403, mimetype="text/plain")
        target = request.form.get("username", "")
        action = request.form.get("action", "")
        if action == "approve":
                auth.set_user_status(target, "approved")
        elif action == "reject":
                auth.set_user_status(target, "rejected")
        elif action == "delete":
                auth.delete_user(target)
        return redirect("/admin/users", code=303)


# TEMPORARY: show the real traceback in the browser to debug the 500 errors.
# Remove this handler once the issue is resolved.
@app.errorhandler(Exception)
def show_error(exc):
        return Response(
                "APP ERROR:\n\n" + traceback.format_exc(),
                status=500,
                mimetype="text/plain",
        )


def query_params():
        return request.args.to_dict(flat=False)


@app.get("/")
@app.get("/leads")
@app.get("/active-clients")
@app.get("/expiry-alerts")
@app.get("/send-quote")
@app.get("/send-payment-link")
def render_page_route():
        return Response(
                core.render_page(request.path, query_params(), current_user()),
                mimetype="text/html",
        )


@app.get("/style.css")
def style_css():
        return Response(core.STYLE_PATH.read_text(encoding="utf-8"), mimetype="text/css")


@app.post("/add-customer")
@app.post("/convert-customer")
@app.post("/delete-customer")
@app.post("/edit-customer")
def form_action():
        return_to = core.perform_form_action(request.path, request.form.to_dict(flat=False))
        return redirect(return_to, code=303)


@app.post("/bulk-upload-customers")
def bulk_upload():
        return_to = core.sanitize_return_to(
                core.sanitize_form_value(request.form.get("return_to", "/leads"))
        )
        upload = request.files.get("excel_file")
        if upload is not None:
                core.append_new_customers_from_excel_bytes(upload.read())
        return redirect(return_to, code=303)


@app.get("/api/send-quote/rows")
@app.get("/api/send-payment-link/rows")
def send_link_rows():
        kind, _ = core.get_send_link_api_action(request.path)
        return jsonify({"rows": core.SEND_LINK_BATCHES[kind]})


@app.get("/api/send-quote/sent-rows")
@app.get("/api/send-payment-link/sent-rows")
def send_link_sent_rows():
        kind, _ = core.get_send_link_api_action(request.path)
        return jsonify({"rows": core.SEND_LINK_SENT[kind]})


@app.post("/api/send-quote/import")
@app.post("/api/send-payment-link/import")
def send_link_import():
        kind, _ = core.get_send_link_api_action(request.path)
        upload = request.files.get("excel_file")
        if upload is None:
                return jsonify({"success": False, "error": "No file was uploaded."}), 400
        payload, status_code = core.import_send_quote_file(upload.filename, upload.read(), kind)
        return jsonify(payload), status_code


@app.post("/api/send-quote/status")
@app.post("/api/send-payment-link/status")
def send_link_status():
        kind, _ = core.get_send_link_api_action(request.path)
        data = request.get_json(silent=True) or {}
        payload, status_code = core.set_send_quote_status(
                data.get("id"), str(data.get("status", "")).strip(), kind
        )
        return jsonify(payload), status_code


@app.post("/api/send-quote/send")
@app.post("/api/send-payment-link/send")
def send_link_send():
        kind, _ = core.get_send_link_api_action(request.path)
        data = request.get_json(silent=True) or {}
        payload, status_code = core.send_quote_for_row(data.get("id"), kind)
        return jsonify(payload), status_code


# Passenger looks for this name.
application = app

if __name__ == "__main__":
        app.run(host="127.0.0.1", port=8001, debug=False)
