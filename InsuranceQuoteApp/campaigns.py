"""Stored campaign images for the bulk quote page.

A bulk send is one picture (an insurer's offer poster, say) going out to many
leads, so the picture is uploaded once, kept on disk, and reused - both across
the recipients of a single run and across later runs of the same campaign.

Only storage lives here: saving the file, listing what has been saved, and
remembering the WhatsApp media id the upload was given. main.py owns the
sending, the same way it owns the token whatsapp_media.py is handed.
"""

import json
import uuid
from datetime import datetime

import paths

CAMPAIGN_DIR_NAME = "campaigns"
INDEX_NAME = "campaigns.json"

# What WhatsApp will accept as an image header, and what Pillow-free code here
# can identify from the filename alone.
ALLOWED_EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

# Meta caps image media at 5 MB; reject earlier so the agent hears about it
# while uploading rather than on the first send.
MAX_IMAGE_BYTES = 5 * 1024 * 1024

# A Meta media id expires 30 days after the upload. Nothing here tracks the
# clock: a send that fails on an expired id clears it and re-uploads.
CAMPAIGN_ID_LENGTH = 32


def campaign_dir():
        return paths.data_path(CAMPAIGN_DIR_NAME)


def index_path():
        return paths.data_path(INDEX_NAME)


def load_campaigns():
        path = index_path()
        if not path.exists():
                return []
        try:
                with path.open("r", encoding="utf-8") as file:
                        data = json.load(file)
        except (ValueError, OSError):
                return []
        return data if isinstance(data, list) else []


def save_campaigns(campaigns):
        paths.ensure_data_dir()
        with index_path().open("w", encoding="utf-8") as file:
                json.dump(campaigns, file, indent=4)


def get_campaign(campaign_id):
        for campaign in load_campaigns():
                if campaign.get("id") == campaign_id:
                        return campaign
        return None


def campaign_file_path(campaign):
        """Where a campaign's image actually sits, or None if it is gone."""
        if not campaign:
                return None
        path = campaign_dir() / str(campaign.get("file", ""))
        return path if path.is_file() else None


def split_extension(filename):
        name = str(filename or "").strip().lower()
        for extension in ALLOWED_EXTENSIONS:
                if name.endswith(extension):
                        return extension
        return ""


def save_campaign_image(filename, content, title=""):
        """Store an uploaded image. Returns (campaign, error_message)."""
        extension = split_extension(filename)
        if not extension:
                return None, "Only PNG and JPG images can be sent on WhatsApp."
        if not content:
                return None, "The uploaded file was empty."
        if len(content) > MAX_IMAGE_BYTES:
                size = len(content) / (1024 * 1024)
                return None, f"Image is {size:.1f} MB; WhatsApp accepts at most 5 MB."

        directory = campaign_dir()
        directory.mkdir(parents=True, exist_ok=True)

        campaign_id = uuid.uuid4().hex
        stored_name = campaign_id + extension
        (directory / stored_name).write_bytes(content)

        campaign = {
                "id": campaign_id,
                "title": str(title or "").strip() or str(filename or "Campaign image"),
                "file": stored_name,
                "mime": ALLOWED_EXTENSIONS[extension],
                "uploadedAt": datetime.now().strftime("%Y-%m-%d %I:%M %p"),
                "mediaId": "",
        }
        campaigns = load_campaigns()
        campaigns.insert(0, campaign)
        save_campaigns(campaigns)
        return campaign, ""


def set_media_id(campaign_id, media_id):
        """Remember (or, with an empty id, forget) the WhatsApp media id."""
        campaigns = load_campaigns()
        for campaign in campaigns:
                if campaign.get("id") == campaign_id:
                        campaign["mediaId"] = media_id
                        save_campaigns(campaigns)
                        return campaign
        return None


def delete_campaign(campaign_id):
        """Remove a campaign and its image. Returns True if one was removed."""
        campaigns = load_campaigns()
        remaining = [c for c in campaigns if c.get("id") != campaign_id]
        if len(remaining) == len(campaigns):
                return False

        for campaign in campaigns:
                if campaign.get("id") == campaign_id:
                        path = campaign_file_path(campaign)
                        if path is not None:
                                try:
                                        path.unlink()
                                except OSError:
                                        pass
        save_campaigns(remaining)
        return True


def is_campaign_id(value):
        """Guard for ids arriving in a URL before they touch the filesystem."""
        text = str(value or "")
        return len(text) == CAMPAIGN_ID_LENGTH and all(ch in "0123456789abcdef" for ch in text)
