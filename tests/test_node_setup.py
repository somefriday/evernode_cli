"""node setup: a node is created and synced with no role, and gets one afterwards."""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from evernode import configuration, creation, elections, process, role, stake, storage, wallets
from evernode.arguments import build_argument_parser
from evernode.cli import main

from support import make_node_configuration

KIT_ENV = Path(__file__).resolve().parents[1] / "evernode/vendor/ever-validator/scripts/env.sh"
SAFE = "0:" + "a" * 64
DEPOOL = "0:" + "b" * 64


class NodeSetupTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o077))
        guard = patch.object(storage.NodeStateStore, "validate_state_root")
        guard.start()
        self.addCleanup(guard.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = temporary.name
        self.store = storage.NodeStateStore(self.root)

    def created(self, name="validator01", **changes):
        """What node create leaves: a started node, its env.sh, no validator role."""
        directory = self.store.node_directory(name)
        (directory / "scripts").mkdir(parents=True)
        config = make_node_configuration(
            name, phase="console-ready", setup_stage="node-started", **changes
        )
        self.store.save_node_config(config)
        env = directory / "scripts" / "env.sh"
        shutil.copy(KIT_ENV, env)
        configuration.write_node_environment(env, configuration.build_node_environment(config))
        return config

    def setup(self, *flags, name="validator01"):
        out = io.StringIO()
        with patch("evernode.cli.require_root_privileges"), contextlib.redirect_stdout(out):
            code = main(["--state-dir", self.root, "node", "setup", "-n", name, "--yes", *flags])
        return code, out.getvalue()

    def saved(self, name="validator01"):
        return self.store.load_node_config(name)

    def env(self, name="validator01"):
        return (self.store.node_directory(name) / "scripts" / "env.sh").read_text()

    def test_a_created_node_has_no_role_and_points_to_node_setup(self):
        config = self.created()
        self.assertFalse(configuration.has_role(config))
        self.assertEqual(wallets.next_setup_command(config), "evernode node setup -n validator01")
        for read in (
            configuration.validator_type,
            wallets.node_wallet_profile,
            stake.require_recorded_stake,
        ):
            with self.subTest(read=read.__name__):
                with self.assertRaisesRegex(process.OperationError, "no validator role yet"):
                    read(config)
        # Status, election status and the node's environment need no role.
        self.assertIsNone(elections.election_schedule_status(self.store, config)["sequence"])
        self.assertEqual(configuration.build_node_environment(config)["STAKE_MODE"], "depool")

    def test_setup_commands_send_a_node_without_a_role_to_node_setup_first(self):
        """The node is healthy; it only lacks its role. That is said before the
        console is asked anything, which an unsynchronized node could refuse."""
        self.created()
        for argv in (
            ["wallet", "recover", "-n", "validator01", "--yes"],
            ["depool", "info", "-n", "validator01"],
            ["stake", "status", "-n", "validator01"],
            ["election", "start", "-n", "validator01"],
        ):
            with self.subTest(command=" ".join(argv[:2])):
                said = io.StringIO()
                with (
                    patch("evernode.cli.require_root_privileges"),
                    patch("evernode.scripts.require_node_console") as console,
                    contextlib.redirect_stdout(said),
                    contextlib.redirect_stderr(said),
                ):
                    code = main(["--state-dir", self.root, *argv])
                self.assertEqual(code, 1)
                self.assertIn("run evernode node setup -n validator01", said.getvalue())
                self.assertNotIn("create a new managed node", said.getvalue())
                console.assert_not_called()

    def test_setup_saves_the_role_and_renders_it_into_env_sh(self):
        self.created()
        code, out = self.setup("--validator-type", "solo", "--custodians", "3",
                               "--required-signatures", "2", "--cron-interval", "15")
        self.assertEqual(code, 0)
        saved = self.saved()
        self.assertEqual(saved["validator_type"], "solo")
        self.assertEqual(saved["wallet"]["mode"], "new")
        self.assertEqual(saved["settings"]["CRONTAB_INTERVAL"], 15)
        self.assertEqual(saved["setup_stage"], "node-started")
        self.assertIn("export STAKE_MODE=msig\n", self.env())
        self.assertIn('"next": "evernode wallet create -n validator01"', out)

    def test_an_existing_safe_is_checked_and_typed_at_wallet_recover_not_here(self):
        self.created()
        with patch("evernode.wallets.read_import_seeds") as typed:
            code, out = self.setup("--import-wallet", "--wallet-address", SAFE,
                                   "--depool-address", DEPOOL, "--custodians", "1",
                                   "--required-signatures", "1")
        self.assertEqual(code, 0)
        typed.assert_not_called()
        self.assertFalse(wallets.import_seeds_stored(self.store, self.saved()))
        self.assertIn('"next": "evernode wallet recover -n validator01"', out)

    def test_the_role_can_change_until_the_wallet_setup_begins(self):
        self.created()
        self.assertEqual(self.setup("--validator-type", "solo", "--custodians", "1",
                                    "--required-signatures", "1")[0], 0)
        self.assertEqual(self.setup("--validator-type", "depool", "--custodians", "3",
                                    "--required-signatures", "2")[0], 0)
        self.assertEqual(self.saved()["validator_type"], "depool")
        self.assertIn("export STAKE_MODE=depool\n", self.env())
        # Phrases or keys on disk tie the Safe to this role.
        seeds = wallets.seed_directory(self.store, self.saved())
        seeds.mkdir(parents=True)
        (seeds / "validator01_seed_1.txt").write_text("phrase")
        code, out = self.setup("--validator-type", "solo", "--custodians", "1",
                               "--required-signatures", "1")
        self.assertEqual(code, 1)
        self.assertEqual(self.saved()["validator_type"], "depool")
        # So does any later stage.
        seeds.joinpath("validator01_seed_1.txt").unlink()
        config = self.saved()
        config["setup_stage"] = "wallet-created"
        self.store.save_node_config(config)
        with self.assertRaisesRegex(process.OperationError, "wallet setup has begun"):
            role.require_role_can_be_set(self.store, config)

    def test_a_node_not_fully_created_or_removed_is_refused(self):
        for phase, hint in (("preparing", "node create --resume"), ("removed", "node start")):
            with self.subTest(phase=phase):
                config = make_node_configuration("validator01", phase=phase)
                with self.assertRaisesRegex(process.OperationError, hint):
                    role.require_role_can_be_set(self.store, config)

    def test_a_safe_another_node_uses_is_refused(self):
        self.created("validator01")
        self.created("validator02")
        flags = ("--import-wallet", "--wallet-address", SAFE, "--depool-address", DEPOOL,
                 "--custodians", "1", "--required-signatures", "1")
        self.assertEqual(self.setup(*flags, name="validator01")[0], 0)
        code, out = self.setup(*flags, name="validator02")
        self.assertEqual(code, 1)
        self.assertFalse(configuration.has_role(self.saved("validator02")))

    def test_a_dry_run_saves_nothing(self):
        self.created()
        before = self.env()
        code, out = self.setup("--validator-type", "solo", "--custodians", "1",
                               "--required-signatures", "1", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn('"plan": "set the validator role"', out)
        self.assertFalse(configuration.has_role(self.saved()))
        self.assertEqual(self.env(), before)

    def test_node_create_refuses_the_names_the_election_scripts_use(self):
        """keys/NAME.addr would be keys/depool.addr or keys/proxy0.addr; refused
        before any other question (no --memory here, so it must come first)."""
        for name in creation.RESERVED_NAMES:
            with self.subTest(name=name):
                args = build_argument_parser().parse_args(["node", "create", "-n", name])
                with self.assertRaisesRegex(process.OperationError, "election scripts"):
                    creation.handle_node_creation(self.store, args)

    def test_node_create_takes_no_role_flags(self):
        parser = build_argument_parser()
        for flag in (["--validator-type", "solo"], ["--import-wallet"],
                     ["--wallet-address", SAFE], ["--custodians", "3"]):
            with self.subTest(flag=flag[0]):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    parser.parse_args(["node", "create", "-n", "validator01", *flag])
                parser.parse_args(["node", "setup", "-n", "validator01", *flag])


if __name__ == "__main__":
    unittest.main()
