"""Checks on the WhatsApp media upload and image-template send.

Nothing here touches the network: _post is stubbed so the tests assert what
would have been sent. The payload shape is the part worth pinning down, since
Meta rejects a malformed template with an error that names no field.

Run with:  py test_whatsapp_media.py
"""

import json
import unittest
from email.parser import BytesParser
from email.policy import default

import whatsapp_media


class RecordingPost:
        """Stands in for whatsapp_media._post and records the call."""

        def __init__(self, ok=True, parsed=None, error=""):
                self.ok, self.parsed, self.error = ok, parsed, error
                self.calls = []

        def __call__(self, url, data, headers, timeout):
                self.calls.append({
                        "url": url, "data": data, "headers": headers, "timeout": timeout,
                })
                return self.ok, self.parsed, self.error

        @property
        def last(self):
                return self.calls[-1]


class stub_post:
        """Swap _post for the duration of a block, then put it back."""

        def __init__(self, recorder):
                self.recorder = recorder

        def __enter__(self):
                self.original = whatsapp_media._post
                whatsapp_media._post = self.recorder
                return self.recorder

        def __exit__(self, *exc):
                whatsapp_media._post = self.original


class Multipart(unittest.TestCase):
        def test_body_parses_back_to_its_fields(self):
                content_type, body = whatsapp_media.encode_multipart(
                        {"messaging_product": "whatsapp", "type": "image/png"},
                        "file", "quote-TN28BM0972.png", b"\x89PNG\r\n\x1a\nDATA", "image/png",
                )
                message = BytesParser(policy=default).parsebytes(
                        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8") + body
                )
                parts = {
                        part.get_param("name", header="content-disposition"): part
                        for part in message.iter_parts()
                }
                self.assertEqual(parts["messaging_product"].get_content().strip(), "whatsapp")
                self.assertEqual(parts["type"].get_content().strip(), "image/png")

                uploaded = parts["file"]
                self.assertEqual(
                        uploaded.get_param("filename", header="content-disposition"),
                        "quote-TN28BM0972.png",
                )
                self.assertEqual(uploaded.get_content_type(), "image/png")
                self.assertEqual(uploaded.get_payload(decode=True), b"\x89PNG\r\n\x1a\nDATA")

        def test_binary_content_survives_intact(self):
                """Every byte value, including the CRLF the encoder delimits on."""
                blob = bytes(range(256))
                content_type, body = whatsapp_media.encode_multipart(
                        {}, "file", "blob.bin", blob, "application/octet-stream"
                )
                message = BytesParser(policy=default).parsebytes(
                        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8") + body
                )
                part = next(message.iter_parts())
                self.assertEqual(part.get_payload(decode=True), blob)

        def test_mime_is_guessed_from_the_filename(self):
                content_type, body = whatsapp_media.encode_multipart(
                        {}, "file", "quote.png", b"x"
                )
                self.assertIn(b"Content-Type: image/png", body)


