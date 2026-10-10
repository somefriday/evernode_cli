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
    def test_remote_refs_sort_versions_and_peel_annotated_tags(self):
        result = SimpleNamespace(
            stdout="\n".join(
                [
                    "a" * 40 + "\trefs/heads/master",
                    "b" * 40 + "\trefs/heads/feature",
                    "c" * 40 + "\trefs/tags/v0.60.9",
                    "d" * 40 + "\trefs/tags/v0.60.11",
                    "e" * 40 + "\trefs/tags/v0.60.11^{}",
                    "f" * 40 + "\trefs/tags/v0.61.0-rc1",
                    "1" * 40 + "\trefs/tags/v0.61.0",
                ]
            )
        )
        with patch(
            "evernode.images.process.execute_command", return_value=result
        ) as git:
            refs = images.list_remote_node_refs("https://example.test/node.git")
        self.assertEqual(git.call_args.args[0][:3], ["git", "ls-remote", "--heads"])
        self.assertEqual(refs["branches"]["master"], "a" * 40)
        self.assertEqual(
            refs["tags"],
            [
                ("v0.61.0", "1" * 40),
                ("v0.61.0-rc1", "f" * 40),
                ("v0.60.11", "e" * 40),
                ("v0.60.9", "c" * 40),
            ],
        )

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

    def test_local_images_are_sorted_by_node_version(self):
        older = record("local/node:older", "sha256:older")
        newer = record("local/node:newer", "sha256:newer")

        def run(command, **_kwargs):
            version = "0.60.9" if "sha256:older" in command else "0.60.11"
            return SimpleNamespace(stdout=f"EVER Node, version {version}\n", stderr="")

        with (
            patch(
                "evernode.images.list_managed_image_records",
                return_value=[older, newer],
            ),
            patch("evernode.images.inspect_recorded_image"),
            patch("evernode.images.process.execute_command", side_effect=run),
        ):
            self.assertEqual(
                images.list_available_managed_images(None),
                [(newer, "0.60.11"), (older, "0.60.9")],
            )

    def test_tag_must_still_point_to_recorded_id(self):
        item = record("local/node:old", "sha256:original")
        result = SimpleNamespace(stdout=json.dumps([{"Id": "sha256:replacement"}]))
        with patch("evernode.images.process.execute_command", return_value=result):
            with self.assertRaisesRegex(process.OperationError, "no longer points"):
                images.inspect_recorded_image(item)

    def test_menu_orders_master_tags_and_local_images(self):
        first = record("local/node:first", "sha256:first")
        second = record("local/node:second", "sha256:second")
        args = build_argument_parser().parse_args(
            ["node", "create", "--node-repo", "https://example.test/node.git"]
        )
        refs = {
            "branches": {"master": "a" * 40, "feature": "b" * 40},
            "tags": [("v0.60.11", "c" * 40), ("v0.60.9", "d" * 40)],
        }
        with (
            patch("evernode.creation.sys.stdin.isatty", return_value=True),
            patch("evernode.creation.sys.stdout.isatty", return_value=True),
            patch(
                "evernode.creation.images.list_available_managed_images",
                return_value=[(first, "0.60.10"), (second, "0.60.11")],
            ),
            patch(
                "evernode.creation.images.list_remote_node_refs", return_value=refs
            ) as remote,
            patch("evernode.creation.ui.select_option", return_value=(0, 0)) as menu,
        ):
            self.assertEqual(
                creation.choose_node_image(None, args), (None, "a" * 40, True)
            )
            self.assertEqual(remote.call_args.args[0], "https://example.test/node.git")
            labels = menu.call_args.args[1]
            self.assertTrue(labels[0].startswith("master (latest)"))
            self.assertIn("v0.60.11", labels[1])
            self.assertIn("v0.60.9", labels[2])
            self.assertIn("local 0.60.10", labels[3])
            menu.return_value = (0, 4)
            self.assertEqual(
                creation.choose_node_image(None, args), (second, None, True)
            )
            menu.return_value = (1, 0)
            self.assertEqual(
                creation.choose_node_image(None, args), (None, "b" * 40, True)
            )

    def test_noninteractive_creation_requires_an_explicit_image_choice(self):
        args = build_argument_parser().parse_args(["node", "create"])
        with patch("evernode.creation.sys.stdin.isatty", return_value=False):
            with self.assertRaisesRegex(process.OperationError, "--build-new"):
                creation.choose_node_image(None, args)

    def test_empty_catalog_still_offers_master(self):
        args = build_argument_parser().parse_args(["node", "create"])
        with (
            patch("evernode.creation.sys.stdin.isatty", return_value=True),
            patch("evernode.creation.sys.stdout.isatty", return_value=True),
            patch(
                "evernode.creation.images.list_available_managed_images",
                return_value=[],
            ),
            patch(
                "evernode.creation.images.list_remote_node_refs",
                return_value={"branches": {}, "tags": []},
            ),
            patch("evernode.creation.ui.select_option", return_value=(0, 0)) as menu,
        ):
            self.assertEqual(
                creation.choose_node_image(None, args), (None, "master", True)
            )
        self.assertEqual(menu.call_args.args[1], ["master (fetch latest source)"])

    def test_remote_lookup_failure_still_allows_local_image(self):
        item = record("local/node:ready", "sha256:ready")
        args = build_argument_parser().parse_args(["node", "create"])
        with (
            patch("evernode.creation.sys.stdin.isatty", return_value=True),
            patch("evernode.creation.sys.stdout.isatty", return_value=True),
            patch(
                "evernode.creation.images.list_available_managed_images",
                return_value=[(item, "0.60.11")],
            ),
            patch(
                "evernode.creation.images.list_remote_node_refs",
                side_effect=process.OperationError("offline"),
            ),
            patch("evernode.creation.ui.select_option", return_value=(0, 1)),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(creation.choose_node_image(None, args), (item, None, True))

    def test_explicit_image_skips_menu(self):
        args = build_argument_parser().parse_args(
            ["node", "create", "--image", "local/node:first"]
        )
        item = record("local/node:first", "sha256:first")
        with (
            patch("evernode.creation.images.find_image_record", return_value=item),
            patch("evernode.creation.ui.select_option") as menu,
        ):
            self.assertEqual(
                creation.choose_node_image(None, args), (item, None, False)
            )
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
            self.assertEqual(ui.select_option("Choose", ["one", "two"]), (0, 1))

    def test_tab_switches_to_other_branches(self):
        class Screen:
            def __init__(self):
                self.keys = iter((9, 10))

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
            self.assertEqual(
                ui.select_option(
                    "Versions", ["master"], alternate=("Branches", ["feature"])
                ),
                (1, 0),
            )

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

    def test_selected_source_commit_is_pinned_for_build(self):
        args = build_argument_parser().parse_args(
            [
                "node",
                "create",
                "-n",
                "validator",
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
        commit = "a" * 40
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
                "evernode.creation.choose_node_image", return_value=(None, commit, True)
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
        self.assertEqual(build.call_args.kwargs["node_ref"], commit)
        self.assertTrue(build.call_args.kwargs["rebuild"])
