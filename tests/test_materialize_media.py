"""Tests for safe, URL-free image materialization."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from materialize_media import MediaDownloadError, materialize_media


class FakeResponse:
    def __init__(self, body: bytes, content_type: str = "image/png"):
        self.body = body
        self.status_code = 200
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
        }
        self.closed = False

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield self.body

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def bundle(url: str, expected_sha256: str | None = None) -> dict:
    public = {
        "id": "media_abc",
        "media_type": "image",
        "content_sha256": expected_sha256,
        "is_texit_render": True,
    }
    private = {"id": "media_abc", "url": url, "proxy_url": ""}
    return {
        "schema_version": "agentic-researcher/ingest-bundle/v1",
        "bundle_id": "bundle-one",
        "public": {"messages": [{"attachments": [public]}]},
        "private": {"messages": [{"attachments": [private]}]},
    }


class MediaMaterializationTests(unittest.TestCase):
    def test_downloads_opaque_image_and_manifest_contains_no_url(self):
        body = b"\x89PNG\r\n\x1a\nsynthetic"
        session = FakeSession(FakeResponse(body))
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            result = materialize_media(
                bundle(
                    "https://cdn.discordapp.com/attachments/1/2/file.png?secret=value",
                    hashlib.sha256(body).hexdigest(),
                ),
                output,
                session=session,
            )
            image = output / "media_abc.png"
            self.assertEqual(image.read_bytes(), body)
            serialized = json.dumps(result)
            self.assertNotIn("discordapp.com", serialized)
            self.assertNotIn("secret", serialized)
            self.assertEqual(result["summary"]["downloaded"], 1)
            self.assertEqual(result["entries"][0]["sha256"], hashlib.sha256(body).hexdigest())

    def test_rejects_non_discord_locator_without_network_request(self):
        session = FakeSession(FakeResponse(b"image"))
        with tempfile.TemporaryDirectory() as temp:
            result = materialize_media(
                bundle("https://example.com/private.png"),
                Path(temp),
                session=session,
            )
            self.assertEqual(session.calls, [])
            self.assertEqual(result["summary"]["failed"], 1)
            self.assertEqual(list(Path(temp).glob("media_abc.*")), [])

    def test_rejects_non_image_response_and_hash_mismatch(self):
        cases = [
            (FakeResponse(b"text", "text/plain"), None),
            (FakeResponse(b"wrong"), "0" * 64),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                result = materialize_media(
                    bundle("https://media.discordapp.net/attachments/1/2/a.png", expected),
                    Path(temp),
                    session=FakeSession(response),
                )
                self.assertEqual(result["summary"]["failed"], 1)
                self.assertFalse((Path(temp) / "media_abc.png").exists())

    def test_rejects_path_unsafe_media_id_before_network_or_file_access(self):
        source = bundle("https://cdn.discordapp.com/attachments/1/2/file.png")
        source["public"]["messages"][0]["attachments"][0]["id"] = "../escape"
        source["private"]["messages"][0]["attachments"][0]["id"] = "../escape"
        session = FakeSession(FakeResponse(b"\x89PNG\r\n\x1a\nsynthetic"))

        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(MediaDownloadError, "path-safe"):
                materialize_media(source, Path(temp), session=session)
            self.assertEqual(session.calls, [])
            self.assertEqual(list(Path(temp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
