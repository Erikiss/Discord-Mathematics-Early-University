#!/usr/bin/env python3
"""Extract discussed math problems (LaTeX/equations) from crawled Discord messages.

The four target channels (calculus, linear-algebra, proofs-and-logic,
computing-software) rarely contain paper links. What they contain are *discussed
problems*: users post LaTeX, which the TeXit bot renders into an image. This
module extracts those discussions -- the LaTeX source, the surrounding
conversation, and metadata -- so each one can be handed to a research agent.

Outputs ``math_discussions.json`` (full records incl. conversation context),
``math_discussions.csv`` (flat overview), and a provider-neutral
``ingest_bundle.json``.  The legacy outputs and Python return value are kept for
backward compatibility.

Standard library only, so it runs unattended in CI.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

INGEST_SCHEMA_VERSION = "agentic-researcher/ingest-bundle/v1"
INGEST_PRODUCER = "Discord-Mathematics-Early-University/extract_discussions.py"
DEFAULT_BLOCK_WINDOW_MINUTES = 45
DEFAULT_CONTEXT_BEFORE = 2
DEFAULT_CONTEXT_AFTER = 4

# --------------------------------------------------------------------------- #
# LaTeX detection
# --------------------------------------------------------------------------- #
# Ordered: display/fenced forms first so a $$...$$ block is not split into two
# empty $...$ matches. Each entry is (name, compiled pattern, group index).
LATEX_PATTERNS = [
    ("fenced", re.compile(r"```(?:math|latex|tex)\s*(.+?)```", re.DOTALL | re.IGNORECASE), 1),
    ("display", re.compile(r"\$\$(.+?)\$\$", re.DOTALL), 1),
    ("bracket", re.compile(r"\\\[(.+?)\\\]", re.DOTALL), 1),
    ("environment", re.compile(r"(\\begin\{[a-zA-Z*]+\}.+?\\end\{[a-zA-Z*]+\})", re.DOTALL), 1),
    ("inline", re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", re.DOTALL), 1),
    ("paren", re.compile(r"\\\((.+?)\\\)", re.DOTALL), 1),
]

# TeX control sequences that signal real mathematical content (not just "$5").
MATH_COMMANDS = [
    r"\int", r"\sum", r"\prod", r"\lim", r"\frac", r"\sqrt", r"\partial",
    r"\infty", r"\begin", r"\matrix", r"\det", r"\binom", r"\oint", r"\nabla",
    r"\forall", r"\exists", r"\in", r"\subset", r"\cup", r"\cap", r"\to",
    r"\mathbb", r"\mathcal", r"\operatorname", r"\sin", r"\cos", r"\tan",
    r"\cot", r"\sec", r"\csc", r"\log", r"\ln", r"\exp", r"\pi", r"\alpha",
    r"\beta", r"\gamma", r"\theta", r"\lambda", r"\sigma", r"\equiv", r"\cdot",
    r"\times", r"\leq", r"\geq", r"\neq", r"\approx", r"\pm", r"\vec", r"\hat",
]

# Words that mark a message as a question / task rather than a statement.
QUESTION_KEYWORDS = [
    "prove", "proof", "show that", "evaluate", "compute", "solve", "find",
    "derive", "calculate", "verify", "how do", "how would", "why is", "why does",
    "stuck", "help", "hint", "any idea", "is it true", "does anyone",
    "counterexample", "converge", "diverge", "closed form",
]

# Topic tags -> keywords, used to label a discussion.
TOPIC_TAGS = {
    "integral": [r"\int", r"\oint", "integral", "antiderivative", "integrate"],
    "series": [r"\sum", r"\prod", "series", "converge", "diverge"],
    "limit": [r"\lim", "limit"],
    "derivative": [
        r"\partial", r"\nabla", "derivative", "differentiate",
        "differentiab", "chain rule",
    ],
    "linear-algebra": [r"\det", r"\matrix", "matrix", "eigen", "vector space",
                       "basis", "rank", "linear map", "determinant"],
    "logic-proof": [r"\forall", r"\exists", "prove", "proof", "lemma", "theorem",
                    "induction", "contradiction", "iff"],
    "probability": ["probability", "expected value", "variance", "random"],
}

# Bot names that render LaTeX in these servers.
RENDER_BOTS = {"texit"}

MIN_LATEX_LEN = 3  # ignore "$x$"-style fragments shorter than this

# These signals do not decide whether text is malicious.  They only annotate
# likely instruction-shaped text so downstream agents continue to treat it as
# quoted, untrusted Discord data rather than as a prompt.
PROMPT_INJECTION_PATTERNS = [
    ("ignore-instructions", re.compile(
        r"\b(?:ignore|disregard|forget)\b.{0,50}\b(?:instructions?|prompts?|rules?)\b",
        re.IGNORECASE | re.DOTALL,
    )),
    ("role-override", re.compile(
        r"\b(?:you are now|act as|system prompt|developer message)\b",
        re.IGNORECASE,
    )),
    ("tool-or-command-request", re.compile(
        r"\b(?:execute|run|call|invoke)\b.{0,40}\b(?:tool|command|shell|terminal|code)\b",
        re.IGNORECASE | re.DOTALL,
    )),
    ("secret-request", re.compile(
        r"\b(?:reveal|print|show|leak|exfiltrate)\b.{0,50}\b"
        r"(?:secret|token|password|credential|api[ -]?key)\b",
        re.IGNORECASE | re.DOTALL,
    )),
]

USER_MENTION_RE = re.compile(r"<@!?\d+>")
ROLE_MENTION_RE = re.compile(r"<@&\d+>")
CHANNEL_MENTION_RE = re.compile(r"<#\d+>")
DISCORD_MESSAGE_LINK_RE = re.compile(
    r"https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/\d+/\d+/\d+",
    re.IGNORECASE,
)


def _strip_code_fences(text: str) -> str:
    """Remove non-math fenced code blocks so they do not pollute LaTeX matches."""
    return re.sub(r"```(?!math|latex|tex)[a-zA-Z]*\s*.*?```", " ", text, flags=re.DOTALL)


def extract_latex(content: str) -> list[str]:
    """Return the LaTeX snippets contained in a message, longest form first.

    Regions already consumed by an earlier (display/fenced) pattern are blanked
    out before later patterns run, so the same characters are never reported
    twice.
    """
    if not content:
        return []
    working = _strip_code_fences(content)
    snippets: list[str] = []
    for _name, pattern, group in LATEX_PATTERNS:
        for match in pattern.finditer(working):
            snippet = (match.group(group) or "").strip()
            if len(snippet) >= MIN_LATEX_LEN:
                snippets.append(snippet)
        # Blank out consumed spans so later patterns cannot re-match them.
        working = pattern.sub(lambda m: " " * len(m.group(0)), working)
    return snippets


def count_math_commands(text: str) -> int:
    lowered = text.lower()
    return sum(lowered.count(cmd) for cmd in MATH_COMMANDS)


def has_question_signal(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    if any(word in lowered for word in QUESTION_KEYWORDS):
        return True
    return "?" in lowered


def topic_tags(text: str) -> list[str]:
    lowered = (text or "").lower()
    tags = [tag for tag, words in TOPIC_TAGS.items()
            if any(word.lower() in lowered for word in words)]
    return tags


def is_bot(msg: dict) -> bool:
    return bool((msg.get("author") or {}).get("bot"))


def author_name(msg: dict) -> str:
    author = msg.get("author") or {}
    return author.get("global_name") or author.get("username") or "Unknown"


def is_render_bot(msg: dict) -> bool:
    return is_bot(msg) and author_name(msg).strip().lower() in RENDER_BOTS


def parse_ts(value: str) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(value.split(".")[0], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)


def canonical_json(value) -> str:
    """Stable JSON encoding used as the basis for every fingerprint."""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def stable_sha256(value) -> str:
    """SHA-256 of a JSON value, stable across runs and input ordering."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def message_key(msg: dict) -> str:
    """Return a public, deterministic key without exposing a Discord snowflake."""
    raw_id = str(msg.get("id") or "")
    if raw_id:
        identity = {"kind": "discord-message", "id": raw_id}
    else:
        author = msg.get("author") or {}
        identity = {
            "kind": "discord-message-fallback",
            "server": str(msg.get("source_server") or "Unknown"),
            "channel": str(msg.get("channel_id") or msg.get("source_channel") or "Unknown"),
            "timestamp": str(msg.get("timestamp") or ""),
            "author": str(author.get("id") or author.get("username") or ""),
            "content": str(msg.get("content") or ""),
        }
    return "msg_" + stable_sha256(identity)[:24]


