#!/usr/bin/env python3
"""Extract discussed math problems (LaTeX/equations) from crawled Discord messages.

The four target channels (calculus, linear-algebra, proofs-and-logic,
computing-software) rarely contain paper links. What they contain are *discussed
problems*: users post LaTeX, which the TeXit bot renders into an image. This
module extracts those discussions -- the LaTeX source, the surrounding
conversation, and metadata -- so each one can be handed to a research agent.

Outputs ``math_discussions.json`` (full records incl. conversation context) and
``math_discussions.csv`` (flat overview).

Standard library only, so it runs unattended in CI.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# LaTeX detection
# --------------------------------------------------------------------------- #
# Every fenced block is located once, in a single left-to-right pass, so a
# closing ``` can never be mistaken for an opening one. Group 1 is the language
# tag, group 2 the body.
FENCE_RE = re.compile(r"```([a-zA-Z0-9_+-]*)[^\S\n]*\n?(.*?)```", re.DOTALL)
MATH_LANGS = {"math", "latex", "tex"}

# Tried in priority order; a region already claimed by a higher-priority pattern
# is never re-matched. ``environment`` deliberately comes last so a delimited
# formula that *contains* \begin{...} is still captured whole.
LATEX_PATTERNS = [
    ("display", re.compile(r"\$\$(.+?)\$\$", re.DOTALL), 1),
    ("bracket", re.compile(r"\\\[(.+?)\\\]", re.DOTALL), 1),
    # Body may not span lines or contain a further '$'. The lookarounds reject a
    # '$' that is escaped or glued to a word, which is what keeps prices such as
    # "$20 and $30 used" from being paired up as a formula.
    ("inline", re.compile(r"(?<![\\$\w])\$(?!\$)([^\n$]{1,400}?)(?<![\\$])\$(?!\$)(?!\w)"), 1),
    ("paren", re.compile(r"\\\((.+?)\\\)", re.DOTALL), 1),
    # Backreference: \end{} must close the same environment that \begin{} opened,
    # so nested environments are captured whole instead of truncated.
    ("environment", re.compile(r"(\\begin\{([a-zA-Z*]+)\}.*?\\end\{\2\})", re.DOTALL), 1),
]

# Delimiters that are ambiguous with ordinary prose need a math-content gate.
GATED_PATTERNS = {"inline", "paren"}
MATH_CHARS = set("\\^_=<>+/*")

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
    "derivative": [r"\partial", r"\nabla", "derivative", "differentiate"],
    "linear-algebra": [r"\det", r"\matrix", "matrix", "eigen", "vector space",
                       "basis", "rank", "linear map", "determinant"],
    "logic-proof": [r"\forall", r"\exists", "prove", "proof", "lemma", "theorem",
                    "induction", "contradiction", "iff"],
    "probability": ["probability", "expected value", "variance", "random"],
}

# Bot names that render LaTeX in these servers.
RENDER_BOTS = {"texit"}

MIN_LATEX_LEN = 3  # ignore "$x$"-style fragments shorter than this


def _looks_like_math(snippet: str) -> bool:
    """Gate for ambiguous delimiters: does this actually look like mathematics?"""
    return any(ch in MATH_CHARS for ch in snippet) or bool(re.search(r"\\[a-zA-Z]", snippet))


def _overlaps(span, claimed) -> bool:
    start, end = span
    return any(start < c_end and c_start < end for c_start, c_end in claimed)


def extract_latex(content: str) -> list[str]:
    """Return the LaTeX snippets contained in a message.

    Fenced blocks are resolved first in one pass (math fences yield a snippet,
    other fences are merely claimed so nothing inside them is matched). The
    remaining patterns are then applied in priority order, and any match that
    overlaps an already-claimed region is skipped -- so the same characters are
    never reported twice and no formula is shredded into fragments.
    """
    if not content:
        return []

    snippets: list[str] = []
    claimed: list[tuple[int, int]] = []

    for match in FENCE_RE.finditer(content):
        claimed.append(match.span())
        if (match.group(1) or "").lower() in MATH_LANGS:
            snippet = (match.group(2) or "").strip()
            if len(snippet) >= MIN_LATEX_LEN:
                snippets.append(snippet)

    for name, pattern, group in LATEX_PATTERNS:
        pos = 0
        while pos < len(content):
            match = pattern.search(content, pos)
            if not match:
                break
            span = match.span()
            snippet = (match.group(group) or "").strip()
            rejected = (
                _overlaps(span, claimed)
                or len(snippet) < MIN_LATEX_LEN
                or (name in GATED_PATTERNS and not _looks_like_math(snippet))
            )
            if rejected:
                # Resume just past the OPENING delimiter, not past the whole
                # rejected match: otherwise a rejected "$20, anyway $" would
                # swallow the opening '$' of the real formula that follows it.
                pos = span[0] + 1
                continue
            claimed.append(span)
            snippets.append(snippet)
            pos = span[1]

    return snippets


MATH_COMMAND_SET = {cmd.lower() for cmd in MATH_COMMANDS}


def count_math_commands(text: str) -> int:
    """Count TeX control sequences, tokenised.

    Substring counting would score ``\\int`` and ``\\infty`` twice each, because
    ``\\in`` is a prefix of both.
    """
    return sum(1 for token in re.findall(r"\\[a-zA-Z]+", text)
               if token.lower() in MATH_COMMAND_SET)


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


def msg_id(msg: dict) -> str:
    """One canonical id representation, so linking never compares 'None' to ''."""
    return str(msg.get("id") or "")


def is_bot(msg: dict) -> bool:
    return bool((msg.get("author") or {}).get("bot"))


def author_name(msg: dict) -> str:
    author = msg.get("author") or {}
    return author.get("global_name") or author.get("username") or "Unknown"


def is_render_bot(msg: dict) -> bool:
    return is_bot(msg) and author_name(msg).strip().lower() in RENDER_BOTS


def parse_ts(value: str) -> datetime:
    """Parse a Discord timestamp, always returning a timezone-aware datetime.

    Mixing naive and aware datetimes would make the per-channel sort raise
    TypeError and abort the whole extraction, so every path is normalised.
    """
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    parsed = None
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        try:
            parsed = datetime.strptime(value.split(".")[0], "%Y-%m-%dT%H:%M:%S")
        except (ValueError, TypeError, AttributeError):
            return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def score_discussion(latex_snippets, content, rendered, engagement) -> int:
    """Heuristic score for "how much of a real math problem is this"."""
    latex_text = " ".join(latex_snippets)
    score = 0
    # Capped like every other term: a message with a dozen tiny $x$ fragments
    # must not outrank a genuine problem.
    score += 3 * min(len(latex_snippets), 5)
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


MAX_RENDER_GAP_SECONDS = 60  # TeXit answers within seconds


def find_rendered_ids(ordered: list[dict], window: int = 4) -> set[str]:
    """IDs of user messages that a render bot (TeXit) answered.

    An explicit reply reference wins. Without one, the render is paired with the
    *oldest not-yet-paired* LaTeX message in the preceding window, which matches
    the order TeXit renders in: when several users post formulas in a burst, k
    renders pair with k distinct messages instead of all collapsing onto the
    most recent one. A pairing is only accepted if the render followed within
    ``MAX_RENDER_GAP_SECONDS``, so a stale message in a quiet channel is not
    retroactively marked as rendered.
    """
    rendered: set[str] = set()
    consumed: set[int] = set()  # positions already paired with some render

    for idx, msg in enumerate(ordered):
        if not is_render_bot(msg):
            continue
        ref = (msg.get("message_reference") or {}).get("message_id")
        if ref:
            rendered.add(str(ref))
            continue

        bot_time = parse_ts(msg.get("timestamp", ""))
        start = max(0, idx - window)
        for back in range(start, idx):  # oldest first
            if back in consumed:
                continue
            candidate = ordered[back]
            if is_bot(candidate):
                continue
            if not extract_latex(candidate.get("content", "") or ""):
                continue
            gap = (bot_time - parse_ts(candidate.get("timestamp", ""))).total_seconds()
            if not (0 <= gap <= MAX_RENDER_GAP_SECONDS):
                continue
            cid = msg_id(candidate)
            if cid:
                rendered.add(cid)
            consumed.add(back)
            break
    return rendered


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
            this_id = msg_id(msg)
            rendered = bool(this_id) and this_id in rendered_ids
            engagement = engagement_of(msg)
            score = score_discussion(snippets, content, rendered, engagement)
            if score < min_score:
                continue
            primary = max(snippets, key=len)
            results.append({
                "id": this_id,
                "score": score,
                "server": server,
                "channel": channel,
                "author": author_name(msg),
                # Normalised: a JSON null here would break the final sort.
                "timestamp": msg.get("timestamp") or "",
                "date": (msg.get("timestamp") or "")[:10],
                "rendered_by_bot": rendered,
                "is_question": has_question_signal(content),
                "topics": topic_tags(content + " " + " ".join(snippets)),
                "primary_latex": primary,
                "latex_snippets": snippets,
                "content": content[:2000],
                "engagement": engagement,
                "channel_id": str(msg.get("channel_id", "") or ""),
                "link": build_link(guild_id, msg.get("channel_id"), this_id),
                "context": conversation_context(ordered, idx),
            })
    results.sort(key=lambda r: (-r["score"], r["timestamp"]))
    return results


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
CSV_FIELDS = [
    "Score", "Channel", "Author", "Date", "Topics", "Rendered", "IsQuestion",
    "PrimaryLatex", "Content", "Link",
]


def _csv_safe(value):
    """Neutralise spreadsheet formula injection (CWE-1236).

    Message text is fully attacker-controlled; a cell starting with =, +, -, @
    or a control character is executed as a formula when opened in Excel.
    """
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def write_csv(rows, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "Score": row["score"],
                "Channel": _csv_safe(row["channel"]),
                "Author": _csv_safe(row["author"]),
                "Date": _csv_safe(row["date"]),
                "Topics": _csv_safe(",".join(row["topics"])),
                "Rendered": "yes" if row["rendered_by_bot"] else "no",
                "IsQuestion": "yes" if row["is_question"] else "no",
                # Keep the CSV one-line-per-row: newlines would break Excel import.
                "PrimaryLatex": _csv_safe(" ".join(row["primary_latex"].split())[:500]),
                "Content": _csv_safe(" ".join(row["content"].split())[:500]),
                "Link": _csv_safe(row["link"]),
            })


def load_messages(input_path, base_dir):
    """Load the merged JSON if present, else walk the per-channel exports."""
    if input_path:
        # An explicitly requested file that is missing is an error, not a reason
        # to silently fall back and report "0 discussions" with exit code 0.
        if not os.path.isfile(input_path):
            raise SystemExit(f"Eingabedatei nicht gefunden: {input_path}")
        with open(input_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise SystemExit(f"Eingabedatei enthält keine Nachrichtenliste: {input_path}")
        return [m for m in data if isinstance(m, dict)]

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


def run(base_dir, input_path=None, json_out=None, csv_out=None, min_score=8, guild_id=None):
    messages = load_messages(input_path, base_dir)
    print(f"Analysiere {len(messages)} Nachrichten auf diskutierte Mathematik ...")

    discussions = extract_discussions(messages, min_score=min_score, guild_id=guild_id)

    json_out = json_out or os.path.join(base_dir, "math_discussions.json")
    csv_out = csv_out or os.path.join(base_dir, "math_discussions.csv")
    os.makedirs(os.path.dirname(os.path.abspath(json_out)), exist_ok=True)

    # Serialise fully, then replace atomically: a failure part-way through must
    # not leave a truncated file where a good one used to be.
    payload = json.dumps(discussions, indent=2, ensure_ascii=False)
    tmp_path = json_out + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(payload)
    os.replace(tmp_path, json_out)
    write_csv(discussions, csv_out)

    print(f"{len(discussions)} diskutierte Probleme gefunden (min-score {min_score}).")
    print(f"  JSON: {json_out}")
    print(f"  CSV:  {csv_out}")
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
    p.add_argument("--min-score", type=int, default=8,
                   help="Minimum heuristic score for a message to count as a discussed problem.")
    p.add_argument("--guild-id", default=None,
                   help="Discord server ID, used to build clickable message links.")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    input_path = args.input
    if input_path is None:
        default_merged = os.path.join(args.base_dir, "MATH_MERGED.json")
        if os.path.isfile(default_merged):
            input_path = default_merged
    run(args.base_dir, input_path, args.json_out, args.csv_out, args.min_score, args.guild_id)


if __name__ == "__main__":
    main()
