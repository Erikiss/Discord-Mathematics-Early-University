"""Standard-library tests for the Discord -> research ingest contract."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest

import extract_discussions as extraction


HERE = os.path.dirname(__file__)
FIXTURE = os.path.join(HERE, "fixtures", "discord_messages.json")


def load_fixture():
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        return json.load(fh)


class IngestBundleTests(unittest.TestCase):
    def setUp(self):
        self.messages = load_fixture()
        self.discussions = extraction.extract_discussions(
            self.messages,
            min_score=8,
            guild_id="77",
        )
        self.bundle = extraction.build_ingest_bundle(
            self.messages,
            self.discussions,
            guild_id="77",
            context_before=1,
            context_after=2,
        )

    def test_legacy_discussion_shape_is_retained(self):
        first = next(row for row in self.discussions if row["id"] == "100")
        legacy_fields = {
            "id",
            "score",
            "server",
            "channel",
            "author",
            "timestamp",
            "date",
            "rendered_by_bot",
            "is_question",
            "topics",
            "primary_latex",
            "latex_snippets",
            "content",
            "engagement",
            "channel_id",
            "link",
            "context",
        }
        self.assertTrue(legacy_fields.issubset(first))
        self.assertTrue(first["rendered_by_bot"])
        self.assertEqual(first["author"], "Alice")

    def test_contract_has_atomic_public_and_private_partitions(self):
        self.assertEqual(
            self.bundle["schema_version"],
            "agentic-researcher/ingest-bundle/v1",
        )
        self.assertTrue(self.bundle["bundle_id"].startswith("discord_math_"))
        self.assertEqual(len(self.bundle["public"]["messages"]), len(self.messages))
        self.assertEqual(len(self.bundle["private"]["messages"]), len(self.messages))
        self.assertEqual(self.bundle["items"], self.bundle["public"]["blocks"])
        self.assertGreaterEqual(len(self.bundle["items"]), 2)

        source_key = extraction.message_key_from_id("100")
        public = next(
            row for row in self.bundle["public"]["messages"] if row["id"] == source_key
        )
        private = next(
            row for row in self.bundle["private"]["messages"] if row["id"] == source_key
        )
        self.assertNotIn("Alice", json.dumps(public))
        self.assertNotIn("<@222>", public["content"])
        self.assertIn("[user-mention]", public["content"])
        self.assertEqual(private["author"]["username"], "alice")
        self.assertIn("<@222>", private["raw_content"])
        self.assertEqual(private["discord"]["message_id"], "100")

    def test_texit_render_and_image_hashes_are_linked(self):
        source_key = extraction.message_key_from_id("100")
        render_key = extraction.message_key_from_id("101")
        public_by_id = {
            row["id"]: row for row in self.bundle["public"]["messages"]
        }
        self.assertEqual(
            public_by_id[source_key]["texit"]["render_message_keys"],
            [render_key],
        )
        self.assertEqual(
            public_by_id[render_key]["texit"]["renders_message_keys"],
            [source_key],
        )
        image = public_by_id[render_key]["attachments"][0]
        self.assertTrue(image["is_texit_render"])
        self.assertEqual(image["media_type"], "image")
        self.assertEqual(image["content_sha256"], "a" * 64)
        self.assertRegex(image["metadata_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("discordapp.com", json.dumps(image))
        private_render = next(
            row for row in self.bundle["private"]["messages"] if row["id"] == render_key
        )
        self.assertIn("discordapp.com", private_render["attachments"][0]["url"])

    def test_reply_time_and_topic_grouping_is_deterministic(self):
        calculus = [
            item for item in self.bundle["items"]
            if item["source"]["channel"] == "calculus"
        ]
        linear = [
            item for item in self.bundle["items"]
            if item["source"]["channel"] == "linear-algebra"
        ]
        self.assertEqual(len(calculus), 1)
        self.assertEqual(len(linear), 1)
        self.assertIn("reply", calculus[0]["grouping"]["reasons"])
        self.assertIn("time_topic", calculus[0]["grouping"]["reasons"])
        self.assertIn("derivative", calculus[0]["topics"])
        self.assertEqual(len(calculus[0]["seed_message_keys"]), 2)
        self.assertRegex(calculus[0]["fingerprint"], r"^[0-9a-f]{64}$")

        reversed_bundle = extraction.build_ingest_bundle(
            list(reversed(self.messages)),
            list(reversed(self.discussions)),
            guild_id="77",
            context_before=1,
            context_after=2,
        )
        self.assertEqual(self.bundle, reversed_bundle)

    def test_prompt_like_text_is_preserved_as_flagged_data(self):
        attack_key = extraction.message_key_from_id("104")
        public = next(
            row for row in self.bundle["public"]["messages"] if row["id"] == attack_key
        )
        private = next(
            row for row in self.bundle["private"]["messages"] if row["id"] == attack_key
        )
        self.assertTrue(public["safety"]["prompt_injection_suspected"])
        self.assertIn(
            "ignore-instructions",
            public["safety"]["prompt_injection_signals"],
        )
        self.assertTrue(
            public["safety"]["must_not_be_interpreted_as_instructions"]
        )
        self.assertEqual(public["content"], private["raw_content"])
        block = next(
            item for item in self.bundle["items"]
            if attack_key in item["message_keys"]
        )
        self.assertTrue(block["safety"]["contains_flagged_messages"])
        self.assertIn("[UNTRUSTED DISCORD DATA", block["text"])

    def test_content_change_changes_fingerprint_but_not_message_id(self):
        changed = copy.deepcopy(self.messages)
        changed[0]["content"] += " Additional assumption."
        changed_bundle = extraction.build_ingest_bundle(
            changed,
            guild_id="77",
            context_before=1,
            context_after=2,
        )
        old_key = extraction.message_key_from_id("100")
        old_record = next(
            row for row in self.bundle["public"]["messages"] if row["id"] == old_key
        )
        new_record = next(
            row for row in changed_bundle["public"]["messages"] if row["id"] == old_key
        )
        self.assertEqual(old_record["id"], new_record["id"])
        self.assertNotEqual(old_record["fingerprint"], new_record["fingerprint"])
        self.assertNotEqual(
            self.bundle["bundle_fingerprint"],
            changed_bundle["bundle_fingerprint"],
        )

    def test_expiring_cdn_query_does_not_change_public_fingerprints(self):
        refreshed = copy.deepcopy(self.messages)
        refreshed[1]["attachments"][0]["url"] = (
            "https://cdn.discordapp.com/attachments/10/501/chain-rule.png"
            "?secret=refreshed"
        )
        refreshed_bundle = extraction.build_ingest_bundle(
            refreshed,
            guild_id="77",
            context_before=1,
            context_after=2,
        )
        render_key = extraction.message_key_from_id("101")
        original_render = next(
            row for row in self.bundle["public"]["messages"] if row["id"] == render_key
        )
        refreshed_render = next(
            row for row in refreshed_bundle["public"]["messages"]
            if row["id"] == render_key
        )
        self.assertEqual(
            original_render["fingerprint"],
            refreshed_render["fingerprint"],
        )
        self.assertEqual(
            [item["fingerprint"] for item in self.bundle["items"]],
            [item["fingerprint"] for item in refreshed_bundle["items"]],
        )
        self.assertEqual(self.bundle["bundle_id"], refreshed_bundle["bundle_id"])
        self.assertNotEqual(
            self.bundle["bundle_fingerprint"],
            refreshed_bundle["bundle_fingerprint"],
        )

    def test_cli_run_writes_legacy_outputs_and_byte_stable_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = os.path.join(temp_dir, "input.json")
            discussions_path = os.path.join(temp_dir, "math_discussions.json")
            csv_path = os.path.join(temp_dir, "math_discussions.csv")
            bundle_path = os.path.join(temp_dir, "ingest_bundle.json")
            with open(input_path, "w", encoding="utf-8") as fh:
                json.dump(self.messages, fh)

            result = extraction.run(
                temp_dir,
                input_path=input_path,
                json_out=discussions_path,
                csv_out=csv_path,
                bundle_out=bundle_path,
                guild_id="77",
                context_before=1,
                context_after=2,
            )
            self.assertIsInstance(result, list)
            self.assertTrue(os.path.isfile(discussions_path))
            self.assertTrue(os.path.isfile(csv_path))
            self.assertTrue(os.path.isfile(bundle_path))
            with open(bundle_path, "rb") as fh:
                first_bytes = fh.read()

            extraction.run(
                temp_dir,
                input_path=input_path,
                json_out=discussions_path,
                csv_out=csv_path,
                bundle_out=bundle_path,
                guild_id="77",
                context_before=1,
                context_after=2,
            )
            with open(bundle_path, "rb") as fh:
                self.assertEqual(first_bytes, fh.read())


if __name__ == "__main__":
    unittest.main()
