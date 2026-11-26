"""The denominator validator type changes nothing for the other types.

The golden file was captured from the code before the denominator type existed
(`python3 tests/test_denominator_isolation.py --capture`). These tests render the
same things again and require them byte for byte: env.sh, the election cron
programs, the node setup plans, the next-step commands, every command's help and
the kit's own files. The two help pages that list every group or every type,
the top-level page and node setup, gain the new name after "nominator" in those
lists and lines of its own; apart from their usage block and the type flag's own
entry, every line they had stays, in order.
"""

import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evernode import configuration, elections, storage, wallets  # noqa: E402
from evernode.arguments import build_argument_parser  # noqa: E402
from evernode.cli import main  # noqa: E402
from support import make_node_configuration  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "isolation" / "golden.json"
KIT = ROOT / "evernode" / "vendor" / "ever-validator"
SINGLE_NOMINATOR = ROOT / "evernode" / "vendor" / "single-nominator"
KIT_ENV = KIT / "scripts" / "env.sh"
# Files the denominator type adds to the kit; everything else in it must be untouched.
NEW_KIT_FILES = {"scripts/denominator_cycle.sh"}
# The help pages that name every type or every group, and so must gain the new one.
GROWING_HELP = {"evernode", "evernode node setup"}

SAFE_MC = "-1:" + "a" * 64
SAFE_BC = "0:" + "a" * 64
OWNER = "0:" + "c" * 64
NOMINATOR = "-1:" + "d" * 64

# One node of each existing kind, as node setup and the wallet stages leave it.
ROLES = {
    "no-role": {},
    "legacy-depool": {"wallet": {"mode": "new", "custodians": 3, "required_signatures": 2,
                                 "wallet_address": SAFE_BC, "depool_address": None}},
    "depool": {"validator_type": "depool",
               "wallet": {"mode": "new", "custodians": 3, "required_signatures": 2,
                          "wallet_address": SAFE_BC, "depool_address": "0:" + "b" * 64},
               "settings": {"NODE_WC": 0, "CRONTAB_INTERVAL": 10, "ValidatorAssuranceT": 50000}},
    "solo": {"validator_type": "solo",
             "wallet": {"mode": "new", "custodians": 1, "required_signatures": 1,
                        "wallet_address": SAFE_MC, "depool_address": None},
             "settings": {"NODE_WC": 0, "MSIG_FIX_STAKE": 12000}},
    "nominator": {"validator_type": "nominator",
                  "wallet": {"mode": "new", "custodians": 1, "required_signatures": 1,
                             "wallet_address": SAFE_MC, "depool_address": None},
                  "settings": {"NODE_WC": 0, "NOMINATOR_OWNER": OWNER, "NOMINATOR_ADDR": NOMINATOR,
                               "NOMINATOR_TOOLS_IMAGE": "local/evernode-nominator-tools:0123456789abcdef",
                               "NOMINATOR_RESERVE": 10000000000, "MSIG_FIX_STAKE": 15000}},
}
SETUP_FLAGS = {
    "depool": ["--validator-type", "depool", "--validator-assurance", "50000", "--min-stake", "10",
               "--reward-fraction", "65", "--balance-threshold", "20", "--depool-type", "EverX",
               "--workchain", "0", "--cron-interval", "10", "--custodians", "3", "--required-signatures", "2"],
    "solo": ["--validator-type", "solo", "--workchain", "0", "--cron-interval", "10",
             "--custodians", "1", "--required-signatures", "1"],
    "solo-import": ["--validator-type", "solo", "--import-wallet", "--wallet-address", "-1:" + "e" * 64,
                    "--workchain", "0", "--cron-interval", "10", "--custodians", "1", "--required-signatures", "1"],
    "nominator": ["--validator-type", "nominator", "--owner-address", OWNER, "--workchain", "0",
                  "--cron-interval", "10", "--custodians", "1", "--required-signatures", "1"],
}
STAGES = ("node-started", "wallet-created", "wallet-deployed", "wallet-recovered", "wallet-verified",
          "imported", "depool-prepared", "depool-deployed", "nominator-prepared", "nominator-deployed",
          "nominator-verified", "ready-for-elections")


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files(root, skip=()):
    return {
        str(path.relative_to(root)): _digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and "node_modules" not in path.parts and "__pycache__" not in path.parts
        and str(path.relative_to(root)) not in skip
    }


