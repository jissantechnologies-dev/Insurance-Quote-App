"""Checks for the Send Bulk Quote page.

The risky parts of a bulk run are the ones that decide *who* gets a message:
the filters, the de-duplication across the active-client and leads lists, and
the guard that stops someone with no mobile number being counted as sent.
Those are what is covered here, along with campaign image storage.

Each test points GI_DATA_DIR at a temporary directory, so nothing here touches
the real customer files or the live campaign images.

Run with:  py test_bulk_quote.py
"""

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path


def load_app(data_dir):
        """Import main.py against a throwaway data directory."""
        os.environ["GI_DATA_DIR"] = str(data_dir)
        import paths
        importlib.reload(paths)
        import campaigns
        importlib.reload(campaigns)
        import main
        importlib.reload(main)
        return main, campaigns


ACTIVE_CUSTOMERS = [
        {
                "name": "Priya",
                "mobileNumber": "9700111222",
                "carType": "Sedan",
                "carBrand": "Benz",
                "carModel": "C Class",
                "policyExpiryDate": "2026-11-02",
        },
        {
                # Also sits in the leads file below: a lead who was converted
                # but never removed from newcustomer.txt.
                "name": "Mani",
                "mobileNumber": "9884000111",
                "carType": "Bus",
                "carBrand": "Ashok Leyland",
                "carModel": "Viking",
                "policyExpiryDate": "2026-09-01",
        },
]

LEAD_LINES = [
        "John|31|9555123412|SUV|Kia|Seltos|2023|TAT2607101|2026-08-05",
        "Karthick|27|9444033221|Commercial Van|Force|Traveller|2021|HDF2607102|2026-07-25",
        "Mani|40|9884000111|Bus|Ashok Leyland|Viking|2019|ICI2607103|2026-09-01",
        "Duplicate John|31|9555123412|SUV|Kia|Seltos|2023|TAT2607104|2026-08-05",
        "NoNumber||       |Bus|Benz|Tourismo|2020||",
]


