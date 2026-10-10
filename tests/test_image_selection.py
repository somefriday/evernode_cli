"""Image menu and managed-image availability checks."""

import contextlib
import curses
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from evernode import creation, images, process, ui
from evernode.arguments import build_argument_parser


def record(tag, image_id, commit="a" * 40):
    return {
        "image": tag,
        "image_id": image_id,
        "inputs": {"node_commit": commit},
    }


class ImageSelectionTests(unittest.TestCase):
    def test_catalog_reports_docker_daemon_failure(self):
        with patch(
            "evernode.images.process.execute_command",
            side_effect=process.OperationError("Cannot connect to Docker"),
        ):
            with self.assertRaisesRegex(process.OperationError, "Cannot connect"):
                images.list_available_managed_images(None)

    def test_catalog_skips_stale_images_and_keeps_unknown_versions(self):
        valid = record("local/node:valid", "sha256:valid")
        unknown = record("local/node:unknown", "sha256:unknown")
        stale = record("local/node:stale", "sha256:stale")

        def inspect(item):
            if item is stale:
                raise process.OperationError("missing tag")
            return item

        def run(command, **_kwargs):
            if "sha256:unknown" in command:
                raise process.OperationError("cannot start image")
            return SimpleNamespace(stdout="EVER Node, version 0.60.11\n", stderr="")

        with (
            patch(
                "evernode.images.list_managed_image_records",
                return_value=[valid, unknown, stale],
            ),
            patch("evernode.images.inspect_recorded_image", side_effect=inspect),
            patch("evernode.images.process.execute_command", side_effect=run),
        ):
            self.assertEqual(
                images.list_available_managed_images(None),
                [(valid, "0.60.11"), (unknown, "unknown")],
            )

    def test_tag_must_still_point_to_recorded_id(self):
        item = record("local/node:old", "sha256:original")
        result = SimpleNamespace(stdout=json.dumps([{"Id": "sha256:replacement"}]))
        with patch("evernode.images.process.execute_command", return_value=result):
            with self.assertRaisesRegex(process.OperationError, "no longer points"):
                images.inspect_recorded_image(item)

    def test_menu_selects_existing_image_or_fresh_build(self):
        first = record("local/node:first", "sha256:first")
        second = record("local/node:second", "sha256:second")
        args = build_argument_parser().parse_args(["node", "create"])
        with (
            patch("evernode.creation.sys.stdin.isatty", return_value=True),
            patch("evernode.creation.sys.stdout.isatty", return_value=True),
            patch(
                "evernode.creation.images.list_available_managed_images",
                return_value=[(first, "0.60.10"), (second, "0.60.11")],
            ),
            patch("evernode.creation.ui.select_option", return_value=1) as menu,
        ):
            self.assertEqual(creation.choose_node_image(None, args), (second, True))
            self.assertIn("0.60.11", menu.call_args.args[1][1])
            menu.return_value = 2
            self.assertEqual(creation.choose_node_image(None, args), (None, True))

    def test_noninteractive_creation_requires_an_explicit_image_choice(self):
        args = build_argument_parser().parse_args(["node", "create"])
        with patch("evernode.creation.sys.stdin.isatty", return_value=False):
            with self.assertRaisesRegex(process.OperationError, "--build-new"):
                creation.choose_node_image(None, args)

    def test_empty_catalog_still_offers_a_build(self):
        args = build_argument_parser().parse_args(["node", "create"])
        with (
            patch("evernode.creation.sys.stdin.isatty", return_value=True),
            patch("evernode.creation.sys.stdout.isatty", return_value=True),
            patch(
                "evernode.creation.images.list_available_managed_images",
                return_value=[],
            ),
            patch("evernode.creation.ui.select_option", return_value=0) as menu,
        ):
            self.assertEqual(creation.choose_node_image(None, args), (None, True))
        self.assertEqual(menu.call_args.args[1], ["Build a new image from source"])

    def test_explicit_image_skips_menu(self):
        args = build_argument_parser().parse_args(
            ["node", "create", "--image", "local/node:first"]
        )
        item = record("local/node:first", "sha256:first")
        with (
            patch("evernode.creation.images.find_image_record", return_value=item),
            patch("evernode.creation.ui.select_option") as menu,
        ):
            self.assertEqual(creation.choose_node_image(None, args), (item, False))
        menu.assert_not_called()

    def test_arrow_keys_select_image(self):
        class Screen:
            def __init__(self):
                self.keys = iter((curses.KEY_DOWN, 10))

            def keypad(self, _value):
                pass

            def erase(self):
                pass

            def getmaxyx(self):
                return 12, 80

            def addnstr(self, *_args):
                pass

            def refresh(self):
                pass

            def getch(self):
                return next(self.keys)

        with (
            patch("evernode.ui.sys.stdin.isatty", return_value=True),
            patch("evernode.ui.sys.stdout.isatty", return_value=True),
            patch("curses.curs_set"),
            patch("curses.wrapper", side_effect=lambda choose: choose(Screen())),
        ):
            self.assertEqual(ui.select_option("Choose", ["one", "two"]), 1)

    def test_escape_cancels_menu(self):
        class Screen:
            def keypad(self, _value):
                pass

            def erase(self):
                pass

            def getmaxyx(self):
                return 12, 80

            def addnstr(self, *_args):
                pass

            def refresh(self):
                pass

            def getch(self):
                return 27

        with (
            patch("evernode.ui.sys.stdin.isatty", return_value=True),
            patch("evernode.ui.sys.stdout.isatty", return_value=True),
            patch("curses.curs_set"),
            patch("curses.wrapper", side_effect=lambda choose: choose(Screen())),
        ):
            with self.assertRaisesRegex(process.OperationError, "Cancelled"):
                ui.select_option("Choose", ["one"])

    def test_build_new_forces_a_new_image(self):
        args = build_argument_parser().parse_args(
            [
                "node",
                "create",
                "-n",
                "validator",
                "--build-new",
                "--yes",
                "--ip",
                "203.0.113.5",
                "--memory",
                "40G",
                "--custodians",
                "3",
                "--required-signatures",
                "2",
            ]
        )
        with (
            patch(
                "evernode.creation.docker.select_container_names",
                return_value=("ever-node-01", "statsd-01"),
            ),
            patch(
                "evernode.creation.docker.select_available_node_port",
                side_effect=(58888, 9102),
            ),
            patch(
                "evernode.creation.images.build_managed_image",
                side_effect=process.OperationError("build stopped"),
            ) as build,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(process.OperationError, "build stopped"):
                creation.handle_node_creation(
                    SimpleNamespace(list_node_names=lambda: []), args
                )
        self.assertTrue(build.call_args.kwargs["rebuild"])
