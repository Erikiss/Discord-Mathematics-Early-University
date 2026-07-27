#!/usr/bin/env python3
"""Discord crawler for the "Mathematics" server, restricted to four channels.

Target channels:
    - calculus
    - linear-algebra
    - computing-software
    - proofs-and-logic

This mirrors the pipeline used for the Machine Learning servers (see the
``Discord_Crawl`` notebook): it authenticates with a user token stored as a
secret (Google Colab ``userdata`` or an environment variable), uses a single
REST client that honours Discord's rate limits (HTTP 429 + ``retry_after``) and
adds a randomised, human-like pause between requests, then walks
Server -> Channels -> Messages and writes one JSON file per channel.

Only the four channels above are crawled, because the Mathematics server has
dozens of channels and the request was to restrict the crawl to these four.

ToS note
--------
Like the original ML pipeline, this authenticates with a *user* account token
(a "self-bot"). Automating a user account is against Discord's Terms of Service.
Use it only on your own account, only for servers you have joined, at your own
risk, and keep the built-in rate limiting in place.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone

import requests

# --------------------------------------------------------------------------- #
# 1. Configuration
# --------------------------------------------------------------------------- #
API = "https://discord.com/api/v9"

# The Mathematics server. Matching is by name (case-insensitive); if several of
# your servers match, the one with the most members is chosen. You can pin the
# guild by ID instead to remove any ambiguity.
DEFAULT_SERVER_NAMES = ["Mathematics"]
DEFAULT_SERVER_IDS: list[str] = []  # e.g. ["123456789012345678"]

# The only four channels we care about.
TARGET_CHANNEL_NAMES = [
    "calculus",
    "linear-algebra",
    "computing-software",
    "proofs-and-logic",
]

# How far back to look. Set to ``None`` (CLI: --full) to crawl the full history.
# The original ML crawl used 3 days; 30 is a more useful default for building a
# study archive of these four courses.
DEFAULT_DAYS_BACK: int | None = 30
DEFAULT_MAX_PER_CHANNEL = 5000

BASE_DIR = "discord_exports"

# Secret keys tried in order (Colab userdata first, then environment).
SECRET_KEYS = ["DISCORD_TOKEN_Backupper123", "DISCORD_TOKEN"]

# Randomised delay applied after every request, to stay gentle on the API.
MIN_DELAY, MAX_DELAY = 1.0, 2.0

# Discord channel types that hold plain text messages we can paginate.
TEXT_CHANNEL_TYPES = {0, 5}  # 0 = GUILD_TEXT, 5 = GUILD_ANNOUNCEMENT


# --------------------------------------------------------------------------- #
# 2. Auth
# --------------------------------------------------------------------------- #
def get_token() -> str:
    """Return the Discord token from Colab secrets, env vars, or a prompt."""
    # 1) Google Colab secrets (userdata)
    try:
        from google.colab import userdata  # type: ignore

        for key in SECRET_KEYS:
            try:
                tok = userdata.get(key)
            except Exception:
                tok = None
            if tok:
                print(f"Token aus Colab-Secret '{key}' geladen.")
                return tok.strip()
    except Exception:
        pass  # not running in Colab

    # 2) Environment variables
    for key in SECRET_KEYS:
        tok = os.environ.get(key)
        if tok:
            print(f"Token aus Umgebungsvariable '{key}' geladen.")
            return tok.strip()

    # 3) Interactive fallback
    try:
        tok = input("Discord-Token nicht gefunden. Bitte einfügen: ").strip()
    except EOFError:
        tok = ""
    if tok:
        return tok

    raise SystemExit(
        "Kein Discord-Token verfügbar. Setze das Secret "
        "'DISCORD_TOKEN_Backupper123' (Colab) oder die Umgebungsvariable."
    )


def build_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": token,
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            ),
        }
    )
    return session


# --------------------------------------------------------------------------- #
# 3. REST client with rate-limit handling
# --------------------------------------------------------------------------- #
def discord_request(session: requests.Session, method: str, url: str):
    """Send a request and handle rate limits.

    Returns the parsed JSON body, ``True`` for 204, the sentinel string
    ``"FORBIDDEN"`` for 403, or ``None`` for other errors / network problems.
    """
    while True:
        try:
            resp = session.request(method, url, timeout=30)
        except requests.RequestException as exc:
            print(f"Netzwerkfehler bei {url}: {exc}")
            return None

        # Human-like delay after every call (applies to every response).
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

        code = resp.status_code
        if code in (200, 201):
            try:
                return resp.json()
            except ValueError:
                return None
        if code == 204:
            return True
        if code == 429:  # rate limited
            try:
                retry_after = float(resp.json().get("retry_after", 2))
            except Exception:
                retry_after = 2.0
            print(f"(!) Rate Limit erreicht. Warte {retry_after:.1f}s ...")
            time.sleep(retry_after + 0.5)
            continue  # retry the same request
        if code == 403:
            return "FORBIDDEN"  # access denied (e.g. private/admin channel)
        if code == 401:
            raise SystemExit("401 Unauthorized - Token ungültig oder abgelaufen.")
        print(f"Fehler {code} bei {url}: {resp.text[:200]}")
        return None


# --------------------------------------------------------------------------- #
# 4. Helpers
# --------------------------------------------------------------------------- #
def clean_filename(name: str) -> str:
    """Strip characters that are invalid in file/folder names."""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def parse_discord_timestamp(timestamp_str: str) -> datetime:
    if not timestamp_str:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(timestamp_str)
    except ValueError:
        return datetime.strptime(
            timestamp_str.split(".")[0], "%Y-%m-%dT%H:%M:%S"
        ).replace(tzinfo=timezone.utc)


def resolve_target_guilds(guilds, names, ids):
    """Return the guild dicts matching the requested names/IDs.

    Name matching prefers an exact (case-insensitive) match and falls back to a
    substring match. When several servers match a name, the one with the most
    members wins, so "Mathematics" resolves to the large public server.
    """
    result: dict[str, dict] = {}

    for sid in ids:
        guild = next((g for g in guilds if g["id"] == str(sid)), None)
        if guild:
            result[guild["id"]] = guild
        else:
            print(f"  Server-ID '{sid}' nicht gefunden.")

    for name in names:
        needle = name.lower().strip()
        candidates = [g for g in guilds if g["name"].lower().strip() == needle]
        if not candidates:
            candidates = [g for g in guilds if needle in g["name"].lower()]
        if not candidates:
            print(f"  Server '{name}' nicht gefunden - exakten Namen oder ID prüfen.")
            continue
        candidates.sort(
            key=lambda g: g.get("approximate_member_count", 0) or 0, reverse=True
        )
        if len(candidates) > 1:
            best = candidates[0]
            print(
                f"  Mehrere Treffer für '{name}', wähle größten: "
                f"{best['name']} ({best.get('approximate_member_count', '?')} Mitglieder)."
            )
        result[candidates[0]["id"]] = candidates[0]

    return list(result.values())


def collect_channel_messages(session, channel_id, cutoff, max_msgs, page=100):
    """Paginate a channel's messages newest-first until the cutoff or the cap.

    Returns a list of messages, or the sentinel ``"FORBIDDEN"`` if the channel
    cannot be read.
    """
    collected: list[dict] = []
    before = None
    while len(collected) < max_msgs:
        url = f"{API}/channels/{channel_id}/messages?limit={page}"
        if before:
            url += f"&before={before}"
        batch = discord_request(session, "GET", url)
        if batch == "FORBIDDEN":
            return "FORBIDDEN"
        if not batch:
            break

        stop = False
        for msg in batch:
            if cutoff is not None and parse_discord_timestamp(msg["timestamp"]) <= cutoff:
                stop = True
                break
            collected.append(msg)
            if len(collected) >= max_msgs:
                stop = True
                break

        before = batch[-1]["id"]
        if stop or len(batch) < page:
            break

    return collected


# --------------------------------------------------------------------------- #
# 5. Main crawl
# --------------------------------------------------------------------------- #
def crawl(
    days_back: int | None = DEFAULT_DAYS_BACK,
    max_per_channel: int = DEFAULT_MAX_PER_CHANNEL,
    base_dir: str = BASE_DIR,
    server_names=None,
    server_ids=None,
    channel_names=None,
):
    server_names = server_names or DEFAULT_SERVER_NAMES
    server_ids = server_ids or DEFAULT_SERVER_IDS
    wanted_names = {c.lower() for c in (channel_names or TARGET_CHANNEL_NAMES)}

    token = get_token()
    session = build_session(token)

    cutoff = (
        None if days_back is None else datetime.now(timezone.utc) - timedelta(days=days_back)
    )
    if cutoff:
        print(f"Sammle Nachrichten ab {cutoff.strftime('%Y-%m-%d %H:%M UTC')}.")
    else:
        print("Sammle die vollständige Historie (kein Zeitfenster).")

    print("Lade Server-Liste (beigetretene Guilds) ...")
    guilds = discord_request(session, "GET", f"{API}/users/@me/guilds?with_counts=true")
    if not guilds or isinstance(guilds, str):
        raise SystemExit("Server-Liste konnte nicht geladen werden - Token prüfen.")
    print(f"Mitglied in {len(guilds)} Servern.")

    targets = resolve_target_guilds(guilds, server_names, server_ids)
    if not targets:
        raise SystemExit("Kein Ziel-Server gefunden.")

    summary: dict[str, int] = {}
    for guild in targets:
        gname, gid = guild["name"], guild["id"]
        print(f"\n=== {gname} (ID: {gid}) ===")
        server_dir = os.path.join(base_dir, clean_filename(gname))
        os.makedirs(server_dir, exist_ok=True)

        channels = discord_request(session, "GET", f"{API}/guilds/{gid}/channels")
        if not channels or isinstance(channels, str):
            print("  Kanäle nicht lesbar (fehlende Rechte?).")
            continue

        by_name = [c for c in channels if c.get("name", "").lower() in wanted_names]
        wanted = [c for c in by_name if c.get("type") in TEXT_CHANNEL_TYPES]
        wrong_type = [c for c in by_name if c.get("type") not in TEXT_CHANNEL_TYPES]

        found = {c["name"].lower() for c in by_name}
        missing = wanted_names - found
        if missing:
            print(f"  Nicht gefunden: {', '.join(sorted(missing))}")
        if wrong_type:
            print(
                "  Gefunden, aber kein Textkanal (übersprungen): "
                + ", ".join(f"#{c['name']} (type={c['type']})" for c in wrong_type)
            )
        print(f"  {len(wanted)} Ziel-Kanäle: {', '.join('#' + c['name'] for c in wanted) or '-'}")

        seen: dict[str, int] = {}
        for ch in wanted:
            time.sleep(0.5)  # short pause between channels
            msgs = collect_channel_messages(session, ch["id"], cutoff, max_per_channel)
            if msgs == "FORBIDDEN":
                print(f"  #{ch['name']}: kein Zugriff (403).")
                continue

            base = clean_filename(ch["name"])
            seen[base] = seen.get(base, 0) + 1
            fname = base if seen[base] == 1 else f"{base}_{ch['id']}"
            path = os.path.join(server_dir, fname + ".json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(msgs, fh, indent=4, ensure_ascii=False)
            summary[ch["name"]] = summary.get(ch["name"], 0) + len(msgs)
            print(f"  #{ch['name']}: {len(msgs)} Nachrichten -> {path}")

    print("\nFertig. Zusammenfassung:")
    if summary:
        for name, count in summary.items():
            print(f"  #{name}: {count} Nachrichten")
    else:
        print("  Keine Nachrichten gespeichert.")
    return summary


# --------------------------------------------------------------------------- #
# 6. CLI
# --------------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Crawl the Mathematics Discord server (four channels only).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS_BACK,
        help="Look back this many days.",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="Crawl the full channel history (ignores --days).",
    )
    p.add_argument(
        "--max",
        type=int,
        default=DEFAULT_MAX_PER_CHANNEL,
        help="Maximum messages collected per channel.",
    )
    p.add_argument("--out", default=BASE_DIR, help="Output base directory.")
    p.add_argument(
        "--server",
        action="append",
        default=None,
        help="Server name(s) to target (repeatable).",
    )
    p.add_argument(
        "--channel",
        action="append",
        default=None,
        help="Channel name(s) to include (repeatable).",
    )
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    crawl(
        days_back=None if args.full else args.days,
        max_per_channel=args.max,
        base_dir=args.out,
        server_names=args.server,
        channel_names=args.channel,
    )


if __name__ == "__main__":
    main()
