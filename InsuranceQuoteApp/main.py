import csv
import json
from io import BytesIO
from collections import Counter
from datetime import date, datetime
from email.parser import BytesParser
from email.policy import default
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

try:
        from openpyxl import load_workbook
except ImportError:
        load_workbook = None


BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "template.html"
STYLE_PATH = BASE_DIR / "style.css"
CUSTOMERS_JSON_PATH = BASE_DIR / "customers.json"
NEW_CUSTOMERS_TEXT_PATH = BASE_DIR / "newcustomer.txt"
NEW_CUSTOMERS_EXCEL_PATH = BASE_DIR / "newcustomer.xlsx"
ALLOWED_PAGE_PATHS = {"/", "/leads", "/active-clients", "/expiry-alerts", "/send-quote", "/send-payment-link"}

import auth

SEND_QUOTE_BASE_COLUMNS = [
        ("name", "Name", ("name", "customer name")),
        ("mobileNumber", "Mobile Number", ("mobile number", "mobile", "mobilenumber", "phone", "phone number")),
        ("vehicleNumber", "Vehicle Number", ("vehicle number", "vehicle no", "vehicleno", "vehiclenumber", "registration number", "reg number", "regnumber")),
        ("expiryDate", "Expiry Date", ("expiry date", "policy expiry date", "expirydate")),
]
SEND_QUOTE_STATUSES = ("Pending", "Sent", "Failed", "Delivered", "Read")

# Two instances of the same "import a list, pick rows, send WhatsApp
# messages" workflow live on separate pages: Send Quote and Send Payment
# Link. They share all logic below via the `kind` parameter ("quote" or
# "payment") and only differ in copy, the link column header, and where
# their in-memory batch/state is stored.
SEND_LINK_KINDS = {
        "quote": {
                "page_title": "Send Quote",
                "path": "/send-quote",
                "api_prefix": "/api/send-quote",
                "link_header": "Quote",
                "link_aliases": ("quote link", "quotelink", "link", "quote"),
                "view_label": "View Quote",
                "send_btn_label": "Send Quote",
                "message_intro": "Your renewal quote is ready.",
        },
        "payment": {
                "page_title": "Send Payment Link",
                "path": "/send-payment-link",
                "api_prefix": "/api/send-payment-link",
                "link_header": "Payment Link",
                "link_aliases": ("payment link", "paymentlink", "link"),
                "view_label": "View Payment Link",
                "send_btn_label": "Send Payment Link",
                "message_intro": "Please use the link below to complete your payment.",
        },
}
SEND_LINK_BATCHES = {kind: [] for kind in SEND_LINK_KINDS}
SEND_LINK_NEXT_ID = {kind: [1] for kind in SEND_LINK_KINDS}


def get_send_link_columns(kind):
        config = SEND_LINK_KINDS[kind]
        return SEND_QUOTE_BASE_COLUMNS + [("quoteLink", config["link_header"], config["link_aliases"])]


def get_send_link_api_action(path):
        """Match an incoming request path like '/api/send-quote/import' or
        '/api/send-payment-link/send' to (kind, action), or None."""
        for kind, config in SEND_LINK_KINDS.items():
                prefix = config["api_prefix"] + "/"
                if path.startswith(prefix):
                        return kind, path[len(prefix):]
        return None


def get_new_customers_excel_path():
        if NEW_CUSTOMERS_EXCEL_PATH.exists():
                return NEW_CUSTOMERS_EXCEL_PATH

        for candidate in sorted(BASE_DIR.glob("*.xlsx")):
                if not candidate.name.startswith("~$"):
                        return candidate
        return None


def load_customers():
        with CUSTOMERS_JSON_PATH.open("r", encoding="utf-8") as file:
                return json.load(file)


def save_customers(customers):
        with CUSTOMERS_JSON_PATH.open("w", encoding="utf-8") as file:
                json.dump(customers, file, indent=4)


def load_new_customers_text():
        with NEW_CUSTOMERS_TEXT_PATH.open("a+", encoding="utf-8") as file:
                file.seek(0)
                text = file.read().strip()
        return text if text else "Empty file"


def load_new_customer_lines():
        if not NEW_CUSTOMERS_TEXT_PATH.exists():
                return []
        with NEW_CUSTOMERS_TEXT_PATH.open("r", encoding="utf-8") as file:
                return [line.strip() for line in file.readlines() if line.strip()]


def save_new_customer_lines(lines):
        content = "\n".join(lines)
        if content:
                content += "\n"
        NEW_CUSTOMERS_TEXT_PATH.write_text(content, encoding="utf-8")


def sanitize_form_value(value):
        clean = str(value or "").strip()
        clean = clean.replace("\r", " ").replace("\n", " ").replace("|", "/")
        return clean


def build_customer_line(customer):
        ordered_keys = [
                "name",
                "age",
                "mobileNumber",
                "carType",
                "carBrand",
                "carModel",
                "yearOfManufacture",
                "PolicyNumber",
                "policyExpiryDate",
        ]
        return "|".join(sanitize_form_value(customer.get(key, "")) for key in ordered_keys)


def append_new_customer(customer):
        customer_line = build_customer_line(customer)
        if not customer_line.strip("|"):
                return

        with NEW_CUSTOMERS_TEXT_PATH.open("a+", encoding="utf-8") as file:
                file.seek(0, 2)
                has_existing_content = file.tell() > 0
                if has_existing_content:
                        file.seek(file.tell() - 1)
                        last_char = file.read(1)
                        if last_char != "\n":
                                file.write("\n")
                file.write(customer_line)


def extract_post_value(post_data, key):
        values = post_data.get(key, [""])
        return sanitize_form_value(values[0] if values else "")


def parse_customer_parts(parts):
        return {
                "name": parts[0] if len(parts) > 0 else "",
                "age": parts[1] if len(parts) > 1 else "",
                "mobileNumber": parts[2] if len(parts) > 2 else "",
                "carType": parts[3] if len(parts) > 3 else "",
                "carBrand": parts[4] if len(parts) > 4 else "",
                "carModel": parts[5] if len(parts) > 5 else "",
                "yearOfManufacture": parts[6] if len(parts) > 6 else "",
                "PolicyNumber": parts[7] if len(parts) > 7 else "",
                "policyExpiryDate": parts[8] if len(parts) > 8 else "",
        }


def get_customer_from_post(post_data):
        return {
                "name": extract_post_value(post_data, "name"),
                "age": extract_post_value(post_data, "age"),
                "mobileNumber": extract_post_value(post_data, "mobileNumber"),
                "carType": extract_post_value(post_data, "carType"),
                "carBrand": extract_post_value(post_data, "carBrand"),
                "carModel": extract_post_value(post_data, "carModel"),
                "yearOfManufacture": extract_post_value(post_data, "yearOfManufacture"),
                "PolicyNumber": extract_post_value(post_data, "PolicyNumber"),
                "policyExpiryDate": extract_post_value(post_data, "policyExpiryDate"),
        }


def update_new_customer(index, customer):
        lines = load_new_customer_lines()
        if index < 0 or index >= len(lines):
                return False
        lines[index] = build_customer_line(customer)
        save_new_customer_lines(lines)
        return True


def delete_new_customer(index):
        lines = load_new_customer_lines()
        if index < 0 or index >= len(lines):
                return False
        del lines[index]
        save_new_customer_lines(lines)
        return True


def update_json_customer(index, customer):
        customers = load_customers()
        if index < 0 or index >= len(customers):
                return False
        customers[index] = customer
        save_customers(customers)
        return True


def delete_json_customer(index):
        customers = load_customers()
        if index < 0 or index >= len(customers):
                return False
        del customers[index]
        save_customers(customers)
        return True


def get_excel_column_index_map(sheet):
        header_values = [normalize_excel_header(cell.value) for cell in sheet[1]]
        return {header: index + 1 for index, header in enumerate(header_values) if header}


def find_excel_column(column_map, *aliases):
        for alias in aliases:
                key = normalize_excel_header(alias)
                if key in column_map:
                        return column_map[key]
        return None


def update_excel_customer(index, customer):
        excel_path = get_new_customers_excel_path()
        if excel_path is None or load_workbook is None:
                return False

        workbook = load_workbook(excel_path)
        sheet = workbook.active
        target_row = index + 2
        if target_row > sheet.max_row:
                workbook.close()
                return False

        column_map = get_excel_column_index_map(sheet)
        mapping = [
                ("name", ("name", "customer name")),
                ("age", ("age",)),
                ("mobileNumber", ("mobile number", "mobile", "mobilenumber", "phone", "phone number")),
                ("carType", ("car type", "car", "cartype")),
                ("carBrand", ("car brand", "brand", "carbrand")),
                ("carModel", ("car model", "model", "carmodel")),
                ("yearOfManufacture", ("year of manufacture", "year", "year of purchase", "yearofmanufacture")),
                ("PolicyNumber", ("policy number", "policy no", "policynumber")),
                ("policyExpiryDate", ("policy expiry date", "expiry date", "policyexpirydate")),
        ]
        for customer_key, aliases in mapping:
                col_index = find_excel_column(column_map, *aliases)
                if col_index:
                        sheet.cell(row=target_row, column=col_index).value = customer.get(customer_key, "")

        workbook.save(excel_path)
        workbook.close()
        return True


