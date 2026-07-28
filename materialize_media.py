#!/usr/bin/env python3
"""Download provider-safe image files referenced by an ingest bundle.

The deterministic bundle keeps signed Discord CDN locators in its private
partition.  This step resolves those locators immediately after a crawl and
writes opaque, content-addressed local files plus a URL-free manifest for the
commercial curation stage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

import requests


ALLOWED_HOSTS = {
    "cdn.discordapp.com",
    "media.discordapp.net",
    "images-ext-1.discordapp.net",
    "images-ext-2.discordapp.net",
}
ALLOWED_CONTENT_TYPES = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
DEFAULT_MAX_FILE_BYTES = 12 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 128 * 1024 * 1024
MANIFEST_SCHEMA = "agentic-researcher/media-manifest/v1"


class MediaDownloadError(RuntimeError):
    """A media item cannot be materialized safely."""


def _safe_media_id(value: object) -> str:
    media_id = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", media_id):
        raise MediaDownloadError("media id is not a path-safe identifier")
    return media_id


def _safe_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise MediaDownloadError("locator is not an allow-listed Discord HTTPS URL")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise MediaDownloadError("locator contains disallowed authority components")
    return url


def _request(session, url: str, timeout: int):
    """Follow a small number of redirects while re-checking the allow-list."""
    current = _safe_url(url)
    for _ in range(4):
        response = session.get(
            current,
            stream=True,
            timeout=(10, timeout),
            allow_redirects=False,
            headers={"User-Agent": "Discord-Mathematics-Early-University/1.0"},
        )
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise MediaDownloadError("redirect response has no Location header")
            current = _safe_url(urljoin(current, location))
            continue
        if response.status_code != 200:
            response.close()
            raise MediaDownloadError(f"Discord CDN returned HTTP {response.status_code}")
        return response
    raise MediaDownloadError("too many redirects")


def _public_media(bundle: dict) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for message in bundle.get("public", {}).get("messages", []):
        for media in message.get("attachments", []):
            if isinstance(media, dict) and isinstance(media.get("id"), str):
                records[media["id"]] = media
    return records


def _private_media(bundle: dict) -> list[dict]:
    records: dict[str, dict] = {}
    for message in bundle.get("private", {}).get("messages", []):
        for media in message.get("attachments", []):
            if not isinstance(media, dict) or not isinstance(media.get("id"), str):
                continue
            records.setdefault(media["id"], media)
    return [records[key] for key in sorted(records)]


def _manifest_entry(media_id: str, status: str, **values) -> dict:
    return {"media_id": media_id, "status": status, **values}


def _has_expected_image_signature(path: Path, content_type: str) -> bool:
    with path.open("rb") as handle:
        header = handle.read(16)
    return {
        "image/png": header.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": header.startswith(b"\xff\xd8\xff"),
        "image/gif": header.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": header.startswith(b"RIFF") and header[8:12] == b"WEBP",
    }.get(content_type, False)


def materialize_media(
    bundle: dict,
    output_dir: Path,
    *,
    session=None,
    max_files: int = 200,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    timeout: int = 60,
) -> dict:
    if bundle.get("schema_version") != "agentic-researcher/ingest-bundle/v1":
        raise ValueError("unsupported ingest bundle schema")
    output_dir.mkdir(parents=True, exist_ok=True)
    active_session = session or requests.Session()
    public_by_id = _public_media(bundle)
    entries: list[dict] = []
    downloaded = 0
    total_bytes = 0

    for private in _private_media(bundle):
        media_id = _safe_media_id(private["id"])
        public = public_by_id.get(media_id)
        if not public or public.get("media_type") != "image":
            entries.append(_manifest_entry(media_id, "skipped", reason="not-an-image"))
            continue
        if downloaded >= max_files:
            entries.append(_manifest_entry(media_id, "skipped", reason="file-limit"))
            continue
        url = str(private.get("url") or private.get("proxy_url") or "")
        if not url:
            entries.append(_manifest_entry(media_id, "skipped", reason="missing-locator"))
            continue

        response = None
        temporary = output_dir / f".{media_id}.partial"
        try:
            response = _request(active_session, url, timeout)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            extension = ALLOWED_CONTENT_TYPES.get(content_type)
            if not extension:
                raise MediaDownloadError(
                    f"response content type is not an approved image: {content_type or 'missing'}"
                )
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > max_file_bytes:
                raise MediaDownloadError("declared image size exceeds the per-file limit")
            digest = hashlib.sha256()
            size = 0
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_file_bytes:
                        raise MediaDownloadError("image exceeds the per-file limit")
                    if total_bytes + size > max_total_bytes:
                        raise MediaDownloadError("images exceed the total download limit")
                    digest.update(chunk)
                    handle.write(chunk)
            if not _has_expected_image_signature(temporary, content_type):
                raise MediaDownloadError(
                    "downloaded bytes do not match the declared image type"
                )
            sha256 = digest.hexdigest()
            expected = public.get("content_sha256")
            if expected and expected != sha256:
                raise MediaDownloadError("downloaded image does not match its expected SHA-256")
            destination = output_dir / f"{media_id}{extension}"
            os.replace(temporary, destination)
            downloaded += 1
            total_bytes += size
            entries.append(
                _manifest_entry(
                    media_id,
                    "downloaded",
                    path=destination.name,
                    content_type=content_type,
                    size_bytes=size,
                    sha256=sha256,
                    is_texit_render=bool(public.get("is_texit_render")),
                )
            )
        except (MediaDownloadError, OSError, requests.RequestException, ValueError) as exc:
            if temporary.exists():
                temporary.unlink()
            entries.append(_manifest_entry(media_id, "failed", reason=str(exc)))
        finally:
            if response is not None:
                response.close()

    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "bundle_id": bundle.get("bundle_id"),
        "handling": "restricted_input_do_not_publish",
        "entries": entries,
        "summary": {
            status: sum(entry["status"] == status for entry in entries)
            for status in ("downloaded", "failed", "skipped")
        },
        "total_bytes": total_bytes,
    }
    manifest_path = output_dir / "media_manifest.json"
    with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize allow-listed Discord image attachments for curation."
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        default=Path("discord_exports/ingest_bundle.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("discord_exports/curation_media"),
    )
    parser.add_argument("--max-files", type=int, default=200)
    parser.add_argument("--max-file-mib", type=int, default=12)
    parser.add_argument("--max-total-mib", type=int, default=128)
    parser.add_argument("--timeout", type=int, default=60)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_files < 0 or args.max_file_mib < 1 or args.max_total_mib < 1:
        raise SystemExit("download limits must be positive")
    with args.bundle.open("r", encoding="utf-8") as handle:
        bundle = json.load(handle)
    manifest = materialize_media(
        bundle,
        args.output_dir,
        max_files=args.max_files,
        max_file_bytes=args.max_file_mib * 1024 * 1024,
        max_total_bytes=args.max_total_mib * 1024 * 1024,
        timeout=args.timeout,
    )
    print(json.dumps(manifest["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