def _comparable(text):
    """A help page as the new type may not change it: without the usage block,
    which argparse builds from every flag and choice, without the type flag's
    entry, whose description names the types, and with the new name taken out
    of the choice lists."""
    lines = text.replace(",nominator,denominator", ",nominator").splitlines()
    lines = lines[lines.index("") + 1:] if "" in lines else lines
    kept, skipping = [], False
    for line in lines:
        if line.startswith("  -"):
            skipping = line.strip().startswith("--validator-type")
        if not skipping:
            kept.append(line)
    return kept


def _in_order(old, new):
    """Whether every line of OLD appears in NEW, in the same order."""
    remaining = iter(new)
    return all(any(line == candidate for candidate in remaining) for line in old)


def _parsers():
    """Every command's parser, by its words."""
    parsers = {}

    def walk(parser, words):
        parsers[" ".join(words)] = parser
        for action in parser._actions:
            if action.__class__.__name__ == "_SubParsersAction":
                for name, sub in action.choices.items():
                    walk(sub, words + [name])

    walk(build_argument_parser(), ["evernode"])
    return parsers


def _help_pages():
    return {page: parser.format_help() for page, parser in _parsers().items()}


def _long_flags(help_text):
    """The long flags a help page lists as options."""
    return set(re.findall(r"^\s+(?:-\w(?: \S+)?, )?(--[a-z][a-z0-9-]*)", help_text, re.M))


@contextlib.contextmanager
def _store():
    previous = os.umask(0o077)
    guard = patch.object(storage.NodeStateStore, "validate_state_root")
    guard.start()
    temporary = tempfile.mkdtemp()
    try:
        yield storage.NodeStateStore(temporary), temporary
    finally:
        guard.stop()
        shutil.rmtree(temporary, ignore_errors=True)
        os.umask(previous)


def _node(store, name, role):
    directory = store.node_directory(name)
    for part in ("scripts", "logs/validator", "elections"):
        (directory / part).mkdir(parents=True, exist_ok=True)
    config = make_node_configuration(name, phase="console-ready", setup_stage="node-started", **role)
    store.save_node_config(config)
    env = directory / "scripts" / "env.sh"
    shutil.copy(KIT_ENV, env)
    configuration.write_node_environment(env, configuration.build_node_environment(config))
    return config


def capture():
    os.environ["COLUMNS"] = "120"
    golden = {"env": {}, "sequence": {}, "setup": {}, "next": {}, "workchain": {}}
    with _store() as (store, root):
        for case, role in ROLES.items():
            config = _node(store, "v-" + case, role)
            golden["env"][case] = (store.node_directory(config["name"]) / "scripts" / "env.sh").read_text()
            if case == "no-role":
                continue
            golden["workchain"][case] = wallets.wallet_workchain(config)
            runner = elections.write_election_programs(store, config)
            sequence = elections.election_sequence_path(store, config)
            golden["sequence"][case] = {
                "file": sequence.name,
                "program": sequence.read_text().replace(root, "<ROOT>"),
                "runner": runner.read_text().replace(root, "<ROOT>"),
            }
            for mode in ("new", "import"):
                for stage in STAGES:
                    item = dict(config, setup_stage=stage, wallet=dict(config["wallet"], mode=mode))
                    golden["next"][f"{case} {mode} {stage}"] = wallets.next_setup_command(item)
        for case, flags in SETUP_FLAGS.items():
            _node(store, "s-" + case, {})
            out = io.StringIO()
            with patch("evernode.cli.require_root_privileges"), contextlib.redirect_stdout(out):
                code = main(["--state-dir", root, "node", "setup", "-n", "s-" + case, "--dry-run", "--yes", *flags])
            golden["setup"][case] = {"code": code, "output": out.getvalue()}
    golden["help"] = _help_pages()
    golden["kit"] = _files(KIT, skip=NEW_KIT_FILES)
    golden["single_nominator"] = _files(SINGLE_NOMINATOR)
    return golden


class IsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN.read_text())
        cls.now = capture()

    def test_env_sh_is_byte_identical_for_every_existing_type(self):
        for case, text in self.golden["env"].items():
            with self.subTest(case=case):
                self.assertEqual(self.now["env"][case], text)

    def test_the_cron_programs_are_byte_identical(self):
        for case, item in self.golden["sequence"].items():
            with self.subTest(case=case):
                self.assertEqual(self.now["sequence"][case], item)

    def test_node_setup_plans_are_identical(self):
        for case, item in self.golden["setup"].items():
            with self.subTest(case=case):
                self.assertEqual(self.now["setup"][case], item)

    def test_the_next_step_commands_are_identical(self):
        self.assertEqual(self.now["next"], self.golden["next"])
        self.assertEqual(self.now["workchain"], self.golden["workchain"])

    def test_every_help_page_is_identical_and_the_entry_points_keep_every_line(self):
        for page, text in self.golden["help"].items():
            with self.subTest(page=page):
                if page in GROWING_HELP:
                    self.assertTrue(
                        _in_order(_comparable(text), _comparable(self.now["help"][page])),
                        f"{page} lost or changed a line",
                    )
                else:
                    self.assertEqual(self.now["help"][page], text)

    def test_the_new_name_joins_the_lists_after_nominator_and_nothing_moves(self):
        parser = build_argument_parser()
        groups = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
        names = list(groups.choices)
        self.assertEqual(names[names.index("nominator") + 1], "denominator")
        self.assertEqual([n for n in names if n != "denominator"],
                         ["host", "node", "wallet", "depool", "stake", "nominator", "election", "image", "doctor", "vote"])
        setup = next(a for a in groups.choices["node"]._actions if a.__class__.__name__ == "_SubParsersAction")
        types = next(a for a in setup.choices["setup"]._actions if "--validator-type" in a.option_strings)
        self.assertEqual(types.choices, ["depool", "solo", "nominator", "denominator"])

    def test_every_abbreviation_of_an_older_flag_still_names_it(self):
        """argparse takes a unique prefix of a long flag for the flag, so a new flag
        that shares such a prefix would turn a command line that worked into an error."""
        parsers = _parsers()
        for page, text in self.golden["help"].items():
            parser = parsers[page]
            if not parser.allow_abbrev:
                continue
            # Flags hidden from the help (cron's own) are not in the recorded pages;
            # the denominator type adds none, so the ones there now were there before.
            hidden = {option for action in parser._actions if action.help is argparse.SUPPRESS
                      for option in action.option_strings if option.startswith("--")}
            old = _long_flags(text) | hidden
            new = {option for action in parser._actions for option in action.option_strings if option.startswith("--")}
            for flag in old:
                for end in range(3, len(flag)):
                    prefix = flag[:end]
                    if [f for f in old if f.startswith(prefix)] == [flag]:
                        with self.subTest(page=page, prefix=prefix):
                            self.assertEqual([f for f in new if f.startswith(prefix)], [flag])

    def test_the_kits_own_files_are_unchanged(self):
        self.assertEqual(self.now["kit"], self.golden["kit"])

    def test_the_single_nominator_sources_are_unchanged(self):
        self.assertEqual(self.now["single_nominator"], self.golden["single_nominator"])


if __name__ == "__main__":
    if "--capture" in sys.argv:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(capture(), indent=1, sort_keys=True) + "\n")
        print(f"captured {GOLDEN}")
    else:
        unittest.main()
