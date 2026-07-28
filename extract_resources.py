#!/usr/bin/env python3
"""Merge crawled channel JSONs and extract math resource links to CSV.

Reads the per-channel JSON files produced by ``discord_math_crawl.py``, merges
them into a single list (tagging each message with its source server/channel),
writes that merged JSON, then scans the messages for links to math/science
resources and writes a de-duplicated CSV.

This mirrors the merge + extraction cells of
``notebooks/discord_math_crawl.ipynb``, but uses only the standard library (no
pandas, no Colab/Drive) so it can run unattended in GitHub Actions.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re

# --------------------------------------------------------------------------- #
# Domains / keywords (same set as the notebook's extraction cell)
# --------------------------------------------------------------------------- #
# 1) Hard math/science domains (unambiguous).
SCIENCE_DOMAINS = [
    "arxiv.org", "openreview.net", "projecteuclid.org", "ams.org",
    "mathoverflow.net", "math.stackexchange.com", "oeis.org",
    "ncatlab.org", "numdam.org", "springer.com", "sciencedirect.com",
    "tandfonline.com", "jstor.org", "doi.org", "nature.com", "ieee.org",
    "wikipedia.org/wiki", "encyclopediaofmath.org",
]
# 2) Social/code domains (only count with a context signal).
SOCIAL_DOMAINS = [
    "twitter.com", "x.com", "youtube.com", "youtu.be", "reddit.com", "github.com",
]
# 3) Signal words that turn a social/code link into a lead.
SIGNAL_KEYWORDS = [
    "theorem", "proof", "lemma", "paper", "preprint", "abstract", "textbook",
    "lecture", "notes", "course", "problem set", "exercise", "solution",
    "calculus", "linear algebra", "eigen", "matrix", "logic", "definition",
    "integral", "derivative", "convergence", "dataset", "algorithm",
]

URL_PATTERN = re.compile(r"https?://\S+")

CSV_FIELDS = [
    "Confidence", "Type", "Title", "Link", "Context",
    "Author", "Server", "Channel", "Date",
]


def contains_keyword(text: str) -> bool:
    if not text:
        return False
    text = text.lower()
    return any(word in text for word in SIGNAL_KEYWORDS)


# --------------------------------------------------------------------------- #
# Merge
# --------------------------------------------------------------------------- #
def merge_exports(base_dir: str):
    """Collect all per-channel message JSONs under ``base_dir`` subfolders.

    Files that live directly in ``base_dir`` (e.g. the merged JSON or the CSV we
    write ourselves) are skipped; only files inside server subfolders are read.
    Each message is tagged with ``source_server`` and ``source_channel``.
    """
    master: list[dict] = []
    file_count = 0
    if not os.path.isdir(base_dir):
        print(f"Kein Export-Ordner gefunden: {base_dir}")
        return master, file_count

    for root, _dirs, files in os.walk(base_dir):
        if os.path.abspath(root) == os.path.abspath(base_dir):
            continue  # skip files sitting at the root (our own outputs)
        for name in files:
            if not name.endswith(".json"):
                continue
            path = os.path.join(root, name)
            server_name = os.path.basename(root)
            channel_name = name[: -len(".json")]
            if channel_name.endswith(".INCOMPLETE"):
                channel_name = channel_name[: -len(".INCOMPLETE")]
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError) as exc:
                print(f"Fehler beim Lesen von {path}: {exc}")
                continue
            if not isinstance(data, list):
                continue
            for msg in data:
                if not isinstance(msg, dict):
                    continue
                msg["source_server"] = server_name
                msg["source_channel"] = channel_name
                master.append(msg)
            file_count += 1

    print(f"Merge fertig: {len(master)} Nachrichten aus {file_count} Dateien.")
    return master, file_count


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def extract_resources(messages) -> list[dict]:
    leads: list[dict] = []
    for msg in messages:
        content = msg.get("content", "") or ""
        embeds = msg.get("embeds", []) or []
        server = msg.get("source_server", "Unknown")
        channel = msg.get("source_channel", "Unknown")
        author = (msg.get("author") or {}).get("username", "Unknown")
        timestamp = msg.get("timestamp", "") or ""

        # A) Embeds first (best source of titles/abstracts).
        for embed in embeds:
            if not isinstance(embed, dict):
                continue
            url = embed.get("url", "") or ""
            title = embed.get("title", "") or ""
            desc = embed.get("description", "") or ""
            if not url:
                continue
            is_science = any(d in url for d in SCIENCE_DOMAINS)
            is_social_hit = any(d in url for d in SOCIAL_DOMAINS) and (
                contains_keyword(title)
                or contains_keyword(desc)
                or contains_keyword(content)
            )
            if is_science or is_social_hit:
                leads.append({
                    "Confidence": "High" if is_science else "Medium",
                    "Type": "Embed",
                    "Title": title or "No Title in Embed",
                    "Link": url,
                    "Context": (desc[:200] + "...") if desc else content[:200],
                    "Author": author,
                    "Server": server,
                    "Channel": channel,
                    "Date": timestamp[:10],
                })

        # B) Raw text URLs (when there is no embed).
        for url in URL_PATTERN.findall(content):
            if any(d in url for d in SCIENCE_DOMAINS):
                leads.append({
                    "Confidence": "High",
                    "Type": "Direct Link",
                    "Title": "Unknown (Raw Link)",
                    "Link": url,
                    "Context": content[:300],
                    "Author": author,
                    "Server": server,
                    "Channel": channel,
                    "Date": timestamp[:10],
                })
            elif any(d in url for d in SOCIAL_DOMAINS) and contains_keyword(content):
                leads.append({
                    "Confidence": "Low/Medium",
                    "Type": "Social Signal",
                    "Title": "Social Discussion",
                    "Link": url,
                    "Context": content[:300],
                    "Author": author,
                    "Server": server,
                    "Channel": channel,
                    "Date": timestamp[:10],
                })
    return leads


def dedupe_by_link(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for row in rows:
        link = row.get("Link", "")
        if link in seen:
            continue
        seen.add(link)
        unique.append(row)
    return unique


def write_csv(rows: list[dict], path: str) -> None:
    # utf-8-sig + ';' delimiter for Excel compatibility (matches the notebook).
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def run(base_dir: str, merged_out: str, csv_out: str) -> dict:
    master, _ = merge_exports(base_dir)

    os.makedirs(os.path.dirname(os.path.abspath(merged_out)), exist_ok=True)
    with open(merged_out, "w", encoding="utf-8") as fh:
        json.dump(master, fh, indent=4, ensure_ascii=False)
    print(f"Master-Datei gespeichert: {merged_out}")

    leads = extract_resources(master)
    unique = dedupe_by_link(leads)
    write_csv(unique, csv_out)
    print(f"{len(unique)} Ressourcen identifiziert -> {csv_out}")

    if unique:
        print("\n--- Vorschau (Top 5) ---")
        for row in unique[:5]:
            print(f"  [{row['Confidence']}] #{row['Channel']}: {row['Title'][:60]} ({row['Link']})")
    return {"messages": len(master), "resources": len(unique)}


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Merge crawled JSONs and extract math resources to CSV.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--base-dir", default="discord_exports", help="Crawl output directory.")
    p.add_argument("--merged-out", default=None, help="Path for the merged JSON.")
    p.add_argument("--csv-out", default=None, help="Path for the resources CSV.")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    merged_out = args.merged_out or os.path.join(args.base_dir, "MATH_MERGED.json")
    csv_out = args.csv_out or os.path.join(args.base_dir, "math_resources.csv")
    run(args.base_dir, merged_out, csv_out)


if __name__ == "__main__":
    main()