def delete_excel_customer(index):
        excel_path = get_new_customers_excel_path()
        if excel_path is None or load_workbook is None:
                return False

        workbook = load_workbook(excel_path)
        sheet = workbook.active
        target_row = index + 2
        if target_row > sheet.max_row:
                workbook.close()
                return False
        sheet.delete_rows(target_row, 1)
        workbook.save(excel_path)
        workbook.close()
        return True


def update_customer_by_source(source, index, customer):
        if source == "lead":
                return update_new_customer(index, customer)
        if source == "active_json":
                return update_json_customer(index, customer)
        if source == "active_xl":
                return update_excel_customer(index, customer)
        return False


def delete_customer_by_source(source, index):
        if source == "lead":
                return delete_new_customer(index)
        if source == "active_json":
                return delete_json_customer(index)
        if source == "active_xl":
                return delete_excel_customer(index)
        return False


def get_edit_customer(source, index):
        if source == "lead":
                customers = load_new_customers()
        elif source == "active_json":
                customers = load_customers()
        elif source == "active_xl":
                customers = load_new_customers_from_excel()
        else:
                return None

        if index < 0 or index >= len(customers):
                return None
        return customers[index]


def convert_new_customer_to_existing(index):
        lines = load_new_customer_lines()
        if index < 0 or index >= len(lines):
                return False

        parts = [part.strip() for part in lines[index].split("|")]
        customer_to_move = parse_customer_parts(parts)

        customers = load_customers()
        customers.append(customer_to_move)
        save_customers(customers)

        del lines[index]
        save_new_customer_lines(lines)
        return True


def normalize_excel_header(header_text):
        return "".join(ch for ch in str(header_text or "").strip().lower() if ch.isalnum())


def clean_excel_value(value):
        if value is None:
                return ""
        if isinstance(value, datetime):
                return value.date().isoformat()
        if isinstance(value, date):
                return value.isoformat()
        if isinstance(value, float) and value.is_integer():
                return str(int(value))
        return str(value).strip()


def get_excel_value(row, *header_aliases):
        for alias in header_aliases:
                value = row.get(normalize_excel_header(alias), "")
                if value != "":
                        return value
        return ""


def load_new_customers_from_excel():
        excel_path = get_new_customers_excel_path()
        return load_customers_from_excel_source(excel_path)


def load_customers_from_excel_source(excel_source):
        if excel_source is None or load_workbook is None:
                return []

        workbook = load_workbook(excel_source, read_only=True, data_only=True)
        sheet = workbook.active

        rows_iter = sheet.iter_rows(values_only=True)
        headers_row = next(rows_iter, None)
        if not headers_row:
                workbook.close()
                return []

        normalized_headers = [normalize_excel_header(cell) for cell in headers_row]

        customers = []
        for values in rows_iter:
                if not values or not any(v not in (None, "") for v in values):
                        continue

                row = {}
                for index, header in enumerate(normalized_headers):
                        if not header:
                                continue
                        value = values[index] if index < len(values) else ""
                        row[header] = clean_excel_value(value)

                customers.append(
                        {
                                "name": get_excel_value(row, "name", "customer name"),
                                "age": get_excel_value(row, "age"),
                                "mobileNumber": get_excel_value(row, "mobile number", "mobile", "phone", "phone number"),
                                "carType": get_excel_value(row, "car type", "car"),
                                "carBrand": get_excel_value(row, "car brand", "brand"),
                                "carModel": get_excel_value(row, "car model", "model"),
                                "yearOfManufacture": get_excel_value(row, "year of manufacture", "year", "year of purchase"),
                                "PolicyNumber": get_excel_value(row, "policy number", "policynumber", "policy no"),
                                "policyExpiryDate": get_excel_value(row, "policy expiry date", "expiry date", "policyexpirydate"),
                        }
                )

        workbook.close()
        return customers


def append_new_customers_from_excel_bytes(excel_bytes):
        if not excel_bytes or load_workbook is None:
                return 0

        try:
                uploaded_customers = load_customers_from_excel_source(BytesIO(excel_bytes))
        except Exception:
                return 0

        lines = load_new_customer_lines()
        added_count = 0
        for customer in uploaded_customers:
                line = build_customer_line(customer)
                if line.strip("|"):
                        lines.append(line)
                        added_count += 1

        if added_count:
                save_new_customer_lines(lines)

        return added_count


def parse_multipart_form_data(body_bytes, content_type):
        if not body_bytes or "multipart/form-data" not in str(content_type or ""):
                return {}

        parser_input = (
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
                + body_bytes
        )
        message = BytesParser(policy=default).parsebytes(parser_input)
        if not message.is_multipart():
                return {}

        fields = {}
        for part in message.iter_parts():
                if part.get_content_disposition() != "form-data":
                        continue

                name = part.get_param("name", header="Content-Disposition")
                if not name:
                        continue

                filename = part.get_param("filename", header="Content-Disposition")
                payload = part.get_payload(decode=True) or b""

                if filename:
                        fields[name] = {
                                "filename": filename,
                                "content": payload,
                        }
                else:
                        charset = part.get_content_charset() or "utf-8"
                        fields[name] = payload.decode(charset, errors="ignore").strip()

        return fields


def load_new_customers_from_text():
        lines = load_new_customer_lines()
        if not lines:
                return []

        customers = []
        for line in lines:
                parts = [part.strip() for part in line.split("|")]
                customers.append(parse_customer_parts(parts))

        return customers


def get_customer_value(customer, key, *fallback_keys):
        for current_key in (key, *fallback_keys):
                if current_key in customer:
                        return customer.get(current_key, "")
        return ""


def render_customer_row(customer):
        return (
                f"<tr><td>{escape(str(get_customer_value(customer, 'name')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'age')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'mobileNumber', 'phoneNumber', 'phone')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'carType', 'car')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'carBrand')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'carModel')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'yearOfManufacture', 'yearOfPurchase', 'year')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'PolicyNumber', 'policyNumber', 'policy_number')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'policyExpiryDate', 'PolicyExpiryDate')))}</td></tr>"
        )


def sanitize_return_to(return_to):
        return return_to if return_to in ALLOWED_PAGE_PATHS else "/"


def render_row_actions(source, index, return_to, include_convert=False):
        safe_return_to = sanitize_return_to(return_to)
        edit_form = (
                f'<form class="action-form" method="get" action="{escape(safe_return_to)}" onsubmit="return confirm(\'Are you sure you want to edit this customer?\')">'
                f'<input type="hidden" name="edit_source" value="{escape(source)}" />'
                f'<input type="hidden" name="edit_index" value="{escape(str(index))}" />'
                '<button class="edit-btn icon-btn" type="submit" title="Edit" aria-label="Edit">'
                '<span class="btn-icon" aria-hidden="true">&#9998;</span>'
                '</button>'
                '</form>'
        )
        delete_form = (
                '<form class="action-form" method="post" action="/delete-customer" onsubmit="return confirm(\'Do you want to delete this customer?\')">'
                f'<input type="hidden" name="source" value="{escape(source)}" />'
                f'<input type="hidden" name="index" value="{escape(str(index))}" />'
                f'<input type="hidden" name="return_to" value="{escape(safe_return_to)}" />'
                '<button class="delete-btn icon-btn" type="submit" title="Delete" aria-label="Delete">'
                '<span class="btn-icon" aria-hidden="true">&#10005;</span>'
                '</button>'
                '</form>'
        )

        parts = [edit_form, delete_form]
        if include_convert:
                convert_form = (
                        '<form class="action-form" method="post" action="/convert-customer">'
                        f'<input type="hidden" name="index" value="{escape(str(index))}" />'
                        f'<input type="hidden" name="return_to" value="{escape(safe_return_to)}" />'
                        '<button class="convert-btn icon-btn" type="submit" title="Convert" aria-label="Convert">'
                        '<span class="btn-icon" aria-hidden="true">&#8644;</span>'
                        '</button>'
                        '</form>'
                )
                parts.insert(0, convert_form)
        return f'<div class="row-actions">{"".join(parts)}</div>'


def render_active_customer_row(customer, source, index, return_to):
        action_html = render_row_actions(source, index, return_to)
        return (
                f"<tr><td>{escape(str(get_customer_value(customer, 'name')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'age')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'mobileNumber', 'phoneNumber', 'phone')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'carType', 'car')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'carBrand')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'carModel')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'yearOfManufacture', 'yearOfPurchase', 'year')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'PolicyNumber', 'policyNumber', 'policy_number')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'policyExpiryDate', 'PolicyExpiryDate')))}</td>"
                f"<td>{action_html}</td></tr>"
        )


def render_new_customer_row(customer, index, return_to):
        action_html = render_row_actions("lead", index, return_to, include_convert=True)
        return (
                f"<tr><td>{escape(str(get_customer_value(customer, 'name')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'age')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'mobileNumber', 'phoneNumber', 'phone')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'carType', 'car')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'carBrand')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'carModel')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'yearOfManufacture', 'yearOfPurchase', 'year')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'PolicyNumber', 'policyNumber', 'policy_number')))}</td>"
                f"<td>{escape(str(get_customer_value(customer, 'policyExpiryDate', 'PolicyExpiryDate')))}</td>"
                f"<td>{action_html}</td></tr>"
        )


