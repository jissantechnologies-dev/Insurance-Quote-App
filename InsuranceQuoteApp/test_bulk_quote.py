"""Checks for the Send Bulk Quote page.

The risky parts of a bulk run are the ones that decide *who* gets a message:
the filters, the de-duplication, and the guard that stops a lead with no
mobile number being counted as sent. Those are what is covered here, along
with campaign image storage.

Each test points GI_DATA_DIR at a temporary directory, so nothing here touches
the real leads file or the live campaign images.

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
                self.main, self.campaigns = load_app(data_dir)
                self.data_dir = data_dir

        def tearDown(self):
                self.temp.cleanup()
                os.environ.pop("GI_DATA_DIR", None)

        def test_leads_are_deduplicated_by_mobile_number(self):
                leads = self.main.load_bulk_leads()
                numbers = [lead["mobileNumber"] for lead in leads if lead["mobileNumber"]]
                self.assertEqual(len(numbers), len(set(numbers)))
                self.assertNotIn("Duplicate John", [lead["name"] for lead in leads])

        def test_lead_without_a_number_is_not_sendable(self):
                lead = next(l for l in self.main.load_bulk_leads() if l["name"] == "NoNumber")
                self.assertFalse(lead["valid"])

        def test_mobile_numbers_get_the_country_code(self):
                lead = next(l for l in self.main.load_bulk_leads() if l["name"] == "John")
                self.assertEqual(lead["mobileNumber"], "919555123412")
                self.assertEqual(lead["id"], "919555123412")

        def test_filter_by_vehicle_segment(self):
                leads = self.main.load_bulk_leads()
                buses = self.main.filter_bulk_leads(leads, segment="Buses")
                self.assertEqual([lead["name"] for lead in buses], ["Mani", "NoNumber"])

                cars = self.main.filter_bulk_leads(leads, segment="Cars")
                self.assertEqual([lead["name"] for lead in cars], ["John"])

                vans = self.main.filter_bulk_leads(leads, segment="Commercial Vehicles")
                self.assertEqual([lead["name"] for lead in vans], ["Karthick"])

        def test_filter_by_brand_and_model(self):
                leads = self.main.load_bulk_leads()
                by_brand = self.main.filter_bulk_leads(leads, brand="ashok leyland")
                self.assertEqual([lead["name"] for lead in by_brand], ["Mani"])

                by_model = self.main.filter_bulk_leads(leads, model="Seltos")
                self.assertEqual([lead["name"] for lead in by_model], ["John"])

                self.assertEqual(
                        self.main.filter_bulk_leads(leads, brand="Kia", model="Viking"), []
                )

        def test_search_matches_name_and_number(self):
                leads = self.main.load_bulk_leads()
                self.assertEqual(
                        [lead["name"] for lead in self.main.filter_bulk_leads(leads, search="9444")],
                        ["Karthick"],
                )
                self.assertEqual(
                        [lead["name"] for lead in self.main.filter_bulk_leads(leads, search="mani")],
                        ["Mani"],
                )

        def test_invalid_lead_is_recorded_as_failed_not_sent(self):
                lead = next(l for l in self.main.load_bulk_leads() if l["name"] == "NoNumber")
                payload, status = self.main.send_bulk_quote_to_lead(lead["id"])
                self.assertEqual(status, 200)
                self.assertFalse(payload["success"])

                history = self.main.load_bulk_sent_history()
                self.assertEqual(history[0]["status"], "Failed")
                self.assertEqual(history[0]["name"], "NoNumber")

        def test_unknown_lead_is_rejected(self):
                payload, status = self.main.send_bulk_quote_to_lead("910000000000")
                self.assertEqual(status, 404)
                self.assertFalse(payload["success"])

        def test_send_without_the_cloud_api_hands_back_a_wa_link(self):
                self.main.WA_TOKEN = ""
                self.main.WA_PHONE_NUMBER_ID = ""
                lead = next(l for l in self.main.load_bulk_leads() if l["name"] == "Mani")
                payload, status = self.main.send_bulk_quote_to_lead(lead["id"])
                self.assertEqual(status, 200)
                self.assertIn("wa.me/919884000111", payload["waLink"])

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
                lead = next(l for l in self.main.load_bulk_leads() if l["name"] == "Mani")
                payload, status = self.main.send_bulk_quote_to_lead(lead["id"], "b" * 32)
                self.assertEqual(status, 404)
                self.assertFalse(payload["success"])

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


if __name__ == "__main__":
        unittest.main(verbosity=2)