def message_key_from_id(raw_id: str) -> str | None:
    if not raw_id:
        return None
    return "msg_" + stable_sha256({"kind": "discord-message", "id": str(raw_id)})[:24]


def author_key(msg: dict) -> str:
    """Pseudonymous participant key stable within repeated exports."""
    author = msg.get("author") or {}
    identity = str(author.get("id") or author.get("username") or author_name(msg))
    return "participant_" + stable_sha256({
        "server": str(msg.get("source_server") or "Unknown"),
        "identity": identity,
    })[:16]


def sanitize_public_content(content: str) -> str:
    """Apply deterministic, conservative redaction to public message text.

    This is intentionally labelled basic redaction in the bundle: arbitrary
    prose can still contain personal information and requires review before
    publication.
    """
    text = content or ""
    text = ROLE_MENTION_RE.sub("[role-mention]", text)
    text = USER_MENTION_RE.sub("[user-mention]", text)
    text = CHANNEL_MENTION_RE.sub("[channel-mention]", text)
    return DISCORD_MESSAGE_LINK_RE.sub("[discord-message-link]", text)


def prompt_injection_signals(content: str) -> list[str]:
    """Return stable labels for instruction-shaped, untrusted message text."""
    return [
        label
        for label, pattern in PROMPT_INJECTION_PATTERNS
        if pattern.search(content or "")
    ]


