"""The command reference in docs/ covers every command and every flag.

docs/rules.md: a command, its page and the index change together. This is the
check that they did.
"""

import argparse
from pathlib import Path
import unittest

from evernode import arguments

DOCS = Path(__file__).resolve().parents[1] / "docs"


def subcommands(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def long_flags(parser):
    return {
        option
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--") and option not in ("--help", "--version")
    }


def commands():
    """(command words, flags the parser accepts) for every documented command."""
    groups = subcommands(arguments.build_argument_parser())
    for group, parser in groups.items():
        actions = subcommands(parser)
        if group == "vote":
            for command in arguments.VOTE_COMMANDS:
                if command in arguments.HIDDEN_VOTE_COMMANDS:
                    continue
                own = (
                    arguments.build_vote_schedule_parser(command)
                    if command in ("join", "leave", "history")
                    else None
                )
                yield (group, command), long_flags(own) if own else set()
        elif actions:
            for action, sub in actions.items():
                yield (group, action), long_flags(sub)
        else:
            yield (group, group), long_flags(parser)


class CommandReferenceTests(unittest.TestCase):
    def test_every_command_has_a_page_with_every_flag_and_is_indexed(self):
        index = (DOCS / "README.md").read_text()
        for (group, action), flags in commands():
            with self.subTest(command=f"{group} {action}"):
                page = DOCS / group / f"{action}.md"
                self.assertTrue(page.is_file(), f"{page} is missing")
                text = page.read_text()
                for heading in ("## Example", "## What it does", "## Flags", "## Behaviour"):
                    self.assertIn(heading, text)
                for flag in flags:
                    self.assertIn(f"`{flag}`", text, f"{page.name} does not list {flag}")
                self.assertIn(f"({group}/{action}.md)", index, "not in docs/README.md")

    def test_no_page_documents_a_command_that_is_gone(self):
        known = {f"{group}/{action}.md" for (group, action), _ in commands()}
        pages = {
            str(path.relative_to(DOCS))
            for path in DOCS.glob("*/*.md")
            # docs/spec/ is the specification, not the command reference.
            if path.parent.name != "spec"
        }
        self.assertEqual(pages - known, set())


if __name__ == "__main__":
    unittest.main()