def build_edit_customer_card(source, index, customer, return_to):
        if customer is None:
                return ""

        safe_return_to = sanitize_return_to(return_to)

        return (
                '<div class="card">'
                '<h2>Edit Customer</h2>'
                '<form class="add-customer-form" method="post" action="/edit-customer" onsubmit="return confirm(\'Are you sure you want to edit this customer?\')">'
                f'<input type="hidden" name="source" value="{escape(source)}" />'
                f'<input type="hidden" name="index" value="{escape(str(index))}" />'
                f'<input type="hidden" name="return_to" value="{escape(safe_return_to)}" />'
                f'<label>Name <input type="text" name="name" value="{escape(str(get_customer_value(customer, "name")))}" required /></label>'
                f'<label>Age <input type="number" name="age" min="1" max="120" value="{escape(str(get_customer_value(customer, "age")))}" /></label>'
                f'<label>Mobile Number <input type="tel" name="mobileNumber" value="{escape(str(get_customer_value(customer, "mobileNumber", "phoneNumber", "phone")))}" required /></label>'
                f'<label>Car Type <input type="text" name="carType" value="{escape(str(get_customer_value(customer, "carType", "car")))}" /></label>'
                f'<label>Car Brand <input type="text" name="carBrand" value="{escape(str(get_customer_value(customer, "carBrand")))}" /></label>'
                f'<label>Car Model <input type="text" name="carModel" value="{escape(str(get_customer_value(customer, "carModel")))}" /></label>'
                f'<label>Year <input type="number" name="yearOfManufacture" min="1900" max="2100" value="{escape(str(get_customer_value(customer, "yearOfManufacture", "yearOfPurchase", "year")))}" /></label>'
                f'<label>Policy Number <input type="text" name="PolicyNumber" value="{escape(str(get_customer_value(customer, "PolicyNumber", "policyNumber", "policy_number")))}" required /></label>'
                f'<label>Policy Expiry Date <input type="date" name="policyExpiryDate" value="{escape(str(get_customer_value(customer, "policyExpiryDate", "PolicyExpiryDate")))}" required /></label>'
                '<button class="save-customer-btn" type="submit">Update Customer</button>'
                '</form>'
                '</div>'
        )


def build_add_customer_card(return_to):
        safe_return_to = sanitize_return_to(return_to)
        return (
                '<div class="card">'
                '<h2>Add Customer</h2>'
                '<button id="toggleAddForm" class="add-customer-btn" type="button">Add New Customer</button>'
                '<form id="addCustomerForm" class="add-customer-form is-hidden" method="post" action="/add-customer">'
                f'<input type="hidden" name="return_to" value="{escape(safe_return_to)}" />'
                '<label>Name <input type="text" name="name" required /></label>'
                '<label>Age <input type="number" name="age" min="1" max="120" /></label>'
                '<label>Mobile Number <input type="tel" name="mobileNumber" required /></label>'
                '<label>Car Type <input type="text" name="carType" /></label>'
                '<label>Car Brand <input type="text" name="carBrand" /></label>'
                '<label>Car Model <input type="text" name="carModel" /></label>'
                '<label>Year <input type="number" name="yearOfManufacture" min="1900" max="2100" /></label>'
                '<label>Policy Number <input type="text" name="PolicyNumber" required /></label>'
                '<label>Policy Expiry Date <input type="date" name="policyExpiryDate" required /></label>'
                '<button class="save-customer-btn" type="submit">Save Customer</button>'
                '</form>'
                '<hr class="section-divider" />'
                '<h2>Bulk Upload from Excel</h2>'
                '<form class="bulk-upload-form" method="post" action="/bulk-upload-customers" enctype="multipart/form-data">'
                f'<input type="hidden" name="return_to" value="{escape(safe_return_to)}" />'
                '<label>Excel File <input type="file" name="excel_file" accept=".xlsx,.xlsm,.xltx,.xltm" required /></label>'
                '<button class="save-customer-btn" type="submit">Upload Excel</button>'
                '</form>'
                '</div>'
        )


def build_send_quote_content(kind="quote"):
        config = SEND_LINK_KINDS[kind]
        script = (
                SEND_QUOTE_SCRIPT.replace("__API_PREFIX__", config["api_prefix"])
                .replace("__VIEW_LABEL__", config["view_label"])
        )
        return (
                '<div class="card">'
                f'<h2>{escape(config["page_title"])}</h2>'
                '<div class="send-quote-toolbar">'
                '<label class="file-upload-label" for="sendQuoteFile">Import Excel'
                '<input type="file" id="sendQuoteFile" accept=".xlsx,.xls,.csv" />'
                '</label>'
                '<span id="sendQuoteImportStatus" class="import-status"></span>'
                '</div>'
                '<p class="sender-note">Messages are sent from the WhatsApp account logged in on this device. Make sure you are logged in to WhatsApp (or WhatsApp Web) with the business number before sending.</p>'
                '<div id="sendQuoteImportError" class="form-error is-hidden"></div>'
                '</div>'
                '<div class="card">'
                '<div class="send-quote-controls">'
                '<div class="search-fields">'
                '<input type="text" id="searchName" placeholder="Search by Name" />'
                '<input type="text" id="searchVehicle" placeholder="Search by Vehicle Number" />'
                '<input type="text" id="searchMobile" placeholder="Search by Mobile Number" />'
                '</div>'
                '<div class="selection-bar">'
                '<span id="selectionCount">Selected: 0 Customers</span>'
                f'<button id="sendQuoteBtn" class="save-customer-btn" type="button" disabled>{escape(config["send_btn_label"])}</button>'
                '</div>'
                '</div>'
                '<div class="table-scroll">'
                '<table id="sendQuoteTable">'
                '<thead><tr>'
                '<th><input type="checkbox" id="selectAllCheckbox" aria-label="Select all" /></th>'
                '<th class="sortable-th" data-sort="name">Name</th>'
                '<th class="sortable-th" data-sort="mobileNumber">Mobile Number</th>'
                '<th class="sortable-th" data-sort="vehicleNumber">Vehicle Number</th>'
                '<th class="sortable-th" data-sort="expiryDate">Expiry Date</th>'
                f'<th>{escape(config["link_header"])}</th>'
                '<th class="sortable-th" data-sort="status">Status</th>'
                '</tr></thead>'
                '<tbody id="sendQuoteTableBody"><tr><td colspan="7">Import an Excel/CSV file to get started.</td></tr></tbody>'
                '</table>'
                '</div>'
                '<div class="pagination-bar">'
                '<button id="prevPageBtn" class="edit-btn" type="button">&laquo; Prev</button>'
                '<span id="pageIndicator">Page 1 of 1</span>'
                '<button id="nextPageBtn" class="edit-btn" type="button">Next &raquo;</button>'
                '</div>'
                '</div>'
                '<div id="confirmSendModal" class="modal-overlay is-hidden">'
                '<div class="modal-box">'
                '<h3 id="confirmSendText">Send WhatsApp messages?</h3>'
                '<div class="modal-actions">'
                '<button id="cancelSendBtn" class="delete-btn" type="button">Cancel</button>'
                '<button id="confirmSendBtn" class="save-customer-btn" type="button">Send</button>'
                '</div>'
                '</div>'
                '</div>'
                '<div id="sendProgressModal" class="modal-overlay is-hidden">'
                '<div class="modal-box">'
                '<h3>Sending...</h3>'
                '<div class="progress-bar-track"><div id="sendProgressBar" class="progress-bar-fill"></div></div>'
                '<p id="sendProgressText">0 / 0 Sent</p>'
                '<div id="sendQueueCurrent" class="send-queue-current"></div>'
                '<div class="modal-actions">'
                '<button id="closeProgressBtn" class="edit-btn" type="button">Done</button>'
                '</div>'
                '</div>'
                '</div>'
                '<div id="toastContainer" class="toast-container"></div>'
                '<script>' + script + '</script>'
        )