def _clean_sha256(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if value.startswith("sha256:"):
        value = value[7:]
    return value if re.fullmatch(r"[0-9a-f]{64}", value) else None


def _safe_extension(filename: str, url: str = "") -> str:
    suffix = os.path.splitext(filename or "")[1].lower()
    if not suffix:
        suffix = os.path.splitext(urlsplit(url or "").path)[1].lower()
    if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        return suffix
    return ""


def _media_kind(content_type: str, filename: str, url: str = "") -> str:
    guessed = content_type or mimetypes.guess_type(filename or urlsplit(url).path)[0] or ""
    category = guessed.split("/", 1)[0].lower()
    return category if category in {"image", "video", "audio", "text"} else "binary"


def _stable_locator(url: str) -> str:
    """Strip expiring CDN query parameters before hashing a media locator."""
    parts = urlsplit(url or "")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path}" if url else ""


def _public_media_record(raw: dict, *, kind: str, ordinal: int, texit: bool) -> tuple[dict, dict]:
    """Split one attachment/embed image into public metadata and private locators."""
    url = str(raw.get("url") or raw.get("proxy_url") or "")
    filename = str(raw.get("filename") or os.path.basename(urlsplit(url).path) or "")
    content_type = str(
        raw.get("content_type")
        or mimetypes.guess_type(filename or urlsplit(url).path)[0]
        or ""
    )
    raw_identifier = str(raw.get("id") or "")
    media_identity = (
        {"kind": kind, "id": raw_identifier}
        if raw_identifier
        else {
            "kind": kind,
            "ordinal": ordinal,
            "locator": _stable_locator(url),
            "filename": filename,
            "size": raw.get("size"),
        }
    )
    media_id = "media_" + stable_sha256(media_identity)[:24]
    known_content_hash = (
        _clean_sha256(raw.get("sha256"))
        or _clean_sha256(raw.get("content_sha256"))
        or _clean_sha256(raw.get("content_hash"))
    )
    public = {
        "id": media_id,
        "kind": kind,
        "media_type": _media_kind(content_type, filename, url),
        "content_type": content_type or None,
        "extension": _safe_extension(filename, url),
        "size_bytes": raw.get("size") if isinstance(raw.get("size"), int) else None,
        "width": raw.get("width") if isinstance(raw.get("width"), int) else None,
        "height": raw.get("height") if isinstance(raw.get("height"), int) else None,
        "content_sha256": known_content_hash,
        # Discord exports contain URLs, not bytes.  This hash still lets later
        # stages detect repeated locators while keeping the signed URL private.
        "locator_sha256": (
            hashlib.sha256(_stable_locator(url).encode("utf-8")).hexdigest()
            if url
            else None
        ),
        "is_texit_render": bool(texit),
    }
    public["metadata_sha256"] = stable_sha256(public)
    private = {
        "id": media_id,
        "source_id": str(raw.get("id") or ""),
        "filename": filename,
        "url": url,
        "proxy_url": str(raw.get("proxy_url") or ""),
        "ephemeral": bool(raw.get("ephemeral", False)),
    }
    return public, private


