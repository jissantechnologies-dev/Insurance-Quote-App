"""WSGI entry point for hosted deployment (cPanel / Passenger).

Passenger settings:
    Application startup file: app.py
    Application Entry point:  application

All page rendering and data logic lives in main.py and is shared with the
local development server (py main.py).
"""

import json
import os
import traceback
from urllib.parse import quote_plus

from flask import Flask, Response, jsonify, redirect, request, send_file

import auth
import chat
import legal
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


@app.get("/privacy-policy")
def privacy_policy_page():
        return Response(legal.render_privacy_policy_page(), mimetype="text/html")


@app.get("/terms-and-conditions")
def terms_page():
        return Response(legal.render_terms_page(), mimetype="text/html")


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


@app.errorhandler(Exception)
def show_error(exc):
        """Tracebacks leak file paths and source, so only show them when debugging."""
        app.logger.exception("Unhandled error on %s", request.path)
        if os.environ.get("GI_SHOW_TRACEBACKS") == "1":
                return Response(
                        "APP ERROR:\n\n" + traceback.format_exc(),
                        status=500,
                        mimetype="text/plain",
                )
        return Response("Something went wrong. Please try again.", status=500, mimetype="text/plain")


@app.after_request
def security_headers(response):
        # Tell browsers to keep using https for this host (1 year).
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response


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


@app.post("/upload-document")
def upload_document():
        source = core.sanitize_form_value(request.form.get("source", ""))
        try:
                index = int(core.sanitize_form_value(request.form.get("index", "")))
        except ValueError:
                index = -1
        doc_type = core.sanitize_form_value(request.form.get("doc_type", ""))
        return_to = core.sanitize_return_to(
                core.sanitize_form_value(request.form.get("return_to", "/leads"))
        )
        upload = request.files.get("document")
        if upload is not None and upload.filename:
                core.handle_document_upload(source, index, doc_type, upload.filename, upload.read())
        return redirect(f"{return_to}?edit_source={quote_plus(source)}&edit_index={index}", code=303)


@app.get("/documents/<doc_id>/<doc_type>")
def get_document(doc_id, doc_type):
        doc_path = core.get_customer_document_path(doc_id, doc_type)
        if doc_path is None:
                return Response("Not found", status=404, mimetype="text/plain")
        return send_file(doc_path, as_attachment=False, download_name=doc_path.name)


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


# --- WhatsApp Cloud API webhook -------------------------------------------
# Meta -> Callback URL: https://app.gravityinsurance.in/webhook/whatsapp
# The verify token must match the GI_WA_VERIFY_TOKEN environment variable.

WA_VERIFY_TOKEN = os.environ.get("GI_WA_VERIFY_TOKEN", "")
WA_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whatsapp_webhook.log")


@app.get("/webhook/whatsapp")
def whatsapp_webhook_verify():
        """Meta's one-time subscription handshake."""
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge", "")
        if mode == "subscribe" and WA_VERIFY_TOKEN and token == WA_VERIFY_TOKEN:
                return Response(challenge, mimetype="text/plain")
        return Response("Forbidden", status=403, mimetype="text/plain")


@app.post("/webhook/whatsapp")
def whatsapp_webhook_receive():
        """Inbound messages and delivery-status callbacks.

        Meta retries anything that is not a fast 200, so acknowledge first and
        keep the handler cheap - store the messages, then append the raw
        payload to a log for troubleshooting.
        """
        payload = request.get_json(silent=True) or {}
        try:
                chat.record_webhook(payload)
        except Exception:
                # Never let a storage failure turn into a retry storm from Meta.
                traceback.print_exc()
        try:
                with open(WA_LOG_PATH, "a", encoding="utf-8") as handle:
                        json.dump(payload, handle, ensure_ascii=False)
                        handle.write("\n")
        except OSError:
                pass
        return Response("EVENT_RECEIVED", status=200, mimetype="text/plain")


@app.get("/api/chat/<number>")
def chat_thread(number):
        payload, status_code = core.get_chat_thread(number)
        return jsonify(payload), status_code


@app.post("/api/chat/<number>/send")
def chat_send(number):
        data = request.get_json(silent=True) or {}
        payload, status_code = core.send_chat_reply(number, data.get("message"))
        return jsonify(payload), status_code


# Passenger looks for this name.
application = app

if __name__ == "__main__":
        app.run(host="127.0.0.1", port=8001, debug=False)
