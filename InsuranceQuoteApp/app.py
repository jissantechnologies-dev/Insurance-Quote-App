"""WSGI entry point for hosted deployment (cPanel / Passenger).

Passenger settings:
    Application startup file: app.py
    Application Entry point:  application

All page rendering and data logic lives in main.py and is shared with the
local development server (py main.py).
"""

import traceback

from flask import Flask, Response, jsonify, redirect, request

import main as core

app = Flask(__name__)


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
def render_page_route():
        return Response(core.render_page(request.path, query_params()), mimetype="text/html")


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
def send_quote_rows():
        return jsonify({"rows": core.SEND_QUOTE_BATCH})


@app.post("/api/send-quote/import")
def send_quote_import():
        upload = request.files.get("excel_file")
        if upload is None:
                return jsonify({"success": False, "error": "No file was uploaded."}), 400
        payload, status_code = core.import_send_quote_file(upload.filename, upload.read())
        return jsonify(payload), status_code


@app.post("/api/send-quote/status")
def send_quote_status():
        data = request.get_json(silent=True) or {}
        payload, status_code = core.set_send_quote_status(
                data.get("id"), str(data.get("status", "")).strip()
        )
        return jsonify(payload), status_code


@app.post("/api/send-quote/send")
def send_quote_send():
        data = request.get_json(silent=True) or {}
        payload, status_code = core.send_quote_for_row(data.get("id"))
        return jsonify(payload), status_code


# Passenger looks for this name.
application = app

if __name__ == "__main__":
        app.run(host="127.0.0.1", port=8001, debug=False)