def media_records(msg: dict) -> tuple[list[dict], list[dict]]:
    """Return normalized public/private media lists for attachments and embeds."""
    pairs: list[tuple[dict, dict]] = []
    texit = is_render_bot(msg)
    for idx, attachment in enumerate(msg.get("attachments") or []):
        if isinstance(attachment, dict):
            pairs.append(_public_media_record(
                attachment,
                kind="attachment",
                ordinal=idx,
                texit=texit,
            ))

    for embed_idx, embed in enumerate(msg.get("embeds") or []):
        if not isinstance(embed, dict):
            continue
        for image_kind in ("image", "thumbnail"):
            image = embed.get(image_kind)
            if not isinstance(image, dict):
                continue
            pairs.append(_public_media_record(
                image,
                kind=f"embed_{image_kind}",
                ordinal=embed_idx,
                texit=texit,
            ))

    pairs.sort(key=lambda pair: pair[0]["id"])
    return [pair[0] for pair in pairs], [pair[1] for pair in pairs]


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def score_discussion(latex_snippets, content, rendered, engagement) -> int:
    """Heuristic score for "how much of a real math problem is this"."""
    latex_text = " ".join(latex_snippets)
    score = 0
    score += 3 * len(latex_snippets)                       # any LaTeX at all
    score += min(len(latex_text) // 20, 10)                # longer formula = more content
    score += 2 * min(count_math_commands(latex_text), 10)  # real TeX math commands
    if has_question_signal(content):
        score += 4                                          # it is being *asked*
    if rendered:
        score += 5                                          # TeXit rendered it
    score += min(engagement, 10)                            # reactions + replies
    return score


def engagement_of(msg: dict) -> int:
    reactions = msg.get("reactions") or []
    total = 0
    for reaction in reactions:
        if isinstance(reaction, dict):
            total += int(reaction.get("count", 0) or 0)
    return total


# --------------------------------------------------------------------------- #
# Core extraction
# --------------------------------------------------------------------------- #
def group_by_channel(messages):
    channels: dict[tuple, list] = {}
    for msg in messages:
        key = (msg.get("source_server", "Unknown"), msg.get("source_channel", "Unknown"))
        channels.setdefault(key, []).append(msg)
    for key in channels:
        channels[key].sort(key=lambda m: parse_ts(m.get("timestamp", "")))
    return channels


def find_render_links(ordered: list[dict], window: int = 4) -> dict[str, list[str]]:
    """Map source-message IDs to TeXit render-message IDs.

    A bot render is linked either through an explicit reply reference or, failing
    that, to the closest preceding LaTeX-bearing user message within ``window``.
    """
    rendered: dict[str, list[str]] = {}
    for idx, msg in enumerate(ordered):
        if not is_render_bot(msg):
            continue
        render_id = str(msg.get("id") or "")
        ref = (msg.get("message_reference") or {}).get("message_id")
        if ref:
            rendered.setdefault(str(ref), []).append(render_id)
            continue
        for back in range(idx - 1, max(-1, idx - 1 - window), -1):
            candidate = ordered[back]
            if is_bot(candidate):
                continue
            if extract_latex(candidate.get("content", "") or ""):
                rendered.setdefault(str(candidate.get("id") or ""), []).append(render_id)
                break
    return {
        source_id: sorted(set(render_ids))
        for source_id, render_ids in sorted(rendered.items())
    }


def find_rendered_ids(ordered: list[dict], window: int = 4) -> set[str]:
    """Backward-compatible shorthand returning only rendered source IDs."""
    return set(find_render_links(ordered, window))


def conversation_context(ordered, idx, before=2, after=4):
    """A few surrounding messages -- the actual *discussion* around the problem."""
    start = max(0, idx - before)
    end = min(len(ordered), idx + after + 1)
    context = []
    for pos in range(start, end):
        msg = ordered[pos]
        context.append({
            "position": "problem" if pos == idx else ("before" if pos < idx else "after"),
            "author": author_name(msg),
            "is_bot": is_bot(msg),
            "timestamp": msg.get("timestamp", ""),
            "content": (msg.get("content", "") or "")[:1500],
            "message_key": message_key(msg),
            "reply_to_message_key": message_key_from_id(str(
                (msg.get("message_reference") or {}).get("message_id") or ""
            )),
        })
    return context


def build_link(guild_id, channel_id, message_id):
    if not (guild_id and channel_id and message_id):
        return ""
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def extract_discussions(messages, min_score=8, guild_id=None) -> list[dict]:
    results: list[dict] = []
    for (server, channel), ordered in group_by_channel(messages).items():
        rendered_ids = find_rendered_ids(ordered)
        for idx, msg in enumerate(ordered):
            if is_bot(msg):
                continue  # the bot's render is not the discussion itself
            content = msg.get("content", "") or ""
            snippets = extract_latex(content)
            if not snippets:
                continue
            msg_id = str(msg.get("id", ""))
            rendered = msg_id in rendered_ids
            engagement = engagement_of(msg)
            score = score_discussion(snippets, content, rendered, engagement)
            if score < min_score:
                continue
            primary = max(snippets, key=len)
            results.append({
                "id": msg_id,
                "message_key": message_key(msg),
                "score": score,
                "server": server,
                "channel": channel,
                "author": author_name(msg),
                "timestamp": msg.get("timestamp", ""),
                "date": (msg.get("timestamp", "") or "")[:10],
                "rendered_by_bot": rendered,
                "is_question": has_question_signal(content),
                "topics": topic_tags(content + " " + " ".join(snippets)),
                "primary_latex": primary,
                "latex_snippets": snippets,
                "content": content[:2000],
                "engagement": engagement,
                "channel_id": str(msg.get("channel_id", "")),
                "link": build_link(guild_id, msg.get("channel_id"), msg_id),
                "context": conversation_context(ordered, idx),
            })
    results.sort(key=lambda r: (-r["score"], r["timestamp"]))
    return results


# --------------------------------------------------------------------------- #
# Provider-neutral ingest bundle
# --------------------------------------------------------------------------- #
def _deduplicate_messages(messages: list[dict]) -> list[dict]:
    """Deduplicate overlapping crawls without depending on input order."""
    selected: dict[str, tuple[int, str, dict]] = {}
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        encoded = canonical_json(msg)
        preference = (len(encoded), encoded)
        key = message_key(msg)
        current = selected.get(key)
        if current is None or preference > current[:2]:
            selected[key] = (preference[0], preference[1], msg)
    return [selected[key][2] for key in sorted(selected)]


def _thread_id(msg: dict) -> str:
    thread = msg.get("thread")
    if isinstance(thread, dict) and thread.get("id"):
        return str(thread["id"])
    return str(msg.get("thread_id") or "")


def _message_sort_key(record: dict):
    source = record.get("source") or {}
    return (
        str(source.get("server") or ""),
        str(source.get("channel") or ""),
        parse_ts(str(source.get("timestamp") or "")),
        record["id"],
    )


def build_atomic_messages(
    messages: list[dict],
    guild_id: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Normalize raw Discord exports into public/private atomic records."""
    deduplicated = _deduplicate_messages(messages)

    source_to_renders: dict[str, list[str]] = {}
    for _channel_key, ordered in sorted(group_by_channel(deduplicated).items()):
        for source_id, render_ids in find_render_links(ordered).items():
            source_key = message_key_from_id(source_id)
            if not source_key:
                continue
            source_to_renders.setdefault(source_key, []).extend(
                key
                for key in (message_key_from_id(render_id) for render_id in render_ids)
                if key
            )
    source_to_renders = {
        key: sorted(set(value))
        for key, value in sorted(source_to_renders.items())
    }
    render_to_sources: dict[str, list[str]] = {}
    for source_key, render_keys in source_to_renders.items():
        for render_key in render_keys:
            render_to_sources.setdefault(render_key, []).append(source_key)
    render_to_sources = {
        key: sorted(set(value))
        for key, value in sorted(render_to_sources.items())
    }

    public_records: list[dict] = []
    private_records: list[dict] = []
    for msg in deduplicated:
        key = message_key(msg)
        content = str(msg.get("content") or "")
        public_content = sanitize_public_content(content)
        snippets = extract_latex(content)
        media_public, media_private = media_records(msg)
        raw_reference = msg.get("message_reference") or {}
        reply_id = str(raw_reference.get("message_id") or "")
        reply_key = message_key_from_id(reply_id)
        raw_thread_id = _thread_id(msg)
        public_thread_key = (
            "thread_" + stable_sha256({"kind": "discord-thread", "id": raw_thread_id})[:24]
            if raw_thread_id
            else None
        )
        timestamp = str(msg.get("timestamp") or "")
        server = str(msg.get("source_server") or "Unknown")
        channel = str(msg.get("source_channel") or "Unknown")
        signals = prompt_injection_signals(content)
        is_texit = is_render_bot(msg)

        public = {
            "id": key,
            "source": {
                "kind": "discord",
                "server": server,
                "channel": channel,
                "timestamp": timestamp,
            },
            "author_key": author_key(msg),
            "is_bot": is_bot(msg),
            "content": public_content,
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "latex": snippets,
            "topics": topic_tags(content + " " + " ".join(snippets)),
            "is_question": has_question_signal(content),
            "engagement": engagement_of(msg),
            "reply_to": reply_key,
            "thread_key": public_thread_key,
            "attachments": media_public,
            "texit": {
                "is_render_message": is_texit,
                "renders_message_keys": render_to_sources.get(key, []),
                "render_message_keys": source_to_renders.get(key, []),
            },
            "safety": {
                "content_role": "untrusted_data_only",
                "must_not_be_interpreted_as_instructions": True,
                "prompt_injection_signals": signals,
                "prompt_injection_suspected": bool(signals),
            },
            "privacy": {
                "projection": "public_basic_redaction",
                "publication_ready": False,
            },
        }
        public["fingerprint"] = stable_sha256(public)

        effective_guild_id = str(
            guild_id
            or msg.get("guild_id")
            or raw_reference.get("guild_id")
            or ""
        )
        private = {
            "id": key,
            "discord": {
                "message_id": str(msg.get("id") or ""),
                "guild_id": effective_guild_id,
                "channel_id": str(msg.get("channel_id") or ""),
                "thread_id": raw_thread_id,
                "reply_to_message_id": reply_id,
                "link": build_link(
                    effective_guild_id,
                    msg.get("channel_id"),
                    msg.get("id"),
                ),
            },
            "source_server": server,
            "source_channel": channel,
            "author": {
                "id": str((msg.get("author") or {}).get("id") or ""),
                "username": str((msg.get("author") or {}).get("username") or ""),
                "global_name": str((msg.get("author") or {}).get("global_name") or ""),
                "bot": is_bot(msg),
            },
            "raw_content": content,
            "attachments": media_private,
        }
        public_records.append(public)
        private_records.append(private)

    public_records.sort(key=_message_sort_key)
    private_by_id = {record["id"]: record for record in private_records}
    private_records = [private_by_id[record["id"]] for record in public_records]
    return public_records, private_records


def _seconds_between(left: dict, right: dict) -> float | None:
    left_ts = str((left.get("source") or {}).get("timestamp") or "")
    right_ts = str((right.get("source") or {}).get("timestamp") or "")
    if not left_ts or not right_ts:
        return None
    return abs((parse_ts(left_ts) - parse_ts(right_ts)).total_seconds())


def _discussion_seed_key(discussion: dict) -> str | None:
    if discussion.get("message_key"):
        return str(discussion["message_key"])
    return message_key_from_id(str(discussion.get("id") or ""))


def build_discussion_blocks(
    public_messages: list[dict],
    discussions: list[dict],
    *,
    block_window_minutes: int = DEFAULT_BLOCK_WINDOW_MINUTES,
    context_before: int = DEFAULT_CONTEXT_BEFORE,
    context_after: int = DEFAULT_CONTEXT_AFTER,
) -> list[dict]:
    """Group qualifying seeds by replies and nearby shared mathematical topics."""
    if block_window_minutes < 0 or context_before < 0 or context_after < 0:
        raise ValueError("block window and context sizes must be non-negative")

    by_id = {record["id"]: record for record in public_messages}
    seed_info = {
        seed_key: discussion
        for discussion in discussions
        for seed_key in [_discussion_seed_key(discussion)]
        if seed_key and seed_key in by_id
    }
    if not seed_info:
        return []

    channel_records: dict[tuple[str, str], list[dict]] = {}
    for record in public_messages:
        source = record["source"]
        channel_records.setdefault(
            (source["server"], source["channel"]),
            [],
        ).append(record)
    for records in channel_records.values():
        records.sort(key=_message_sort_key)

    # Reply components are computed over every atomic message, not just seeds,
    # so a prose answer between two formula posts can connect them.
    reply_neighbors: dict[str, set[str]] = {key: set() for key in by_id}
    for record in public_messages:
        target = record.get("reply_to")
        if target in by_id:
            reply_neighbors[record["id"]].add(target)
            reply_neighbors[target].add(record["id"])

    reply_component: dict[str, set[str]] = {}
    unseen = set(by_id)
    while unseen:
        start = min(unseen)
        stack = [start]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(sorted(reply_neighbors[current] - component, reverse=True))
        unseen -= component
        for member in component:
            reply_component[member] = component

    parent = {key: key for key in seed_info}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str):
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            return
        # Stable representative independent of iteration order.
        low, high = sorted((left_root, right_root))
        parent[high] = low

    for seed in sorted(seed_info):
        related_seeds = sorted(reply_component.get(seed, {seed}) & set(seed_info))
        for related in related_seeds[1:]:
            union(related_seeds[0], related)

    max_gap_seconds = block_window_minutes * 60
    for (_server, _channel), records in sorted(channel_records.items()):
        seeds = [record for record in records if record["id"] in seed_info]
        for index, left in enumerate(seeds):
            for right in seeds[index + 1:]:
                gap = _seconds_between(left, right)
                if gap is None or gap > max_gap_seconds:
                    if gap is not None and gap > max_gap_seconds:
                        break
                    continue
                shared_topics = set(left["topics"]) & set(right["topics"])
                # Unknown-topic formulae are joined only in a tight ten-minute
                # window; recognized topics may use the configured wider window.
                tight_unknown_topic = (
                    not left["topics"]
                    and not right["topics"]
                    and gap <= min(max_gap_seconds, 10 * 60)
                )
                if shared_topics or tight_unknown_topic:
                    union(left["id"], right["id"])

    grouped_seeds: dict[str, list[str]] = {}
    for seed in sorted(seed_info):
        grouped_seeds.setdefault(find(seed), []).append(seed)

    blocks: list[dict] = []
    for seeds in grouped_seeds.values():
        seeds.sort(key=lambda key: _message_sort_key(by_id[key]))
        first_seed = by_id[seeds[0]]
        source_key = (
            first_seed["source"]["server"],
            first_seed["source"]["channel"],
        )
        ordered = channel_records[source_key]
        positions = {record["id"]: idx for idx, record in enumerate(ordered)}
        included: set[str] = set(seeds)
        for seed in seeds:
            included.update(reply_component.get(seed, {seed}))
            seed_pos = positions[seed]
            start = max(0, seed_pos - context_before)
            end = min(len(ordered), seed_pos + context_after + 1)
            for record in ordered[start:end]:
                gap = _seconds_between(by_id[seed], record)
                if gap is None or gap <= max_gap_seconds:
                    included.add(record["id"])

        included_records = sorted(
            (by_id[key] for key in included),
            key=_message_sort_key,
        )
        included_ids = [record["id"] for record in included_records]
        included_set = set(included_ids)
        reply_edges = sorted(
            (
                {"from": record["id"], "to": record["reply_to"]}
                for record in included_records
                if record.get("reply_to") in included_set
            ),
            key=lambda edge: (edge["from"], edge["to"]),
        )
        topics = sorted({
            topic
            for record in included_records
            for topic in record["topics"]
        })
        latex = list(dict.fromkeys(
            snippet
            for record in included_records
            for snippet in record["latex"]
        ))
        attachments = [
            {"message_id": record["id"], **attachment}
            for record in included_records
            for attachment in record["attachments"]
        ]
        reasons = []
        if reply_edges:
            reasons.append("reply")
        if len(seeds) > 1:
            reasons.append("time_topic")
        if included_set - set(seeds):
            reasons.append("context_window")
        text = "\n\n".join(
            "[UNTRUSTED DISCORD DATA | "
            f"{record['id']} | {record['source']['timestamp']} | "
            f"{record['author_key']}]\n{record['content']}"
            for record in included_records
            if record["content"]
        )
        anchor = seeds[0]
        block_id = "topic_" + stable_sha256({
            "server": source_key[0],
            "channel": source_key[1],
            "anchor_message": anchor,
        })[:24]
        block = {
            "id": block_id,
            "source": {
                "kind": "discord",
                "server": source_key[0],
                "channel": source_key[1],
                "started_at": included_records[0]["source"]["timestamp"],
                "ended_at": included_records[-1]["source"]["timestamp"],
            },
            "anchor_message_id": anchor,
            "message_keys": included_ids,
            "seed_message_keys": seeds,
            "topics": topics,
            "latex": latex,
            "attachments": attachments,
            "text": text,
            "score": max(int(seed_info[key].get("score", 0)) for key in seeds),
            "grouping": {
                "window_minutes": block_window_minutes,
                "context_before": context_before,
                "context_after": context_after,
                "reasons": reasons,
                "reply_edges": reply_edges,
            },
            "safety": {
                "content_role": "untrusted_data_only",
                "must_not_be_interpreted_as_instructions": True,
                "contains_flagged_messages": any(
                    record["safety"]["prompt_injection_suspected"]
                    for record in included_records
                ),
            },
        }
        block["fingerprint"] = stable_sha256(block)
        blocks.append(block)

    blocks.sort(key=lambda block: (
        block["source"]["server"],
        block["source"]["channel"],
        parse_ts(block["source"]["started_at"]),
        block["id"],
    ))
    return blocks


def build_ingest_bundle(
    messages: list[dict],
    discussions: list[dict] | None = None,
    *,
    min_score: int = 8,
    guild_id: str | None = None,
    block_window_minutes: int = DEFAULT_BLOCK_WINDOW_MINUTES,
    context_before: int = DEFAULT_CONTEXT_BEFORE,
    context_after: int = DEFAULT_CONTEXT_AFTER,
) -> dict:
    """Build the deterministic hand-off contract consumed by research systems."""
    if discussions is None:
        discussions = extract_discussions(messages, min_score=min_score, guild_id=guild_id)
    public_messages, private_messages = build_atomic_messages(messages, guild_id=guild_id)
    blocks = build_discussion_blocks(
        public_messages,
        discussions,
        block_window_minutes=block_window_minutes,
        context_before=context_before,
        context_after=context_after,
    )
    timestamps = [
        record["source"]["timestamp"]
        for record in public_messages
        if record["source"]["timestamp"]
    ]
    channels = sorted({
        (record["source"]["server"], record["source"]["channel"])
        for record in public_messages
    })
    private_by_id = {record["id"]: record for record in private_messages}
    private_blocks = [
        {
            "id": block["id"],
            "discord_message_ids": [
                private_by_id[key]["discord"]["message_id"]
                for key in block["message_keys"]
            ],
        }
        for block in blocks
    ]
    source = {
        "kind": "discord",
        "servers": sorted({server for server, _channel in channels}),
        "channels": [
            {"server": server, "channel": channel}
            for server, channel in channels
        ],
        "message_count": len(public_messages),
        "discussion_count": len(discussions),
        "block_count": len(blocks),
        "started_at": min(timestamps, key=parse_ts) if timestamps else None,
        "ended_at": max(timestamps, key=parse_ts) if timestamps else None,
    }
    bundle = {
        "schema_version": INGEST_SCHEMA_VERSION,
        "producer": INGEST_PRODUCER,
        "source": source,
        # `items` is the deliberately provider-neutral ingestion surface.  The
        # same records remain under `public.blocks` for consumers that need the
        # complete public/private partition.
        "items": blocks,
        "public": {
            "messages": public_messages,
            "blocks": blocks,
            "redaction": {
                "level": "basic",
                "publication_ready": False,
                "removed": [
                    "discord_user_mentions",
                    "discord_role_mentions",
                    "discord_channel_mentions",
                    "discord_message_links",
                    "author_names_and_ids",
                    "attachment_urls_and_filenames",
                ],
            },
        },
        "private": {
            "handling": "restricted_input_do_not_publish",
            "source": {"guild_id": str(guild_id or "")},
            "messages": private_messages,
            "blocks": private_blocks,
        },
        "safety": {
            "discord_content_is_untrusted": True,
            "all_message_text_is_data_only": True,
            "prompt_injection_detection_is_advisory": True,
        },
    }
    public_identity = {
        "schema_version": bundle["schema_version"],
        "source": source,
        "message_fingerprints": [
            record["fingerprint"] for record in public_messages
        ],
        "item_fingerprints": [item["fingerprint"] for item in blocks],
    }
    bundle["bundle_id"] = "discord_math_" + stable_sha256(public_identity)[:24]
    bundle["bundle_fingerprint"] = stable_sha256(bundle)
    return bundle


def write_bundle(bundle: dict, path: str):
    """Write a byte-stable JSON bundle (including a final newline)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(bundle, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
CSV_FIELDS = [
    "Score", "Channel", "Author", "Date", "Topics", "Rendered", "IsQuestion",
    "PrimaryLatex", "Content", "Link",
]


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "Score": row["score"],
                "Channel": row["channel"],
                "Author": row["author"],
                "Date": row["date"],
                "Topics": ",".join(row["topics"]),
                "Rendered": "yes" if row["rendered_by_bot"] else "no",
                "IsQuestion": "yes" if row["is_question"] else "no",
                # Keep the CSV one-line-per-row: newlines would break Excel import.
                "PrimaryLatex": row["primary_latex"].replace("\n", " ")[:500],
                "Content": row["content"].replace("\n", " ")[:500],
                "Link": row["link"],
            })


def load_messages(input_path, base_dir):
    """Load the merged JSON if present, else walk the per-channel exports."""
    if input_path and os.path.isfile(input_path):
        with open(input_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    messages = []
    if not os.path.isdir(base_dir):
        print(f"Kein Export-Ordner gefunden: {base_dir}")
        return messages
    for root, _dirs, files in os.walk(base_dir):
        if os.path.abspath(root) == os.path.abspath(base_dir):
            continue
        for name in sorted(files):
            if not name.endswith(".json"):
                continue
            path = os.path.join(root, name)
            channel = name[: -len(".json")]
            if channel.endswith(".INCOMPLETE"):
                channel = channel[: -len(".INCOMPLETE")]
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError) as exc:
                print(f"Fehler beim Lesen von {path}: {exc}")
                continue
            if not isinstance(data, list):
                continue
            for msg in data:
                if isinstance(msg, dict):
                    msg.setdefault("source_server", os.path.basename(root))
                    msg.setdefault("source_channel", channel)
                    messages.append(msg)
    return messages


def run(
    base_dir,
    input_path=None,
    json_out=None,
    csv_out=None,
    min_score=8,
    guild_id=None,
    bundle_out=None,
    block_window_minutes=DEFAULT_BLOCK_WINDOW_MINUTES,
    context_before=DEFAULT_CONTEXT_BEFORE,
    context_after=DEFAULT_CONTEXT_AFTER,
    emit_bundle=True,
):
    messages = load_messages(input_path, base_dir)
    print(f"Analysiere {len(messages)} Nachrichten auf diskutierte Mathematik ...")

    discussions = extract_discussions(messages, min_score=min_score, guild_id=guild_id)

    json_out = json_out or os.path.join(base_dir, "math_discussions.json")
    csv_out = csv_out or os.path.join(base_dir, "math_discussions.csv")
    os.makedirs(os.path.dirname(os.path.abspath(json_out)), exist_ok=True)

    with open(json_out, "w", encoding="utf-8") as fh:
        json.dump(discussions, fh, indent=2, ensure_ascii=False)
    write_csv(discussions, csv_out)

    bundle = None
    if emit_bundle:
        bundle_out = bundle_out or os.path.join(base_dir, "ingest_bundle.json")
        bundle = build_ingest_bundle(
            messages,
            discussions,
            min_score=min_score,
            guild_id=guild_id,
            block_window_minutes=block_window_minutes,
            context_before=context_before,
            context_after=context_after,
        )
        write_bundle(bundle, bundle_out)

    print(f"{len(discussions)} diskutierte Probleme gefunden (min-score {min_score}).")
    print(f"  JSON: {json_out}")
    print(f"  CSV:  {csv_out}")
    if bundle is not None:
        print(
            f"  Bundle: {bundle_out} "
            f"({len(bundle['public']['messages'])} Nachrichten, "
            f"{len(bundle['items'])} Themenblöcke)"
        )
    if discussions:
        by_channel: dict[str, int] = {}
        for row in discussions:
            by_channel[row["channel"]] = by_channel.get(row["channel"], 0) + 1
        print("  Pro Kanal: " + ", ".join(f"#{k}: {v}" for k, v in sorted(by_channel.items())))
        print("\n--- Top 5 ---")
        for row in discussions[:5]:
            print(f"  [{row['score']:3d}] #{row['channel']}: {row['primary_latex'][:70]}")
    return discussions


def build_arg_parser():
    p = argparse.ArgumentParser(
        description="Extract discussed math problems (LaTeX) from crawled Discord messages.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--base-dir", default="discord_exports", help="Crawl output directory.")
    p.add_argument("--input", default=None,
                   help="Merged JSON to read (default: <base-dir>/MATH_MERGED.json if present).")
    p.add_argument("--json-out", default=None, help="Path for the discussions JSON.")
    p.add_argument("--csv-out", default=None, help="Path for the discussions CSV.")
    p.add_argument(
        "--bundle-out",
        default=None,
        help="Path for the provider-neutral ingest bundle.",
    )
    p.add_argument(
        "--no-bundle",
        action="store_true",
        help="Do not emit ingest_bundle.json (legacy-only mode).",
    )
    p.add_argument("--min-score", type=int, default=8,
                   help="Minimum heuristic score for a message to count as a discussed problem.")
    p.add_argument("--guild-id", default=None,
                   help="Discord server ID, used to build clickable message links.")
    p.add_argument(
        "--block-window-minutes",
        type=int,
        default=DEFAULT_BLOCK_WINDOW_MINUTES,
        help="Maximum time gap for topic-based discussion grouping.",
    )
    p.add_argument(
        "--context-before",
        type=int,
        default=DEFAULT_CONTEXT_BEFORE,
        help="Atomic messages before each math seed included in its block.",
    )
    p.add_argument(
        "--context-after",
        type=int,
        default=DEFAULT_CONTEXT_AFTER,
        help="Atomic messages after each math seed included in its block.",
    )
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    input_path = args.input
    if input_path is None:
        default_merged = os.path.join(args.base_dir, "MATH_MERGED.json")
        if os.path.isfile(default_merged):
            input_path = default_merged
    run(
        args.base_dir,
        input_path,
        args.json_out,
        args.csv_out,
        args.min_score,
        args.guild_id,
        args.bundle_out,
        args.block_window_minutes,
        args.context_before,
        args.context_after,
        not args.no_bundle,
    )


if __name__ == "__main__":
    main()