SEND_QUOTE_SCRIPT = r"""
(function () {
        var state = {
                rows: [],
                selectedIds: new Set(),
                page: 1,
                pageSize: 10,
                sortKey: null,
                sortDir: 1,
                queue: [],
                queueIndex: 0,
                sentCount: 0,
                failedCount: 0
        };

        var tableBody = document.getElementById("sendQuoteTableBody");
        var selectAllCheckbox = document.getElementById("selectAllCheckbox");
        var selectionCount = document.getElementById("selectionCount");
        var sendQuoteBtn = document.getElementById("sendQuoteBtn");
        var searchName = document.getElementById("searchName");
        var searchVehicle = document.getElementById("searchVehicle");
        var searchMobile = document.getElementById("searchMobile");
        var prevPageBtn = document.getElementById("prevPageBtn");
        var nextPageBtn = document.getElementById("nextPageBtn");
        var pageIndicator = document.getElementById("pageIndicator");
        var fileInput = document.getElementById("sendQuoteFile");
        var importStatus = document.getElementById("sendQuoteImportStatus");
        var importError = document.getElementById("sendQuoteImportError");
        var confirmSendModal = document.getElementById("confirmSendModal");
        var confirmSendText = document.getElementById("confirmSendText");
        var cancelSendBtn = document.getElementById("cancelSendBtn");
        var confirmSendBtn = document.getElementById("confirmSendBtn");
        var sendProgressModal = document.getElementById("sendProgressModal");
        var sendProgressBar = document.getElementById("sendProgressBar");
        var sendProgressText = document.getElementById("sendProgressText");
        var sendQueueCurrent = document.getElementById("sendQueueCurrent");
        var closeProgressBtn = document.getElementById("closeProgressBtn");
        var toastContainer = document.getElementById("toastContainer");

        function showToast(type, message) {
                var toast = document.createElement("div");
                toast.className = "toast toast-" + type;
                toast.textContent = message;
                toastContainer.appendChild(toast);
                setTimeout(function () {
                        toast.classList.add("toast-fade");
                        setTimeout(function () { toast.remove(); }, 400);
                }, 4000);
        }

        function escapeHtml(value) {
                var div = document.createElement("div");
                div.textContent = String(value == null ? "" : value);
                return div.innerHTML;
        }

        function statusClass(status) {
                return "status-badge status-" + String(status || "pending").toLowerCase();
        }

        function getFilteredRows() {
                var nameQuery = searchName.value.trim().toLowerCase();
                var vehicleQuery = searchVehicle.value.trim().toLowerCase();
                var mobileQuery = searchMobile.value.trim().toLowerCase();

                var filtered = state.rows.filter(function (row) {
                        if (nameQuery && row.name.toLowerCase().indexOf(nameQuery) === -1) return false;
                        if (vehicleQuery && row.vehicleNumber.toLowerCase().indexOf(vehicleQuery) === -1) return false;
                        if (mobileQuery && row.mobileNumber.toLowerCase().indexOf(mobileQuery) === -1) return false;
                        return true;
                });

                if (state.sortKey) {
                        var key = state.sortKey;
                        var dir = state.sortDir;
                        filtered.sort(function (a, b) {
                                var av = String(a[key] || "").toLowerCase();
                                var bv = String(b[key] || "").toLowerCase();
                                if (av < bv) return -1 * dir;
                                if (av > bv) return 1 * dir;
                                return 0;
                        });
                }

                return filtered;
        }

        function updateSelectionCount() {
                selectionCount.textContent = "Selected: " + state.selectedIds.size + " Customers";
                sendQuoteBtn.disabled = state.selectedIds.size === 0;
        }

        function render() {
                var filtered = getFilteredRows();
                var totalPages = Math.max(1, Math.ceil(filtered.length / state.pageSize));
                if (state.page > totalPages) state.page = totalPages;
                var startIndex = (state.page - 1) * state.pageSize;
                var pageRows = filtered.slice(startIndex, startIndex + state.pageSize);

                if (!filtered.length) {
                        tableBody.innerHTML = '<tr><td colspan="7">' + (state.rows.length ? "No matching rows." : "Import an Excel/CSV file to get started.") + '</td></tr>';
                } else {
                        tableBody.innerHTML = pageRows.map(function (row) {
                                var checked = state.selectedIds.has(row.id) ? "checked" : "";
                                var disabled = row.valid ? "" : "disabled";
                                var rowClass = row.valid ? "" : " class=\"invalid-row\"";
                                var title = row.valid ? "" : ' title="' + escapeHtml(row.issues.join(", ")) + '"';
                                return "<tr" + rowClass + title + ">" +
                                        '<td><input type="checkbox" class="row-checkbox" data-id="' + row.id + '" ' + checked + " " + disabled + " /></td>" +
                                        "<td>" + escapeHtml(row.name) + "</td>" +
                                        "<td>" + escapeHtml(row.mobileNumber) + "</td>" +
                                        "<td>" + escapeHtml(row.vehicleNumber) + "</td>" +
                                        "<td>" + escapeHtml(row.expiryDate) + "</td>" +
                                        '<td><a href="' + escapeHtml(row.quoteLink) + '" target="_blank" rel="noopener noreferrer">__VIEW_LABEL__</a></td>' +
                                        '<td><span class="' + statusClass(row.status) + '">' + escapeHtml(row.status) + "</span></td>" +
                                        "</tr>";
                        }).join("");
                }

                pageIndicator.textContent = "Page " + state.page + " of " + totalPages;
                prevPageBtn.disabled = state.page <= 1;
                nextPageBtn.disabled = state.page >= totalPages;

                var validVisibleIds = pageRows.filter(function (row) { return row.valid; }).map(function (row) { return row.id; });
                selectAllCheckbox.checked = validVisibleIds.length > 0 && validVisibleIds.every(function (id) { return state.selectedIds.has(id); });

                updateSelectionCount();
        }

        tableBody.addEventListener("change", function (event) {
                if (!event.target.classList.contains("row-checkbox")) return;
                var id = Number(event.target.getAttribute("data-id"));
                if (event.target.checked) {
                        state.selectedIds.add(id);
                } else {
                        state.selectedIds.delete(id);
                }
                updateSelectionCount();
                var box = event.target.closest("tr").querySelector(".row-checkbox");
        });

        selectAllCheckbox.addEventListener("change", function () {
                var filtered = getFilteredRows();
                var startIndex = (state.page - 1) * state.pageSize;
                var pageRows = filtered.slice(startIndex, startIndex + state.pageSize).filter(function (row) { return row.valid; });
                if (selectAllCheckbox.checked) {
                        pageRows.forEach(function (row) { state.selectedIds.add(row.id); });
                } else {
                        pageRows.forEach(function (row) { state.selectedIds.delete(row.id); });
                }
                render();
        });

        [searchName, searchVehicle, searchMobile].forEach(function (input) {
                input.addEventListener("input", function () {
                        state.page = 1;
                        render();
                });
        });

        document.querySelectorAll(".sortable-th").forEach(function (th) {
                th.addEventListener("click", function () {
                        var key = th.getAttribute("data-sort");
                        if (state.sortKey === key) {
                                state.sortDir *= -1;
                        } else {
                                state.sortKey = key;
                                state.sortDir = 1;
                        }
                        render();
                });
        });

        prevPageBtn.addEventListener("click", function () {
                if (state.page > 1) { state.page -= 1; render(); }
        });
        nextPageBtn.addEventListener("click", function () {
                state.page += 1;
                render();
        });

        fileInput.addEventListener("change", function () {
                var file = fileInput.files[0];
                if (!file) return;

                importError.classList.add("is-hidden");
                importStatus.textContent = "Importing " + file.name + "...";

                var formData = new FormData();
                formData.append("excel_file", file);

                fetch("__API_PREFIX__/import", { method: "POST", body: formData })
                        .then(function (response) { return response.json().then(function (data) { return { ok: response.ok, data: data }; }); })
                        .then(function (result) {
                                if (!result.ok || !result.data.success) {
                                        importStatus.textContent = "";
                                        importError.textContent = result.data.error || "Import failed.";
                                        importError.classList.remove("is-hidden");
                                        showToast("error", result.data.error || "Import failed.");
                                        return;
                                }
                                state.rows = result.data.rows;
                                state.selectedIds = new Set();
                                state.page = 1;
                                importStatus.textContent = result.data.total + " rows imported (" + result.data.invalidCount + " invalid).";
                                showToast("success", "Imported " + result.data.total + " rows.");
                                render();
                        })
                        .catch(function () {
                                importStatus.textContent = "";
                                importError.textContent = "Could not reach the server to import the file.";
                                importError.classList.remove("is-hidden");
                                showToast("error", "Import failed. Please try again.");
                        })
                        .finally(function () {
                                fileInput.value = "";
                        });
        });

        sendQuoteBtn.addEventListener("click", function () {
                confirmSendText.textContent = "Send WhatsApp messages to " + state.selectedIds.size + " selected customers?";
                confirmSendModal.classList.remove("is-hidden");
        });

        cancelSendBtn.addEventListener("click", function () {
                confirmSendModal.classList.add("is-hidden");
        });

        function renderQueueStep() {
                var total = state.queue.length;
                var processed = state.sentCount + state.failedCount;
                sendProgressBar.style.width = (total ? (processed / total) * 100 : 0) + "%";
                sendProgressText.textContent = processed + " / " + total + " Sent";

                if (state.queueIndex >= total) {
                        sendQueueCurrent.innerHTML = "<p><strong>All done.</strong> " + state.sentCount + " sent, " + state.failedCount + " failed.</p>";
                        showToast("success", state.sentCount + " quote(s) sent, " + state.failedCount + " failed.");
                        render();
                        return;
                }

                var row = state.queue[state.queueIndex];
                sendQueueCurrent.innerHTML =
                        "<p><strong>Sending to " + escapeHtml(row.name) + "</strong> &middot; " + escapeHtml(row.mobileNumber) + "...</p>" +
                        "<p>WhatsApp Web will open and send the message automatically. Please don't touch the keyboard or mouse.</p>";

                fetch("__API_PREFIX__/send", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ id: row.id })
                })
                        .then(function (response) { return response.json(); })
                        .then(function (data) {
                                if (!data.success && data.waLink) {
                                        // Automation unavailable (e.g. hosted server): open a
                                        // pre-filled WhatsApp chat instead; user presses Send.
                                        window.open(data.waLink, "_blank", "noopener");
                                        row.status = "Sent";
                                        state.sentCount += 1;
                                        showToast("success", row.name + ": WhatsApp opened - press Send in the new tab.");
                                        return fetch("__API_PREFIX__/status", {
                                                method: "POST",
                                                headers: { "Content-Type": "application/json" },
                                                body: JSON.stringify({ id: row.id, status: "Sent" })
                                        }).catch(function () {});
                                }
                                row.status = data.status || "Failed";
                                if (data.success) {
                                        state.sentCount += 1;
                                } else {
                                        state.failedCount += 1;
                                        if (data.error) showToast("error", row.name + ": " + data.error);
                                }
                        })
                        .catch(function () {
                                row.status = "Failed";
                                state.failedCount += 1;
                        })
                        .finally(function () {
                                state.queueIndex += 1;
                                render();
                                renderQueueStep();
                        });
        }

        confirmSendBtn.addEventListener("click", function () {
                confirmSendModal.classList.add("is-hidden");
                state.queue = state.rows.filter(function (row) { return row.valid && state.selectedIds.has(row.id); });
                state.queueIndex = 0;
                state.sentCount = 0;
                state.failedCount = 0;
                sendProgressModal.classList.remove("is-hidden");
                renderQueueStep();
        });

        closeProgressBtn.addEventListener("click", function () {
                sendProgressModal.classList.add("is-hidden");
        });

        fetch("__API_PREFIX__/rows")
                .then(function (response) { return response.json(); })
                .then(function (data) {
                        state.rows = data.rows || [];
                        render();
                })
                .catch(function () {});
})();
"""