class Upload(unittest.TestCase):
        def test_returns_the_media_id(self):
                with stub_post(RecordingPost(parsed={"id": "media-123"})) as post:
                        ok, media_id, error = whatsapp_media.upload_media(
                                b"png-bytes", "quote.png", "TOKEN", "PHONE_ID",
                                graph_version="v23.0",
                        )
                self.assertEqual((ok, media_id, error), (True, "media-123", ""))
                self.assertEqual(
                        post.last["url"],
                        "https://graph.facebook.com/v23.0/PHONE_ID/media",
                )
                self.assertEqual(post.last["headers"]["Authorization"], "Bearer TOKEN")
                self.assertTrue(
                        post.last["headers"]["Content-Type"].startswith("multipart/form-data;")
                )

        def test_rejects_an_oversized_image_without_calling_out(self):
                post = RecordingPost()
                with stub_post(post):
                        ok, media_id, error = whatsapp_media.upload_media(
                                b"x" * (whatsapp_media.MAX_IMAGE_BYTES + 1),
                                "quote.png", "TOKEN", "PHONE_ID",
                        )
                self.assertFalse(ok)
                self.assertIn("5 MB", error)
                self.assertEqual(post.calls, [])

        def test_empty_content_is_refused(self):
                post = RecordingPost()
                with stub_post(post):
                        ok, _, error = whatsapp_media.upload_media(
                                b"", "quote.png", "TOKEN", "PHONE_ID"
                        )
                self.assertFalse(ok)
                self.assertEqual(post.calls, [])

        def test_a_response_without_an_id_is_a_failure(self):
                with stub_post(RecordingPost(parsed={"unexpected": True})):
                        ok, media_id, error = whatsapp_media.upload_media(
                                b"png", "quote.png", "TOKEN", "PHONE_ID"
                        )
                self.assertFalse(ok)
                self.assertEqual(media_id, "")
                self.assertIn("no id", error)

        def test_graph_errors_are_passed_through(self):
                with stub_post(RecordingPost(ok=False, error="Bad token (code 190)")):
                        ok, _, error = whatsapp_media.upload_media(
                                b"png", "quote.png", "TOKEN", "PHONE_ID"
                        )
                self.assertFalse(ok)
                self.assertEqual(error, "Bad token (code 190)")


class ImageTemplate(unittest.TestCase):
        def payload_for(self, values=("Saravanan", "TN28BM0972", "32,494")):
                with stub_post(RecordingPost(parsed={"messages": [{"id": "wamid.X"}]})) as post:
                        ok, wamid, error = whatsapp_media.send_image_template(
                                "919941456453", "quote_image_share", "media-123", values,
                                "TOKEN", "PHONE_ID", language="en", graph_version="v23.0",
                        )
                self.result = (ok, wamid, error)
                self.call = post.last
                return json.loads(post.last["data"].decode("utf-8"))

        def test_header_carries_the_media_id(self):
                payload = self.payload_for()
                self.assertEqual(self.result, (True, "wamid.X", ""))
                header = payload["template"]["components"][0]
                self.assertEqual(header["type"], "header")
                self.assertEqual(header["parameters"][0]["image"], {"id": "media-123"})

        def test_body_parameters_are_ordered_and_typed(self):
                body = self.payload_for()["template"]["components"][1]
                self.assertEqual(body["type"], "body")
                self.assertEqual(
                        [p["text"] for p in body["parameters"]],
                        ["Saravanan", "TN28BM0972", "32,494"],
                )
                self.assertTrue(all(p["type"] == "text" for p in body["parameters"]))

        def test_newlines_in_parameters_are_flattened(self):
                """WhatsApp rejects a parameter containing a newline or tab."""
                payload = self.payload_for(values=["Sara\nvanan", "TN28\tBM0972", "32,494"])
                params = payload["template"]["components"][1]["parameters"]
                self.assertEqual(
                        [p["text"] for p in params], ["Sara vanan", "TN28 BM0972", "32,494"]
                )

        def test_no_body_component_when_there_are_no_values(self):
                payload = self.payload_for(values=[])
                components = payload["template"]["components"]
                self.assertEqual(len(components), 1)
                self.assertEqual(components[0]["type"], "header")

        def test_targets_the_messages_endpoint_as_json(self):
                self.payload_for()
                self.assertEqual(
                        self.call["url"],
                        "https://graph.facebook.com/v23.0/PHONE_ID/messages",
                )
                self.assertEqual(self.call["headers"]["Content-Type"], "application/json")

        def test_send_failure_yields_no_wamid(self):
                with stub_post(RecordingPost(ok=False, error="Template not found (code 132001)")):
                        ok, wamid, error = whatsapp_media.send_image_template(
                                "919941456453", "quote_image_share", "media-123", [],
                                "TOKEN", "PHONE_ID",
                        )
                self.assertEqual((ok, wamid), (False, ""))
                self.assertIn("132001", error)


if __name__ == "__main__":
        unittest.main()
