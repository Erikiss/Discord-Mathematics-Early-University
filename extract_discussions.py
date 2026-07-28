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
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.strptime(value.split(".")[0], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)


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


def find_rendered_ids(ordered: list[dict], window: int = 4) -> set[str]:
    """IDs of user messages that a render bot (TeXit) answered.

    A bot render is linked either through an explicit reply reference or, failing
    that, to the closest preceding LaTeX-bearing user message within ``window``.
    """
    rendered: set[str] = set()
    for idx, msg in enumerate(ordered):
        if not is_render_bot(msg):
            continue
        ref = (msg.get("message_reference") or {}).get("message_id")
        if ref:
            rendered.add(str(ref))
            continue
        for back in range(idx - 1, max(-1, idx - 1 - window), -1):
            candidate = ordered[back]
            if is_bot(candidate):
                continue
            if extract_latex(candidate.get("content", "") or ""):
                rendered.add(str(candidate.get("id")))
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
            msg_id = str(msg.get("id", ""))
            rendered = msg_id in rendered_ids
            engagement = engagement_of(msg)
            score = score_discussion(snippets, content, rendered, engagement)
            if score < min_score:
                continue
            primary = max(snippets, key=len)
            results.append({
                "id": msg_id,
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


def run(base_dir, input_path=None, json_out=None, csv_out=None, min_score=8, guild_id=None):
    messages = load_messages(input_path, base_dir)
    print(f"Analysiere {len(messages)} Nachrichten auf diskutierte Mathematik ...")

    discussions = extract_discussions(messages, min_score=min_score, guild_id=guild_id)

    json_out = json_out or os.path.join(base_dir, "math_discussions.json")
    csv_out = csv_out or os.path.join(base_dir, "math_discussions.csv")
    os.makedirs(os.path.dirname(os.path.abspath(json_out)), exist_ok=True)

    with open(json_out, "w", encoding="utf-8") as fh:
        json.dump(discussions, fh, indent=2, ensure_ascii=False)
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