def build_nav_class(active_path, target_path):
        return "menu-link active" if active_path == target_path else "menu-link"


def load_new_customers():
        return load_new_customers_from_text()


def get_filter_value(query_params, key):
        return extract_post_value(query_params, key).strip().lower()


def collect_filter_options(customers, key, *fallback_keys):
        values = {
                str(get_customer_value(customer, key, *fallback_keys)).strip()
                for customer in customers
                if str(get_customer_value(customer, key, *fallback_keys)).strip()
        }
        return sorted(values, key=str.lower)


def build_filter_option_tags(options, selected_value):
        tags = ['<option value="">All</option>']
        for value in options:
                selected_attr = ' selected' if value.lower() == selected_value else ''
                tags.append(f'<option value="{escape(value)}"{selected_attr}>{escape(value)}</option>')
        return "".join(tags)


def parse_policy_expiry(policy_expiry_text):
        if not policy_expiry_text:
                return None
        try:
                return datetime.strptime(str(policy_expiry_text), "%Y-%m-%d").date()
        except ValueError:
                return None


def normalize_mobile_number(number_text):
        raw = str(number_text or "")
        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) == 10:
                return f"91{digits}"
        return digits


def build_whatsapp_link(mobile_number, message):
        if not mobile_number:
                return ""
        return f"https://wa.me/{mobile_number}?text={quote_plus(message)}"


def build_call_link(number_text):
        digits = "".join(ch for ch in str(number_text or "") if ch.isdigit())
        if not digits:
                return ""
        return f"tel:{digits}"


def normalize_send_quote_mobile(text):
        digits = "".join(ch for ch in str(text or "") if ch.isdigit())
        if len(digits) == 12 and digits.startswith("91"):
                return digits
        if len(digits) == 10:
                return "91" + digits
        return ""


def parse_send_quote_expiry(text):
        raw = str(text or "").strip()
        if not raw:
                return None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y"):
                try:
                        return datetime.strptime(raw, fmt).date().isoformat()
                except ValueError:
                        continue
        return None


def is_valid_quote_link(text):
        raw = str(text or "").strip()
        return raw.startswith("http://") or raw.startswith("https://")


def build_send_quote_message(row, kind="quote"):
        config = SEND_LINK_KINDS[kind]
        return (
                f"Hello {row['name']},\n\n"
                f"Your motor insurance for vehicle {row['vehicleNumber']} is due to expire on {row['expiryDate']}.\n\n"
                f"{config['message_intro']}\n\n"
                "Please click the link below:\n"
                f"{row['quoteLink']}\n\n"
                "If you have any questions, feel free to contact us.\n\n"
                "Thank you,\nGravity Insurance"
        )


def send_whatsapp_quote(row, kind="quote"):
        """Send the quote/payment link via WhatsApp Web automation.

        Returns (ok, error_message, automation_available)."""
        try:
                import pywhatkit
        except Exception as exc:
                return False, f"Automated sending is not available on this server: {exc}", False

        try:
                pywhatkit.sendwhatmsg_instantly(
                        phone_no="+" + row["mobileNumber"],
                        message=build_send_quote_message(row, kind),
                        wait_time=25,
                        tab_close=True,
                        close_time=4,
                )
                return True, "", True
        except Exception as exc:
                return False, str(exc), True


def find_send_quote_column(normalized_headers, aliases):
        alias_keys = {normalize_excel_header(alias) for alias in aliases}
        for header in normalized_headers:
                if header in alias_keys:
                        return header
        return None


def parse_send_quote_upload(filename, file_bytes):
        extension = Path(str(filename or "")).suffix.lower()

        if extension == ".csv":
                try:
                        text = file_bytes.decode("utf-8-sig", errors="ignore")
                except Exception:
                        return None, None, "Could not read the CSV file."

                rows = list(csv.reader(text.splitlines()))
                if not rows:
                        return None, None, "The file is empty."

                normalized_headers = [normalize_excel_header(cell) for cell in rows[0]]
                raw_rows = []
                for values in rows[1:]:
                        if not any(str(value).strip() for value in values):
                                continue
                        row = {}
                        for index, header in enumerate(normalized_headers):
                                if not header:
                                        continue
                                value = values[index] if index < len(values) else ""
                                row[header] = str(value).strip()
                        raw_rows.append(row)
                return normalized_headers, raw_rows, None

        if extension in (".xlsx", ".xlsm"):
                if load_workbook is None:
                        return None, None, "Excel support is not available on the server (openpyxl is not installed)."
                try:
                        workbook = load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
                except Exception:
                        return None, None, "Could not read the Excel file. Make sure it is a valid .xlsx file."

                sheet = workbook.active
                rows_iter = sheet.iter_rows(values_only=True)
                header_row = next(rows_iter, None)
                if not header_row:
                        workbook.close()
                        return None, None, "The file is empty."

                normalized_headers = [normalize_excel_header(cell) for cell in header_row]
                raw_rows = []
                for values in rows_iter:
                        if not values or not any(v not in (None, "") for v in values):
                                continue
                        row = {}
                        for index, header in enumerate(normalized_headers):
                                if not header:
                                        continue
                                value = values[index] if index < len(values) else ""
                                row[header] = clean_excel_value(value)
                        raw_rows.append(row)
                workbook.close()
                return normalized_headers, raw_rows, None

        if extension == ".xls":
                return None, None, "Legacy .xls files are not supported. Please save the file as .xlsx or .csv and try again."

        return None, None, "Unsupported file type. Please upload a .xlsx, .xls, or .csv file."


def build_send_quote_rows(raw_rows, column_keys, kind="quote"):
        seen_keys = set()
        result_rows = []
        next_id_holder = SEND_LINK_NEXT_ID[kind]

        for raw in raw_rows:
                name = str(raw.get(column_keys["name"], "")).strip()
                mobile_raw = str(raw.get(column_keys["mobileNumber"], "")).strip()
                vehicle = str(raw.get(column_keys["vehicleNumber"], "")).strip()
                expiry_raw = str(raw.get(column_keys["expiryDate"], "")).strip()
                quote_link = str(raw.get(column_keys["quoteLink"], "")).strip()

                if not any([name, mobile_raw, vehicle, expiry_raw, quote_link]):
                        continue

                issues = []
                if not name:
                        issues.append("Name is required")

                mobile = normalize_send_quote_mobile(mobile_raw)
                if not mobile:
                        issues.append("Invalid mobile number")

                if not vehicle:
                        issues.append("Vehicle number is required")

                expiry_iso = parse_send_quote_expiry(expiry_raw)
                if not expiry_iso:
                        issues.append("Invalid expiry date")

                if not is_valid_quote_link(quote_link):
                        issues.append(f"Invalid {SEND_LINK_KINDS[kind]['link_header'].lower()} URL")

                dedupe_key = (mobile, vehicle.strip().lower())
                if mobile and vehicle and dedupe_key in seen_keys:
                        issues.append("Duplicate row")
                else:
                        seen_keys.add(dedupe_key)

                row_id = next_id_holder[0]
                next_id_holder[0] += 1

                result_rows.append(
                        {
                                "id": row_id,
                                "name": name,
                                "mobileNumber": mobile or mobile_raw,
                                "vehicleNumber": vehicle,
                                "expiryDate": expiry_iso or expiry_raw,
                                "quoteLink": quote_link,
                                "status": "Pending",
                                "valid": len(issues) == 0,
                                "issues": issues,
                        }
                )

        return result_rows


