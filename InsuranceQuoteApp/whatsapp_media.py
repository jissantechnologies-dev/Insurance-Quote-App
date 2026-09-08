"""Uploading media to the WhatsApp Cloud API and sending it as a template.

Sending an image is two calls, not one. The PNG is uploaded to the media
endpoint first, which returns an id; that id then goes in the header component
of a template message. The id is good for 30 days but is tied to the phone
number that uploaded it, so it cannot be shared between environments.

Only the transport lives here - no app config, no customer data - so the same
helpers work for the quote PNG and for anything sent later. Callers pass the
token and phone number id in; main.py owns where those come from.
"""

import json
import mimetypes
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GRAPH_HOST = "https://graph.facebook.com"

# Meta caps image media at 5 MB.
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def describe_error(exc):
        """Turn a Graph API HTTPError into a message that names the numeric code.

        Meta's prose ("API access blocked.") is shared by several unrelated
        restrictions, so the code is what actually identifies the problem."""
        try:
                detail = json.loads(exc.read().decode("utf-8"))
        except Exception:
                return f"WhatsApp API returned HTTP {exc.code}."

        error = detail.get("error", {})
        message = error.get("error_user_msg") or error.get("message") or str(exc)
        details = (error.get("error_data") or {}).get("details")
        if details and details != message:
                message = f"{message} {details}"

        codes = [str(part) for part in (error.get("code"), error.get("error_subcode")) if part]
        if codes:
                message = f"{message} (code {'/'.join(codes)})"
        return message


def encode_multipart(fields, file_field, filename, content, mime=None):
        """Build a multipart/form-data body.

        The media endpoint will not accept JSON, and the codebase has no
        requests dependency, so the body is assembled by hand. Returns
        (content_type, body_bytes)."""
        if mime is None:
                mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        boundary = f"----GravityInsurance{uuid.uuid4().hex}"
        marker = f"--{boundary}\r\n".encode("utf-8")
        parts = []

        for name, value in fields.items():
                parts.append(marker)
                parts.append(
                        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
                )
                parts.append(f"{value}\r\n".encode("utf-8"))

        parts.append(marker)
        parts.append(
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{filename}"\r\n'.encode("utf-8")
        )
        parts.append(f"Content-Type: {mime}\r\n\r\n".encode("utf-8"))
        parts.append(content)
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

        return f"multipart/form-data; boundary={boundary}", b"".join(parts)


def build_image_template_payload(number, template_name, language, media_id, values):
        """A template message whose header is an uploaded image.

        `values` fill the body's {{1}}..{{n}}. WhatsApp rejects parameters
        containing newlines or tabs, so each is flattened to one line."""
        components = [{
                "type": "header",
                "parameters": [{"type": "image", "image": {"id": media_id}}],
        }]
        if values:
                components.append({
                        "type": "body",
                        "parameters": [
                                {"type": "text", "text": " ".join(str(v or "").split())}
                                for v in values
                        ],
                })
        return {
                "messaging_product": "whatsapp",
                "to": number,
                "type": "template",
                "template": {
                        "name": template_name,
                        "language": {"code": language},
                        "components": components,
                },
        }


def _post(url, data, headers, timeout):
        """Returns (ok, parsed_body, error_message)."""
        request = Request(url, data=data, headers=headers, method="POST")
        try:
                with urlopen(request, timeout=timeout) as response:
                        return True, json.loads(response.read().decode("utf-8")), ""
        except HTTPError as exc:
                return False, None, describe_error(exc)
        except (URLError, OSError) as exc:
                return False, None, f"Could not reach the WhatsApp API: {exc}"
        except ValueError as exc:
                return False, None, f"Unreadable response from the WhatsApp API: {exc}"


def upload_media(content, filename, token, phone_number_id,
                 graph_version="v23.0", mime="image/png", timeout=60):
        """Upload bytes to the media endpoint. Returns (ok, media_id, error).

        The upload is the slow half of sending an image, hence the longer
        default timeout than a text send gets."""
        if not content:
                return False, "", "Nothing to upload."
        if len(content) > MAX_IMAGE_BYTES:
                size = len(content) / (1024 * 1024)
                return False, "", f"Image is {size:.1f} MB; WhatsApp accepts at most 5 MB."

        content_type, body = encode_multipart(
                {"messaging_product": "whatsapp", "type": mime},
                "file", filename, content, mime,
        )
        ok, parsed, error = _post(
                f"{GRAPH_HOST}/{graph_version}/{phone_number_id}/media",
                body,
                {"Authorization": f"Bearer {token}", "Content-Type": content_type},
                timeout,
        )
        if not ok:
                return False, "", error

        media_id = str(parsed.get("id") or "")
        if not media_id:
                return False, "", "The media upload returned no id."
        return True, media_id, ""


def send_image_template(number, template_name, media_id, values, token, phone_number_id,
                        language="en", graph_version="v23.0", timeout=30):
        """Send an already-uploaded image as a template. Returns (ok, wamid, error)."""
        payload = build_image_template_payload(
                number, template_name, language, media_id, values
        )
        ok, parsed, error = _post(
                f"{GRAPH_HOST}/{graph_version}/{phone_number_id}/messages",
                json.dumps(payload).encode("utf-8"),
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout,
        )
        if not ok:
                return False, "", error
        return True, (parsed.get("messages") or [{}])[0].get("id", ""), ""
