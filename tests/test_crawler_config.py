import unittest

import discord_math_crawl as crawler


class ServerSelectorTests(unittest.TestCase):
    def test_default_server_name_is_used_without_an_id(self):
        names, ids = crawler.resolve_server_selectors()

        self.assertEqual(names, ["Mathematics"])
        self.assertEqual(ids, [])

    def test_server_id_disables_the_default_name(self):
        names, ids = crawler.resolve_server_selectors(
            server_ids=["123456789012345678"]
        )

        self.assertEqual(names, [])
        self.assertEqual(ids, ["123456789012345678"])

    def test_explicit_name_can_be_combined_with_an_id(self):
        names, ids = crawler.resolve_server_selectors(
            server_names=["Another Mathematics"],
            server_ids=["123456789012345678"],
        )

        self.assertEqual(names, ["Another Mathematics"])
        self.assertEqual(ids, ["123456789012345678"])

    def test_cli_accepts_repeatable_server_ids(self):
        args = crawler.build_arg_parser().parse_args(
            [
                "--server-id",
                "123456789012345678",
                "--server-id",
                "234567890123456789",
            ]
        )

        self.assertEqual(
            args.server_id,
            ["123456789012345678", "234567890123456789"],
        )


if __name__ == "__main__":
    unittest.main()