def render_expiry_alert_rows(customers, today):
        reminder_rows_list = []
        for c in customers:
                policy_number = str(c.get("PolicyNumber", c.get("policyNumber", c.get("policy_number", ""))))
                expiry_text = str(c.get("policyExpiryDate", c.get("PolicyExpiryDate", "")))
                expiry_date = parse_policy_expiry(expiry_text)
                if not expiry_date:
                        continue

                days_left = (expiry_date - today).days
                if 0 <= days_left <= 30:
                        name = str(c.get("name", "Customer"))
                        mobile = str(c.get("mobileNumber", c.get("phoneNumber", c.get("phone", ""))))
                        whatsapp_number = normalize_mobile_number(mobile)
                        call_link = build_call_link(mobile)
                        message = (
                                f"Hello {name}, your insurance policy {policy_number} is expiring on "
                                f"{expiry_text} ({days_left} days left). Please renew your policy."
                        )
                        whatsapp_link = build_whatsapp_link(whatsapp_number, message)
                        action_links = []
                        if whatsapp_link:
                                action_links.append(
                                        f'<a class="wa-link" href="{escape(whatsapp_link)}" target="_blank" rel="noopener noreferrer">WhatsApp</a>'
                                )
                        if call_link:
                                action_links.append(
                                        f'<a class="call-link" href="{escape(call_link)}">Call</a>'
                                )

                        action_html = (
                                f'<div class="action-links">{"".join(action_links)}</div>'
                                if action_links
                                else "No mobile"
                        )

                        reminder_rows_list.append(
                                (
                                        days_left,
                                        f"<tr><td>{escape(name)}</td><td>{escape(policy_number)}</td><td>{escape(expiry_text)}</td><td>{escape(str(days_left))}</td><td>{action_html}</td></tr>",
                                )
                        )

        return reminder_rows_list


def to_int(value, default=0):
        try:
                return int(str(value).strip())
        except (TypeError, ValueError):
                return default


def format_inr(amount):
        return f"INR {amount:,.0f}"


def months_from_now(base_date, offset):
        month = base_date.month + offset
        year = base_date.year
        while month > 12:
                month -= 12
                year += 1
        while month <= 0:
                month += 12
                year -= 1
        return year, month


def infer_insurance_company(policy_number):
        text = str(policy_number or "").upper()
        prefix = "".join(ch for ch in text if ch.isalpha())[:3]
        mapping = {
                "ROY": "Royal Sundaram",
                "TAT": "TATA AIG",
                "TAI": "TATA AIG",
                "HDF": "HDFC ERGO",
                "ICI": "ICICI Lombard",
                "BAJ": "Bajaj Allianz",
                "REL": "Reliance General",
                "SBI": "SBI General",
                "CHO": "Chola MS",
                "TOK": "Tokio Marine",
        }
        if prefix in mapping:
                return mapping[prefix]
        if text.startswith("123"):
                return "Royal Sundaram"
        if text.startswith("098"):
                return "TATA AIG"
        if text.startswith("112"):
                return "HDFC ERGO"
        if text.startswith("556"):
                return "Bajaj Allianz"
        return "Other Insurer"


def classify_vehicle_segment(car_type):
        value = str(car_type or "").strip().lower()
        if value in {"car", "hatchback", "sedan", "suv", "muv", "crossover", "coupe"}:
                return "Cars"
        if "commercial" in value or value in {"van", "pickup", "tempo", "mini truck"}:
                return "Commercial Vehicles"
        if "bus" in value:
                return "Buses"
        if value in {"lorry", "truck"} or "lorry" in value or "truck" in value:
                return "Lorry"
        return "Others"


def build_dashboard_content(customers, xl_customers, new_customers, reminder_rows_list, today):
        active_clients = customers + xl_customers

        segment_monthly_revenue = {
                "Cars": 2200,
                "Commercial Vehicles": 4500,
                "Buses": 7000,
                "Lorry": 6500,
                "Others": 2800,
        }
        estimated_monthly_revenue = 0
        for customer in active_clients:
                segment = classify_vehicle_segment(customer.get("carType", customer.get("car", "")))
                estimated_monthly_revenue += segment_monthly_revenue.get(segment, 2800)

        def customer_premium(customer):
                premium_keys = ("premium", "Premium", "monthlyPremium", "policyPremium", "premiumAmount")
                for key in premium_keys:
                        value = customer.get(key)
                        if value not in (None, ""):
                                parsed = to_int(value, -1)
                                if parsed > 0:
                                        return parsed
                segment = classify_vehicle_segment(customer.get("carType", customer.get("car", "")))
                return segment_monthly_revenue.get(segment, 2800)

        month_labels = []
        month_revenues = []
        month_factors = [0.76, 0.84, 0.9, 0.98, 1.06, 1.14]
        current_year = today.year
        current_month = today.month
        for index, factor in enumerate(month_factors):
                month_value = current_month - (5 - index)
                year_value = current_year
                while month_value <= 0:
                        month_value += 12
                        year_value -= 1
                month_start = datetime(year_value, month_value, 1).date()
                month_labels.append(month_start.strftime("%b %Y"))
                month_revenues.append(int(round(estimated_monthly_revenue * factor)))

        max_revenue = max(month_revenues) if month_revenues else 1
        revenue_bars = "".join(
                (
                        '<div class="revenue-item">'
                        f'<p class="revenue-value">{format_inr(value)}</p>'
                        f'<div class="revenue-bar" style="height: {max(10, int((value / max_revenue) * 100))}%;"></div>'
                        f'<p class="revenue-label">{escape(label)}</p>'
                        '</div>'
                )
                for label, value in zip(month_labels, month_revenues)
        )

        projection_labels = []
        projection_values = []
        for offset in range(1, 7):
                year_value, month_value = months_from_now(today, offset)
                month_start = datetime(year_value, month_value, 1).date()
                projection_labels.append(month_start.strftime("%b %Y"))

                projected_total = 0
                for customer in active_clients:
                        expiry_text = str(customer.get("policyExpiryDate", customer.get("PolicyExpiryDate", "")))
                        expiry_date = parse_policy_expiry(expiry_text)
                        if not expiry_date:
                                continue
                        if expiry_date.year == year_value and expiry_date.month == month_value:
                                projected_total += customer_premium(customer)
                projection_values.append(projected_total)

        projection_max = max(projection_values) if max(projection_values, default=0) > 0 else 1
        projection_bars = "".join(
                (
                        '<div class="revenue-item">'
                        f'<p class="revenue-value">{format_inr(value)}</p>'
                        f'<div class="revenue-bar projection-bar" style="height: {max(10, int((value / projection_max) * 100))}%;"></div>'
                        f'<p class="revenue-label">{escape(label)}</p>'
                        '</div>'
                )
                for label, value in zip(projection_labels, projection_values)
        )

        insurer_counts = Counter(
                infer_insurance_company(
                        customer.get("PolicyNumber", customer.get("policyNumber", customer.get("policy_number", "")))
                )
                for customer in active_clients
        )

        vehicle_counts = Counter(
                classify_vehicle_segment(customer.get("carType", customer.get("car", "")))
                for customer in active_clients
        )

        expiring_this_month = 0
        for customer in active_clients:
                expiry_text = str(customer.get("policyExpiryDate", customer.get("PolicyExpiryDate", "")))
                expiry_date = parse_policy_expiry(expiry_text)
                if expiry_date and expiry_date.month == today.month and expiry_date.year == today.year:
                        expiring_this_month += 1

        ages = [
                to_int(customer.get("age", ""), -1)
                for customer in active_clients
        ]
        valid_ages = [age for age in ages if age > 0]
        average_age = (sum(valid_ages) / len(valid_ages)) if valid_ages else 0

        contactable_clients = sum(
                1
                for customer in active_clients
                if "".join(ch for ch in str(customer.get("mobileNumber", customer.get("phone", ""))) if ch.isdigit())
        )

        insurer_rows = "".join(
                f"<tr><td>{escape(company)}</td><td>{escape(str(count))}</td></tr>"
                for company, count in sorted(insurer_counts.items(), key=lambda item: (-item[1], item[0]))
        )
        if not insurer_rows:
                insurer_rows = '<tr><td colspan="2">No insurer data</td></tr>'

        segment_order = ["Cars", "Commercial Vehicles", "Buses", "Lorry", "Others"]
        vehicle_rows = "".join(
                f"<tr><td>{segment}</td><td>{vehicle_counts.get(segment, 0)}</td></tr>"
                for segment in segment_order
        )

        return (
                '<div class="card">'
                '<h2>Dashboard</h2>'
                '<div class="dashboard-grid">'
                f'<div class="stat-tile"><p>Total Leads</p><strong>{len(new_customers)}</strong></div>'
                f'<div class="stat-tile"><p>Total Active Clients</p><strong>{len(active_clients)}</strong></div>'
                f'<div class="stat-tile"><p>Expiry Alerts</p><strong>{len(reminder_rows_list)}</strong></div>'
                '</div>'
                '</div>'
                '<div class="card">'
                '<h2>Monthly Revenue - Last 6 Months</h2>'
                '<div class="revenue-chart">'
                f'{revenue_bars}'
                '</div>'
                '</div>'
                '<div class="card">'
                '<h2>Projected Monthly Revenue - Next 6 Months</h2>'
                '<p class="projection-note">Based on expiring client premium (or segment premium when missing).</p>'
                '<div class="revenue-chart">'
                f'{projection_bars}'
                '</div>'
                '</div>'
                '<div class="insights-grid">'
                '<div class="card">'
                '<h2>Customers by Insurance Company</h2>'
                '<table class="mini-table">'
                '<thead><tr><th>Insurance Company</th><th>Customers</th></tr></thead>'
                f'<tbody>{insurer_rows}</tbody>'
                '</table>'
                '</div>'
                '<div class="card">'
                '<h2>Vehicle Mix</h2>'
                '<table class="mini-table">'
                '<thead><tr><th>Segment</th><th>Count</th></tr></thead>'
                f'<tbody>{vehicle_rows}</tbody>'
                '</table>'
                '</div>'
                '<div class="card">'
                '<h2>Operations Snapshot</h2>'
                '<table class="mini-table">'
                '<thead><tr><th>Metric</th><th>Value</th></tr></thead>'
                '<tbody>'
                f'<tr><td>Revenue (Current Month Estimate)</td><td>{format_inr(estimated_monthly_revenue)}</td></tr>'
                f'<tr><td>Expiring This Month</td><td>{expiring_this_month}</td></tr>'
                f'<tr><td>Contactable Clients</td><td>{contactable_clients}</td></tr>'
                f'<tr><td>Average Client Age</td><td>{average_age:.1f}</td></tr>'
                '</tbody>'
                '</table>'
                '</div>'
                '</div>'
        )


