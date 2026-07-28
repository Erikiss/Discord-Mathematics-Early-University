#!/usr/bin/env python3
"""Smoke-test the Discord adapter against The Agentic Researcher contract.

With ``--bundle`` the script validates a real crawl artifact.  Without it, the
checked-in synthetic Discord fixture is converted first, so CI also exercises
the adapter itself without accessing Discord.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "discord_messages.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exercise the Discord -> Agentic Researcher hand-off."
    )
    parser.add_argument(
        "--agentic-researcher",
        required=True,
        type=Path,
        help="Path to a checkout of The-Agentic-Researcher.",
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        help="Existing ingest_bundle.json; otherwise build one from the test fixture.",
    )
    parser.add_argument(
        "--media-root",
        type=Path,
        help="Optional materialized image directory to test against the media contract.",
    )
    return parser.parse_args()


def load_bundle(path: Path | None) -> dict:
    if path is not None:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    sys.path.insert(0, str(REPO_ROOT))
    import extract_discussions

    with FIXTURE.open("r", encoding="utf-8") as handle:
        messages = json.load(handle)
    discussions = extract_discussions.extract_discussions(
        messages,
        min_score=8,
        guild_id="77",
    )
    return extract_discussions.build_ingest_bundle(
        messages,
        discussions,
        guild_id="77",
        context_before=1,
        context_after=2,
    )


def curator_response(provider: str, bundle_id: str, source_id: str) -> dict:
    return {
        "schema_version": "agentic-researcher/curation-response/v1",
        "provider": provider,
        "bundle_id": bundle_id,
        "topics": [
            {
                "title": "Chain rule discussion",
                "summary": "A discussion of the chain rule and its prerequisites.",
                "source_item_ids": [source_id],
                "area": "Calculus",
                "subarea": "Differential calculus",
                "formulas": [r"(f\circ g)'=(f'\circ g)g'"],
                "questions": ["How can the chain rule be derived and verified?"],
                "prerequisites": ["functions", "derivatives"],
                "uncertainties": [],
                "confidence": 0.9,
            }
        ],
    }


def main() -> int:
    args = parse_args()
    framework = args.agentic_researcher.resolve()
    instructions = framework / "INSTRUCTIONS.md"
    if not instructions.is_file():
        raise SystemExit(f"Agentic Researcher checkout is invalid: {framework}")

    sys.path.insert(0, str(framework))
    from agentic_researcher.pipeline import (
        expand_topics,
        merge_curation_responses,
        run_batch,
        stage_media_files,
        validate_ingest_bundle,
    )

    bundle = validate_ingest_bundle(load_bundle(args.bundle))
    source_id = bundle["items"][0]["id"]
    responses = [
        curator_response(provider, bundle["bundle_id"], source_id)
        for provider in ("claude", "codex", "antigravity")
    ]
    curated = merge_curation_responses(bundle, responses, threshold=2)
    accepted = [
        topic for topic in curated["topics"] if topic["status"] == "accepted"
    ]
    if not accepted:
        raise SystemExit("2-of-3 contract smoke test produced no accepted topic")

    queue = expand_topics(curated)
    if not queue["tasks"]:
        raise SystemExit("topic expansion produced an empty research queue")

    with tempfile.TemporaryDirectory(prefix="agentic-math-contract-") as temp:
        temp_dir = Path(temp)
        staged_media = 0
        if args.media_root is not None:
            staged = stage_media_files(
                bundle,
                args.media_root,
                temp_dir / "provider-workspace" / "media",
            )
            with (staged / "manifest.json").open("r", encoding="utf-8") as handle:
                staged_media = len(json.load(handle)["files"])
        state = run_batch(
            queue,
            temp_dir / "work",
            temp_dir / "batch-state.json",
            instruction_template=instructions,
            provider="opencode",
            dry_run=True,
        )
        if state["summary"].get("skipped") != len(queue["tasks"]):
            raise SystemExit("dry-run batch did not initialize every research task")

    print(
        json.dumps(
            {
                "status": "ok",
                "schema_version": bundle["schema_version"],
                "bundle_id": bundle["bundle_id"],
                "items": len(bundle["items"]),
                "accepted_topics": len(accepted),
                "research_tasks": len(queue["tasks"]),
                "staged_media": staged_media,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