class BulkQuoteTests(unittest.TestCase):
        def setUp(self):
                self.temp = tempfile.TemporaryDirectory()
                data_dir = Path(self.temp.name)
                data_dir.joinpath("newcustomer.txt").write_text(
                        "\n".join(LEAD_LINES), encoding="utf-8"
                )
                data_dir.joinpath("customers.json").write_text(
                        json.dumps(ACTIVE_CUSTOMERS), encoding="utf-8"
                )
                self.main, self.campaigns = load_app(data_dir)
                self.data_dir = data_dir

        def tearDown(self):
                self.temp.cleanup()
                os.environ.pop("GI_DATA_DIR", None)

        def test_both_lists_are_loaded(self):
                people = self.main.load_bulk_recipients()
                self.assertIn("Priya", [p["name"] for p in people])
                self.assertIn("John", [p["name"] for p in people])
                self.assertEqual(
                        {p["list"] for p in people},
                        {self.main.BULK_LIST_ACTIVE, self.main.BULK_LIST_LEAD},
                )

        def test_someone_in_both_lists_appears_once_as_a_client(self):
                people = self.main.load_bulk_recipients()
                manis = [p for p in people if p["name"] == "Mani"]
                self.assertEqual(len(manis), 1)
                self.assertEqual(manis[0]["list"], self.main.BULK_LIST_ACTIVE)

        def test_filter_by_list(self):
                people = self.main.load_bulk_recipients()
                active = self.main.filter_bulk_recipients(people, list_name=self.main.BULK_LIST_ACTIVE)
                self.assertEqual(sorted(p["name"] for p in active), ["Mani", "Priya"])

                leads_only = self.main.filter_bulk_recipients(people, list_name=self.main.BULK_LIST_LEAD)
                self.assertNotIn("Priya", [p["name"] for p in leads_only])
                self.assertIn("John", [p["name"] for p in leads_only])

        def test_leads_are_deduplicated_by_mobile_number(self):
                leads = self.main.load_bulk_recipients()
                numbers = [lead["mobileNumber"] for lead in leads if lead["mobileNumber"]]
                self.assertEqual(len(numbers), len(set(numbers)))
                self.assertNotIn("Duplicate John", [lead["name"] for lead in leads])

        def test_lead_without_a_number_is_not_sendable(self):
                lead = next(l for l in self.main.load_bulk_recipients() if l["name"] == "NoNumber")
                self.assertFalse(lead["valid"])

        def test_mobile_numbers_get_the_country_code(self):
                lead = next(l for l in self.main.load_bulk_recipients() if l["name"] == "John")
                self.assertEqual(lead["mobileNumber"], "919555123412")
                self.assertEqual(lead["id"], "919555123412")

        def test_filter_by_vehicle_segment(self):
                leads = self.main.load_bulk_recipients()
                buses = self.main.filter_bulk_recipients(leads, segment="Buses")
                self.assertEqual(sorted(lead["name"] for lead in buses), ["Mani", "NoNumber"])

                cars = self.main.filter_bulk_recipients(leads, segment="Cars")
                self.assertEqual([lead["name"] for lead in cars], ["Priya", "John"])

                vans = self.main.filter_bulk_recipients(leads, segment="Commercial Vehicles")
                self.assertEqual([lead["name"] for lead in vans], ["Karthick"])

        def test_filter_by_brand_and_model(self):
                leads = self.main.load_bulk_recipients()
                by_brand = self.main.filter_bulk_recipients(leads, brand="ashok leyland")
                self.assertEqual([lead["name"] for lead in by_brand], ["Mani"])

                benz = self.main.filter_bulk_recipients(leads, brand="Benz")
                self.assertEqual([lead["name"] for lead in benz], ["Priya", "NoNumber"])

                by_model = self.main.filter_bulk_recipients(leads, model="Seltos")
                self.assertEqual([lead["name"] for lead in by_model], ["John"])

                self.assertEqual(
                        self.main.filter_bulk_recipients(leads, brand="Kia", model="Viking"), []
                )

        def test_search_matches_name_and_number(self):
                leads = self.main.load_bulk_recipients()
                self.assertEqual(
                        [lead["name"] for lead in self.main.filter_bulk_recipients(leads, search="9444")],
                        ["Karthick"],
                )
                self.assertEqual(
                        [lead["name"] for lead in self.main.filter_bulk_recipients(leads, search="mani")],
                        ["Mani"],
                )

        def test_invalid_lead_is_recorded_as_failed_not_sent(self):
                lead = next(l for l in self.main.load_bulk_recipients() if l["name"] == "NoNumber")
                payload, status = self.main.send_bulk_quote_to_recipient(lead["id"])
                self.assertEqual(status, 200)
                self.assertFalse(payload["success"])

                history = self.main.load_bulk_sent_history()
                self.assertEqual(history[0]["status"], "Failed")
                self.assertEqual(history[0]["name"], "NoNumber")

        def test_unknown_recipient_is_rejected(self):
                payload, status = self.main.send_bulk_quote_to_recipient("910000000000")
                self.assertEqual(status, 404)
                self.assertFalse(payload["success"])

        def test_send_without_the_cloud_api_hands_back_a_wa_link(self):
                self.main.WA_TOKEN = ""
                self.main.WA_PHONE_NUMBER_ID = ""
                lead = next(l for l in self.main.load_bulk_recipients() if l["name"] == "Mani")
                payload, status = self.main.send_bulk_quote_to_recipient(lead["id"])
                self.assertEqual(status, 200)
                self.assertIn("wa.me/919884000111", payload["waLink"])

        def test_bulk_sends_carry_no_body_parameters(self):
                """The approved templates hold no variables.

                Sending a parameter to a template that declares none is
                rejected by Meta (#132000), so both bulk paths must pass an
                empty list - and the image must be the campaign's, uploaded
                once and reused."""
                self.main.WA_TOKEN = "test-token"
                self.main.WA_PHONE_NUMBER_ID = "12345"
                campaign, _ = self.campaigns.save_campaign_image(
                        "offer.png", b"fake-png-bytes", "Extended Warranty"
                )

                calls = {}

                def fake_upload(content, filename, token, phone_id, **kwargs):
                        calls["uploaded"] = calls.get("uploaded", 0) + 1
                        return True, "media-1", ""

                def fake_send_image(number, template, media_id, values, token, phone_id, **kwargs):
                        calls["image"] = {"template": template, "media_id": media_id, "values": values}
                        return True, "wamid-1", ""

                self.main.whatsapp_media.upload_media = fake_upload
                self.main.whatsapp_media.send_image_template = fake_send_image
                self.main.chat.save_message = lambda *a, **k: None

                people = [p for p in self.main.load_bulk_recipients() if p["valid"]][:2]
                for person in people:
                        payload, status = self.main.send_bulk_quote_to_recipient(
                                person["id"], campaign["id"]
                        )
                        self.assertTrue(payload["success"], payload)

                self.assertEqual(calls["image"]["values"], [])
                self.assertEqual(calls["image"]["template"], self.main.WA_TEMPLATE_BULK_IMAGE)
                self.assertEqual(calls["image"]["media_id"], "media-1")
                # Two recipients, one upload: the media id is cached.
                self.assertEqual(calls["uploaded"], 1)

        def test_text_template_payload_has_no_body_component(self):
                sent = {}

                def fake_urlopen(request, timeout=30):
                        sent["payload"] = json.loads(request.data.decode("utf-8"))
                        raise OSError("stop here - the payload is what matters")

                self.main.WA_TOKEN = "test-token"
                self.main.WA_PHONE_NUMBER_ID = "12345"
                self.main.urlopen = fake_urlopen
                self.main.send_template_message("919884000111", "bulk_offer_text", [])

                self.assertEqual(sent["payload"]["template"]["components"], [])

        def test_templates_with_values_still_send_a_body(self):
                """The quote and payment templates do take parameters."""
                sent = {}

                def fake_urlopen(request, timeout=30):
                        sent["payload"] = json.loads(request.data.decode("utf-8"))
                        raise OSError("stop here - the payload is what matters")

                self.main.WA_TOKEN = "test-token"
                self.main.WA_PHONE_NUMBER_ID = "12345"
                self.main.urlopen = fake_urlopen
                self.main.send_template_message("919884000111", "quote_share", ["Mani", "TN01AB1234"])

                body = sent["payload"]["template"]["components"][0]
                self.assertEqual(body["type"], "body")
                self.assertEqual([p["text"] for p in body["parameters"]], ["Mani", "TN01AB1234"])

        def test_campaign_image_round_trip(self):
                campaign, error = self.campaigns.save_campaign_image(
                        "offer.png", b"fake-png-bytes", "Extended Warranty"
                )
                self.assertEqual(error, "")
                self.assertEqual(campaign["title"], "Extended Warranty")

                stored = self.campaigns.campaign_file_path(campaign)
                self.assertIsNotNone(stored)
                self.assertEqual(stored.read_bytes(), b"fake-png-bytes")

                self.campaigns.set_media_id(campaign["id"], "media-123")
                self.assertEqual(self.campaigns.get_campaign(campaign["id"])["mediaId"], "media-123")

                self.assertTrue(self.campaigns.delete_campaign(campaign["id"]))
                self.assertIsNone(self.campaigns.get_campaign(campaign["id"]))
                self.assertFalse(stored.exists())

        def test_campaign_upload_rejects_non_images(self):
                campaign, error = self.campaigns.save_campaign_image("offer.pdf", b"data")
                self.assertIsNone(campaign)
                self.assertIn("PNG", error)

        def test_campaign_upload_rejects_oversized_images(self):
                campaign, error = self.campaigns.save_campaign_image(
                        "big.png", b"x" * (self.campaigns.MAX_IMAGE_BYTES + 1)
                )
                self.assertIsNone(campaign)
                self.assertIn("5 MB", error)

        def test_campaign_id_guard_rejects_path_traversal(self):
                self.assertFalse(self.campaigns.is_campaign_id("../../etc/passwd"))
                self.assertFalse(self.campaigns.is_campaign_id(""))
                self.assertTrue(self.campaigns.is_campaign_id("a" * 32))
                self.assertIsNone(self.main.get_campaign_image_path("../../style.css"))

        def test_send_to_a_missing_campaign_is_refused(self):
                lead = next(l for l in self.main.load_bulk_recipients() if l["name"] == "Mani")
                payload, status = self.main.send_bulk_quote_to_recipient(lead["id"], "b" * 32)
                self.assertEqual(status, 404)
                self.assertFalse(payload["success"])

        def test_clearing_the_history_empties_it(self):
                for index in range(3):
                        self.main.record_bulk_send({"name": f"n{index}", "status": "Sent"})
                self.assertEqual(len(self.main.load_bulk_sent_history()), 3)

                removed = self.main.clear_bulk_sent_history()
                self.assertEqual(removed, 3)
                self.assertEqual(self.main.load_bulk_sent_history(), [])

                # The file survives, so the next send has somewhere to append.
                self.assertTrue(self.main.BULK_SENT_PATH.exists())
                self.main.record_bulk_send({"name": "after", "status": "Sent"})
                self.assertEqual(len(self.main.load_bulk_sent_history()), 1)

        def test_clearing_an_empty_history_is_harmless(self):
                self.assertEqual(self.main.clear_bulk_sent_history(), 0)
                self.assertEqual(self.main.load_bulk_sent_history(), [])

        def test_history_is_capped(self):
                for index in range(505):
                        self.main.record_bulk_send({"name": f"n{index}", "status": "Sent"})
                history = json.loads(self.main.BULK_SENT_PATH.read_text(encoding="utf-8"))
                self.assertEqual(len(history), 500)
                self.assertEqual(history[0]["name"], "n504")

        def test_page_renders_filter_options_from_the_leads(self):
                html = self.main.build_bulk_quote_content()
                self.assertIn("Send Bulk Quote", html)
                self.assertIn(">Ashok Leyland<", html)
                self.assertIn(">Seltos<", html)
                self.assertIn(">Buses<", html)
                self.assertIn(">Active Client<", html)
                self.assertIn(">Lead<", html)


if __name__ == "__main__":
        unittest.main(verbosity=2)