def perform_form_action(path, post_data):
        return_to = sanitize_return_to(extract_post_value(post_data, "return_to"))

        if path == "/add-customer":
                new_customer = get_customer_from_post(post_data)
                append_new_customer(new_customer)

        if path == "/convert-customer":
                try:
                        index = int(extract_post_value(post_data, "index"))
                except ValueError:
                        index = -1
                convert_new_customer_to_existing(index)

        if path == "/delete-customer":
                source = extract_post_value(post_data, "source")
                try:
                        index = int(extract_post_value(post_data, "index"))
                except ValueError:
                        index = -1
                delete_customer_by_source(source, index)

        if path == "/edit-customer":
                source = extract_post_value(post_data, "source")
                try:
                        index = int(extract_post_value(post_data, "index"))
                except ValueError:
                        index = -1
                updated_customer = get_customer_from_post(post_data)
                update_customer_by_source(source, index, updated_customer)

        return return_to


def import_send_quote_file(filename, file_bytes, kind="quote"):
        """Parse an uploaded send-quote/send-payment-link file and replace the
        current batch for that kind.

        Returns (payload, http_status) ready for a JSON response."""
        normalized_headers, raw_rows, error = parse_send_quote_upload(filename, file_bytes)
        if error:
                return {"success": False, "error": error}, 400

        missing_columns = []
        column_keys = {}
        for field_key, display_name, aliases in get_send_link_columns(kind):
                found = find_send_quote_column(normalized_headers, aliases)
                if found is None:
                        missing_columns.append(display_name)
                else:
                        column_keys[field_key] = found

        if missing_columns:
                return {
                        "success": False,
                        "error": "Missing required column(s): " + ", ".join(missing_columns),
                }, 400

        rows = build_send_quote_rows(raw_rows, column_keys, kind)
        batch = SEND_LINK_BATCHES[kind]
        batch.clear()
        batch.extend(rows)

        invalid_count = sum(1 for row in rows if not row["valid"])
        return {"success": True, "rows": rows, "total": len(rows), "invalidCount": invalid_count}, 200


def set_send_quote_status(row_id, new_status, kind="quote"):
        """Returns (payload, http_status)."""
        if new_status not in SEND_QUOTE_STATUSES:
                return {"success": False, "error": "Invalid status."}, 400
        for row in SEND_LINK_BATCHES[kind]:
                if row["id"] == row_id:
                        row["status"] = new_status
                        return {"success": True}, 200
        return {"success": False, "error": "Row not found."}, 404


def send_quote_for_row(row_id, kind="quote"):
        """Attempt automated send for one row. Returns (payload, http_status).

        When automation is unavailable (e.g. hosted server with no browser),
        the payload includes a wa.me fallback link the frontend can open."""
        row = next((r for r in SEND_LINK_BATCHES[kind] if r["id"] == row_id), None)
        if row is None:
                return {"success": False, "error": "Row not found."}, 404
        if not row["valid"]:
                row["status"] = "Failed"
                return {"success": False, "status": "Failed", "error": "Row has validation issues."}, 200

        ok, error, automation_available = send_whatsapp_quote(row, kind)
        row["status"] = "Sent" if ok else "Failed"
        payload = {"success": ok, "status": row["status"], "error": error}
        if not ok and not automation_available:
                payload["waLink"] = build_whatsapp_link(
                        row["mobileNumber"], build_send_quote_message(row, kind)
                )
        return payload, 200


