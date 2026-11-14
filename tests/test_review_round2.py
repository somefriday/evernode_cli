"""Regressions for the defects review round 2 found (review_round2.md).

Each test names the finding it pins, and drives the code path the finding
broke rather than the text of a script.
"""

import argparse
import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from evernode import (
    arguments,
    creation,
    images,
    nodes,
    nominator,
    process,
    scripts,
    stake,
    storage,
    voting,
    wallets,
)
from support import make_node_configuration

OWNER = "-1:" + "e2" * 32
HOT = "-1:" + "c4" * 32
POOL = "-1:" + "ab" * 32


def profile(**changes):
    value = {
        "mode": "new", "custodians": 3, "required_signatures": 2,
        "wallet_address": HOT, "depool_address": None,
    }
    value.update(changes)
    return value


def nominator_config(**changes):
    value = make_node_configuration(
        "validator01",
        validator_type="nominator",
        wallet=profile(),
        settings={"NOMINATOR_OWNER": OWNER, "NOMINATOR_ADDR": POOL, "MSIG_FIX_STAKE": 1000},
        setup_stage="ready-for-elections",
    )
    value.update(changes)
    return value


class NominatorSafeTests(unittest.TestCase):
    def test_r2_02_recovery_asks_the_contract_from_the_safe(self):
        with patch("evernode.stake.scripts.recover_solo_stake", return_value={}) as recover:
            stake.recover(None, nominator_config())
        (_, _, subject), options = recover.call_args
        self.assertEqual(subject, POOL)
        self.assertEqual(
            options,
            {"signer": HOT, "destination": POOL, "value": 2000000000},
        )

    def test_r2_03_a_nominator_finishes_a_safe_transaction_with_stake_confirm(self):
        with patch("evernode.wallets.scripts.execute_validator_script") as run:
            _, result = wallets.stake_confirm(None, nominator_config(), transaction_id="7")
        self.assertEqual(run.call_args[0][2:5], ("Sign_Trans.sh", "validator01", "7"))
        self.assertIn("nominator info", result["next"])
        depool = make_node_configuration(
            "v1", wallet=profile(wallet_address="0:" + "a" * 64, depool_address="0:" + "b" * 64),
            setup_stage="ready-for-elections",
        )
        with self.assertRaisesRegex(process.OperationError, "use evernode depool"):
            wallets.stake_confirm(None, depool, transaction_id="7")

    def test_r2_04_funding_the_deployer_reads_the_wallets_own_threshold(self):
        """The handshake runs only when Required_Signs says so, and only the
        wallet can say it: run the program's opening against a stub wallet."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "managed_ops.shinc").write_text(
                'Get_Account_Custodians_Info() { echo "3 2"; }\n'
                'echoerr() { echo "$@" >&2; }\n'
            )
            done = subprocess.run(
                ["bash", "-c", scripts.READ_SIGNATURE_THRESHOLD + 'echo "$signer $Required_Signs"'],
                cwd=tmp, env=dict(os.environ, EVERNODE_WALLET=HOT),
                capture_output=True, text=True,
            )
        self.assertEqual(done.stdout.strip(), f"{HOT} 2", done.stderr)
        self.assertIn(scripts.READ_SIGNATURE_THRESHOLD, scripts.NOMINATOR_FUND_DEPLOYER)

    def test_r2_21_a_rotation_takes_only_a_whole_masterchain_address(self):
        for address in ("-1:abc", "0:" + "a" * 64, "-1:" + "g" * 64):
            with self.subTest(address=address), self.assertRaises(process.OperationError):
                nominator.rotate(None, nominator_config(), validator_address=address)


class CreationTests(unittest.TestCase):
    def setUp(self):
        self.mask = os.umask(0o077)
        self.addCleanup(os.umask, self.mask)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = storage.NodeStateStore(self.root / "state")
        guard = patch.object(storage.NodeStateStore, "validate_state_root")
        guard.start()
        self.addCleanup(guard.stop)

    def arguments(self, *extra):
        return arguments.build_argument_parser().parse_args(
            ["node", "create", "-n", "validator01", "--image", "local/ever-node:abc",
             "--ip", "203.0.113.5", "--memory", "8G", "--yes", *extra]
        )

    def test_r2_05_a_directory_that_was_already_there_is_not_removed(self):
        keys = self.root / "state" / "nodes" / "validator01" / "keys"
        keys.mkdir(parents=True)
        (keys / "validator01_1.keys.json").write_text("{}")
        record = {"image": "local/ever-node:abc", "image_id": "sha256:" + "a" * 64}
        done = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(storage.NodeStateStore, "list_node_names", return_value=[]),
            patch("evernode.docker.inspect_container", return_value=None),
            patch("evernode.docker.is_host_port_available", return_value=True),
            patch("evernode.process.execute_command", return_value=done),
            patch("evernode.creation.images.find_image_record", return_value=record),
            patch("evernode.creation.images.inspect_recorded_image", return_value=record),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            with self.assertRaisesRegex(process.OperationError, "already exists"):
                creation.handle_node_creation(self.store, self.arguments())
        self.assertTrue((keys / "validator01_1.keys.json").exists())
        self.assertNotIn("Removed failed setup", errors.getvalue())

    def test_r2_13_an_existing_address_needs_import_wallet(self):
        with (
            patch("evernode.docker.select_container_names", return_value=("a", "b")),
            self.assertRaisesRegex(process.OperationError, "--import-wallet"),
        ):
            creation.handle_node_creation(
                self.store, self.arguments("--wallet-address", "0:" + "a" * 64)
            )

    def test_r2_06_wallet_setup_opens_when_a_resumed_node_first_starts(self):
        config = make_node_configuration(
            "validator01", phase="prepared", initialized=True, setup_stage="preparing",
            wallet=profile(wallet_address=None),
        )
        self.store.node_directory("validator01").mkdir(parents=True)
        with (
            patch("evernode.nodes.docker.inspect_managed_container", return_value=None),
            patch("evernode.nodes.docker.execute_node_compose"),
            patch("evernode.nodes.fetch_node_statistics", return_value={}),
            patch.object(self.store, "save_node_config"),
        ):
            nodes.start_node(self.store, config)
        self.assertEqual(config["setup_stage"], "node-started")
        self.assertEqual(wallets.next_setup_command(config), "evernode wallet create -n validator01")

    def test_r2_06_a_resumed_import_types_its_phrases_at_wallet_recover(self):
        config = make_node_configuration(
            "validator01", setup_stage="node-started",
            wallet=profile(mode="import", custodians=1, required_signatures=1,
                           wallet_address="0:" + "a" * 64, depool_address="0:" + "b" * 64),
        )
        (self.root / "state" / "nodes" / "validator01" / "keys").mkdir(parents=True)
        self.assertFalse(wallets.import_seeds_stored(self.store, config))
        phrase = "abandon " * 11 + "about"
        for typed in (phrase, "legal " * 11 + "winner"):
            with patch("evernode.wallets.read_import_seeds", return_value=[typed]):
                wallets.enter_import_seeds(self.store, config, replace=True)
            seed = wallets.seed_directory(self.store, config) / "validator01_seed_1.txt"
            self.assertEqual(seed.read_text().strip(), typed.strip())
        self.assertTrue(wallets.import_seeds_stored(self.store, config))


class SmallFixTests(unittest.TestCase):
    def test_r2_10_a_failure_log_never_holds_a_seed_phrase(self):
        phrase = "abandon ability able about above absent absorb abstract absurd abuse access accident"
        with tempfile.TemporaryDirectory() as tmp:
            result = argparse.Namespace(
                stdout=f"###-ERROR: Can't generate key pair for seed phrase {phrase}\n", stderr=""
            )
            text = scripts._record_script_failure(Path(tmp), "Msig-prep.sh", result).read_text()
        self.assertNotIn("abandon", text)
        self.assertIn("seed phrase withheld", text)

    def test_r2_16_an_image_kept_for_rollback_is_still_in_use(self):
        record = {"image": "local/ever-node:old", "image_id": "sha256:" + "b" * 64, "key": "k"}
        node = make_node_configuration(
            "v1", image_rollback={"image": record["image"], "image_id": record["image_id"]}
        )
        store = argparse.Namespace(
            acquire_operation_lock=contextlib.nullcontext,
            list_node_names=lambda: ["v1"],
            load_node_config=lambda name: node,
        )
        with (
            patch("evernode.images.find_image_record", return_value=record),
            patch("evernode.images.process.execute_command",
                  return_value=subprocess.CompletedProcess([], 0, "", "")),
            self.assertRaisesRegex(process.OperationError, "referenced by: v1"),
        ):
            images.remove_managed_image(store, record["image"])

    def test_r2_22_the_safes_own_address_decides_whether_it_can_pay(self):
        imported = make_node_configuration("v1", wallet=profile(depool_address="0:" + "b" * 64))
        voting.require_masterchain_safe(imported)
        basechain = make_node_configuration(
            "v1", wallet=profile(wallet_address="0:" + "a" * 64, depool_address="0:" + "b" * 64)
        )
        with self.assertRaisesRegex(process.OperationError, "workchain 0"):
            voting.require_masterchain_safe(basechain)


if __name__ == "__main__":
    unittest.main()