class AppHandler(BaseHTTPRequestHandler):
        def get_current_user(self):
                cookies = auth.parse_cookie_header(self.headers.get("Cookie", ""))
                return auth.get_session_user(cookies.get(auth.SESSION_COOKIE_NAME))

        def send_redirect(self, location, set_cookie=None):
                self.send_response(303)
                self.send_header("Location", location)
                if set_cookie:
                        self.send_header("Set-Cookie", set_cookie)
                self.end_headers()

        def send_html_response(self, html, status_code=200):
                body = html.encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def read_post_form(self):
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(content_length).decode("utf-8", errors="ignore")
                return parse_qs(body)

        def send_json_response(self, status_code, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def handle_send_quote_import(self, kind):
                content_length = int(self.headers.get("Content-Length", "0"))
                content_type = self.headers.get("Content-Type", "")
                body_bytes = self.rfile.read(content_length)
                form_fields = parse_multipart_form_data(body_bytes, content_type)

                upload_item = form_fields.get("excel_file")
                if not isinstance(upload_item, dict):
                        self.send_json_response(400, {"success": False, "error": "No file was uploaded."})
                        return

                filename = upload_item.get("filename", "")
                file_bytes = upload_item.get("content", b"")
                payload, status_code = import_send_quote_file(filename, file_bytes, kind)
                self.send_json_response(status_code, payload)

        def handle_send_quote_status(self, kind):
                content_length = int(self.headers.get("Content-Length", "0"))
                body_bytes = self.rfile.read(content_length)
                try:
                        payload = json.loads(body_bytes.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                        self.send_json_response(400, {"success": False, "error": "Invalid request."})
                        return

                response_payload, status_code = set_send_quote_status(
                        payload.get("id"), str(payload.get("status", "")).strip(), kind
                )
                self.send_json_response(status_code, response_payload)

        def handle_send_quote_send(self, kind):
                content_length = int(self.headers.get("Content-Length", "0"))
                body_bytes = self.rfile.read(content_length)
                try:
                        payload = json.loads(body_bytes.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                        self.send_json_response(400, {"success": False, "error": "Invalid request."})
                        return

                response_payload, status_code = send_quote_for_row(payload.get("id"), kind)
                self.send_json_response(status_code, response_payload)

        def do_POST(self):
                if self.path == "/login":
                        post_data = self.read_post_form()
                        username = extract_post_value(post_data, "username")
                        password = extract_post_value(post_data, "password")
                        user, error = auth.authenticate(username, password)
                        if user is None:
                                self.send_html_response(auth.render_login_page(error=error))
                                return
                        token = auth.create_session_token(user["username"])
                        self.send_redirect("/", set_cookie=auth.build_session_cookie(token))
                        return

                if self.path == "/register":
                        post_data = self.read_post_form()
                        ok, error = auth.register_user(
                                extract_post_value(post_data, "username"),
                                extract_post_value(post_data, "password"),
                                extract_post_value(post_data, "fullName"),
                                extract_post_value(post_data, "mobileNumber"),
                        )
                        if not ok:
                                self.send_html_response(auth.render_register_page(error=error))
                                return
                        self.send_html_response(
                                auth.render_login_page(message="Registration received. You can log in after an admin approves your account.")
                        )
                        return

                user = self.get_current_user()
                if user is None:
                        self.send_redirect("/login")
                        return

                if self.path == "/logout":
                        self.send_redirect("/login", set_cookie=auth.build_logout_cookie())
                        return

                if self.path == "/admin/user-action":
                        if user.get("role") != "admin":
                                self.send_error(403, "Admin access required")
                                return
                        post_data = self.read_post_form()
                        target = extract_post_value(post_data, "username")
                        action = extract_post_value(post_data, "action")
                        if action == "approve":
                                auth.set_user_status(target, "approved")
                        elif action == "reject":
                                auth.set_user_status(target, "rejected")
                        elif action == "delete":
                                auth.delete_user(target)
                        self.send_redirect("/admin/users")
                        return

                send_link_action = get_send_link_api_action(self.path)
                if send_link_action is not None:
                        kind, action = send_link_action
                        if action == "import":
                                self.handle_send_quote_import(kind)
                        elif action == "status":
                                self.handle_send_quote_status(kind)
                        elif action == "send":
                                self.handle_send_quote_send(kind)
                        return

                if self.path not in ("/add-customer", "/convert-customer", "/delete-customer", "/edit-customer", "/bulk-upload-customers"):
                        self.send_error(404, "Page not found")
                        return

                content_length = int(self.headers.get("Content-Length", "0"))

                if self.path == "/bulk-upload-customers":
                        return_to = "/leads"
                        content_type = self.headers.get("Content-Type", "")
                        if "multipart/form-data" in content_type:
                                body_bytes = self.rfile.read(content_length)
                                form_fields = parse_multipart_form_data(body_bytes, content_type)
                                return_to = sanitize_return_to(
                                        sanitize_form_value(form_fields.get("return_to", "/leads"))
                                )

                                upload_item = form_fields.get("excel_file")
                                if isinstance(upload_item, dict):
                                        excel_bytes = upload_item.get("content", b"")
                                        append_new_customers_from_excel_bytes(excel_bytes)

                        self.send_response(303)
                        self.send_header("Location", return_to)
                        self.end_headers()
                        return

                body = self.rfile.read(content_length).decode("utf-8", errors="ignore")
                return_to = perform_form_action(self.path, parse_qs(body))

                self.send_response(303)
                self.send_header("Location", return_to)
                self.end_headers()

        def do_GET(self):
                parsed = urlparse(self.path)

                if parsed.path == "/style.css":
                        css = STYLE_PATH.read_text(encoding="utf-8")
                        self.send_response(200)
                        self.send_header("Content-type", "text/css; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(css.encode("utf-8"))
                        return

                user = self.get_current_user()

                if parsed.path == "/login":
                        if user is not None:
                                self.send_redirect("/")
                                return
                        self.send_html_response(auth.render_login_page())
                        return

                if parsed.path == "/register":
                        self.send_html_response(auth.render_register_page())
                        return

                if parsed.path == "/logout":
                        self.send_redirect("/login", set_cookie=auth.build_logout_cookie())
                        return

                if user is None:
                        self.send_redirect("/login")
                        return

                if parsed.path == "/admin/users":
                        if user.get("role") != "admin":
                                self.send_error(403, "Admin access required")
                                return
                        self.send_html_response(render_admin_users_page(user))
                        return

                send_link_action = get_send_link_api_action(parsed.path)
                if send_link_action is not None:
                        kind, action = send_link_action
                        if action == "rows":
                                self.send_json_response(200, {"rows": SEND_LINK_BATCHES[kind]})
                        else:
                                self.send_error(404, "Page not found")
                        return

                current_path = parsed.path
                if current_path not in ALLOWED_PAGE_PATHS:
                        self.send_error(404, "Page not found")
                        return

                self.send_html_response(render_page(current_path, parse_qs(parsed.query), user))


def build_auth_nav_links(user):
        """Returns (users_link_html, auth_link_html) for the top menu."""
        if user is None:
                return "", '<a class="menu-link" href="/login">Login</a>'
        users_link = ""
        if user.get("role") == "admin":
                users_link = '<a class="menu-link" href="/admin/users">Users</a>'
        display_name = escape(str(user.get("fullName") or user.get("username", "")))
        auth_link = (
                f'<a class="menu-link logout-link" href="/logout" '
                f'onclick="return confirm(\'Log out?\')">Logout ({display_name})</a>'
        )
        return users_link, auth_link


def render_with_template(page_content, user=None, active_path=""):
        users_link, auth_link = build_auth_nav_links(user)
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        return (
                template.replace("{{NAV_HOME_CLASS}}", build_nav_class(active_path, "/"))
                .replace("{{NAV_LEADS_CLASS}}", build_nav_class(active_path, "/leads"))
                .replace("{{NAV_ACTIVE_CLASS}}", build_nav_class(active_path, "/active-clients"))
                .replace("{{NAV_EXPIRY_CLASS}}", build_nav_class(active_path, "/expiry-alerts"))
                .replace("{{NAV_SENDQUOTE_CLASS}}", build_nav_class(active_path, "/send-quote"))
                .replace("{{NAV_SENDPAYMENT_CLASS}}", build_nav_class(active_path, "/send-payment-link"))
                .replace("{{NAV_USERS_LINK}}", users_link)
                .replace("{{NAV_AUTH_LINK}}", auth_link)
                .replace("{{PAGE_CONTENT}}", page_content)
        )


def render_admin_users_page(user):
        return render_with_template(auth.build_users_admin_content(), user, "/admin/users")


def render_page(current_path, query_params, user=None):
        edit_source = extract_post_value(query_params, "edit_source")
        try:
                edit_index = int(extract_post_value(query_params, "edit_index"))
        except ValueError:
                edit_index = -1
        edit_customer = get_edit_customer(edit_source, edit_index)
        edit_card = build_edit_customer_card(edit_source, edit_index, edit_customer, current_path)

        customers = load_customers()
        xl_customers = load_new_customers_from_excel()
        new_customers = load_new_customers()
        today = date.today()

        active_clients = customers + xl_customers
        active_clients_with_source = []
        active_clients_with_source.extend(
                (customer, "active_json", index)
                for index, customer in enumerate(customers)
        )
        active_clients_with_source.extend(
                (customer, "active_xl", index)
                for index, customer in enumerate(xl_customers)
        )

        car_type_filter = get_filter_value(query_params, "car_type")
        car_brand_filter = get_filter_value(query_params, "car_brand")
        car_model_filter = get_filter_value(query_params, "car_model")

        filtered_active_clients_with_source = []
        for customer, source, index in active_clients_with_source:
                car_type_value = str(get_customer_value(customer, "carType", "car")).strip().lower()
                car_brand_value = str(get_customer_value(customer, "carBrand")).strip().lower()
                car_model_value = str(get_customer_value(customer, "carModel")).strip().lower()

                if car_type_filter and car_type_value != car_type_filter:
                        continue
                if car_brand_filter and car_brand_value != car_brand_filter:
                        continue
                if car_model_filter and car_model_value != car_model_filter:
                        continue
                filtered_active_clients_with_source.append((customer, source, index))

        car_type_options = collect_filter_options(active_clients, "carType", "car")
        car_brand_options = collect_filter_options(active_clients, "carBrand")
        car_model_options = collect_filter_options(active_clients, "carModel")

        active_filter_card = (
                '<div class="card">'
                '<h2>Filter Active Client</h2>'
                '<form class="filter-form" method="get" action="/active-clients">'
                '<label>Car Type'
                f'<select name="car_type">{build_filter_option_tags(car_type_options, car_type_filter)}</select>'
                '</label>'
                '<label>Car Brand'
                f'<select name="car_brand">{build_filter_option_tags(car_brand_options, car_brand_filter)}</select>'
                '</label>'
                '<label>Model'
                f'<select name="car_model">{build_filter_option_tags(car_model_options, car_model_filter)}</select>'
                '</label>'
                '<div class="filter-actions">'
                '<button class="save-customer-btn" type="submit">Apply Filter</button>'
                '<a class="menu-link" href="/active-clients">Clear</a>'
                '</div>'
                '</form>'
                '</div>'
        )

        customer_rows_list = []
        customer_rows_list.extend(
                render_active_customer_row(customer, source, index, "/active-clients")
                for customer, source, index in filtered_active_clients_with_source
        )
        customer_rows = "".join(customer_rows_list)
        if not customer_rows:
                customer_rows = '<tr><td colspan="10">No active clients available.</td></tr>'

        new_customer_rows = "".join(
                render_new_customer_row(customer, index, "/leads")
                for index, customer in enumerate(new_customers)
        )
        if not new_customer_rows:
                new_customer_rows = '<tr><td colspan="10">No leads available.</td></tr>'

        reminder_rows_list = render_expiry_alert_rows(customers, today)
        reminder_rows_list.extend(render_expiry_alert_rows(xl_customers, today))
        reminder_rows_list.sort(key=lambda item: item[0])

        reminder_rows = "".join(item[1] for item in reminder_rows_list)
        if not reminder_rows:
                reminder_rows = '<tr><td colspan="5">No policies expiring in next 30 days.</td></tr>'

        dashboard_content = build_dashboard_content(customers, xl_customers, new_customers, reminder_rows_list, today)

        leads_content = (
                build_add_customer_card("/leads")
                + edit_card
                + '<div class="card"><h2>Leads</h2><table>'
                '<thead><tr><th>Name</th><th>Age</th><th>Mobile Number</th><th>Car Type</th><th>Car Brand</th><th>Car Model</th><th>Year</th><th>Policy Number</th><th>Policy Expiry Date</th><th>Action</th></tr></thead>'
                f'<tbody>{new_customer_rows}</tbody>'
                '</table></div>'
        )

        active_content = (
                edit_card
                + active_filter_card
                + '<div class="card"><h2>Active Client</h2><div class="table-scroll"><table>'
                '<thead><tr><th>Name</th><th>Age</th><th>Mobile Number</th><th>Car Type</th><th>Car Brand</th><th>Car Model</th><th>Year</th><th>Policy Number</th><th>Policy Expiry Date</th><th>Action</th></tr></thead>'
                f'<tbody>{customer_rows}</tbody>'
                '</table></div></div>'
        )

        expiry_content = (
                '<div class="card"><h2>Expiry Alerts</h2><table>'
                '<thead><tr><th>Name</th><th>Policy Number</th><th>Expiry Date</th><th>Days Left</th><th>Action</th></tr></thead>'
                f'<tbody>{reminder_rows}</tbody>'
                '</table></div>'
        )

        page_content = dashboard_content
        if current_path == "/leads":
                page_content = leads_content
        if current_path == "/active-clients":
                page_content = active_content
        if current_path == "/expiry-alerts":
                page_content = expiry_content
        if current_path == "/send-quote":
                page_content = build_send_quote_content("quote")
        if current_path == "/send-payment-link":
                page_content = build_send_quote_content("payment")

        return render_with_template(page_content, user, current_path)


if __name__ == "__main__":
        host, port = "127.0.0.1", 8000
        print(f"Server running at http://{host}:{port}")
        ThreadingHTTPServer((host, port), AppHandler).serve_forever()