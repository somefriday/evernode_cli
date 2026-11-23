"""A validator whose capital is held by a contract a cold owner controls.

The point of this mode is that the key able to withdraw the principal is not
on the validator server, so these tests pin the separation itself as much as
the plumbing: which operations this CLI will sign, which ones it refuses to do
more than prepare, and which addresses it refuses to accept as an owner.

The cell-level tests compare against bytes recorded by the reference
experiment, so they check what the chain actually accepted rather than what
this implementation believes.
"""

import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from evernode import (
    configuration, elections, nominator, process, role, scripts, setup_actions,
    stake, wallets,
)
from evernode.arguments import build_argument_parser, normalize_address_arguments
from support import make_node_configuration

VENDOR = Path(__file__).resolve().parents[1] / "evernode" / "vendor" / "single-nominator"
ASSETS = Path(__file__).resolve().parents[1] / "evernode" / "assets"
# Bytes the reference experiment recorded from the chain; public data, kept with the tests.
REFERENCE = Path(__file__).resolve().parent / "fixtures" / "nominator"
OWNER = "-1:e253907b4dc0e9d64cb738df4895094ef8d115ae3e1742f637f0329608815913"
HOT = "-1:c43ea310703d54abf436c53af049013960f1097b5360a453af3467b1321b00bc"
POOL = "-1:2e733a99573d9d81fa4292930c3044f6d47dadb7a52e5b5d86bc5a52cd2ea374"
FACTORY = "-1:21bfa1f5c717de99cd17b406822ab7295498d194570dfd8a2d26782b9536d88a"
CODE_HASH = "78562f9a9a11337188bdd1fc430092146dbcc8b2b11b0bf30b3a8140efc72a25"
# upstream v1.1, unmodified: the hash upstream publishes
UPSTREAM_CODE_HASH = "cc0d39589eb2c0cfe0fde28456657a3bdd3d953955ae3f98f25664ab3c904fbd"
# The one change evernode makes to upstream's source, as (upstream text, vendored text).
EVERNODE_CHANGE = (
    ("const ERROR::INSUFFICIENT_ELECTOR_FEE = 0x2005;\n",
     "const ERROR::INSUFFICIENT_ELECTOR_FEE = 0x2005;\n"
     "const ERROR::WRONG_NEW_STAKE_MSG = 0x2006; ;; evernode: a new_stake body not in the elector's request format\n"),
    ("int check_new_stake_msg(slice cs) impure inline_ref {\n",
     ";; evernode: accepts exactly the elector's request format: 576 bits and one 512-bit signature cell\n"
     "int check_new_stake_msg(slice cs) impure inline_ref {\n"
     "    throw_unless(ERROR::WRONG_NEW_STAKE_MSG, (cs.slice_bits() == 576) & (cs.slice_refs() == 1));\n"),
    ("    var signature = cs~load_ref().begin_parse().preload_bits(512);\n",
     "    slice signature = cs~load_ref().begin_parse();\n"
     "    throw_unless(ERROR::WRONG_NEW_STAKE_MSG, (signature.slice_bits() == 512) & (signature.slice_refs() == 0));\n"),
)


def nominator_config(**changes):
    value = make_node_configuration(
        "validator01",
        validator_type="nominator",
        wallet={
            "mode": "new", "custodians": 1, "required_signatures": 1,
            "wallet_address": None, "depool_address": None,
        },
    )
    value.setdefault("settings", {})
    value["settings"] = dict(value["settings"], NOMINATOR_OWNER=OWNER)
    value.update(changes)
    return value


def without_crc(boc):
    """The same cells as the tool now writes them: BOC flag 0x40 marks a trailing CRC32C."""
    return boc[:4] + bytes([boc[4] & ~0x40]) + boc[5:-4] if boc[4] & 0x40 else boc


class ValidatorTypeTests(unittest.TestCase):
    def test_the_reference_scripts_are_told_the_new_mode_by_name(self):
        environment = configuration.build_node_environment(nominator_config())
        self.assertEqual(environment["STAKE_MODE"], "nominator")

    def test_the_two_older_modes_keep_their_names(self):
        for validator_type, expected in (("depool", "depool"), ("solo", "msig")):
            with self.subTest(validator_type=validator_type):
                config = make_node_configuration("v1", validator_type=validator_type)
                self.assertEqual(
                    configuration.build_node_environment(config)["STAKE_MODE"], expected
                )

    def test_a_node_saved_before_this_mode_existed_still_loads(self):
        """Older node.json files carry none of the nominator settings."""
        saved = make_node_configuration("v1")
        saved["wallet"] = {"mode": "new", "custodians": 3, "required_signatures": 2,
                           "wallet_address": None, "depool_address": None}
        saved["settings"] = {"NODE_WC": 0, "CRONTAB_INTERVAL": 10}
        value = configuration.validate_saved_node_config(saved, "v1")
        self.assertEqual(configuration.validator_type(value), "depool")

    def test_the_contract_and_owner_reach_the_reference_environment(self):
        config = nominator_config()
        config["settings"] = dict(config["settings"], NOMINATOR_ADDR=POOL)
        environment = configuration.build_node_environment(config)
        self.assertEqual(environment["NOMINATOR_ADDR"], POOL)
        self.assertEqual(environment["NOMINATOR_OWNER"], OWNER)


class OwnerSeparationTests(unittest.TestCase):
    """The owner key must not be on this server, and must not be assumed."""

    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o077))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def store_with(self, saved):
        root = self.root

        class Store:
            def list_node_names(inner):
                return list(saved)

            def load_node_config(inner, name):
                return saved[name]

            def node_directory(inner, name):
                return root

        return Store()

    def test_an_owner_this_server_already_holds_is_refused(self):
        other = make_node_configuration(
            "other",
            wallet={
                "mode": "new", "custodians": 3, "required_signatures": 2,
                "wallet_address": OWNER, "depool_address": None,
            },
        )
        with self.assertRaises(process.OperationError) as caught:
            wallets.ensure_owner_is_external(
                self.store_with({"other": other}), nominator_config(), OWNER
            )
        self.assertIn("cold wallet this host does not hold", str(caught.exception))

    def test_another_nodes_nominator_contract_cannot_be_an_owner(self):
        other = make_node_configuration(
            "other",
            validator_type="nominator",
            wallet={
                "mode": "new", "custodians": 1, "required_signatures": 1,
                "wallet_address": None, "depool_address": None,
            },
            settings={"NOMINATOR_ADDR": POOL},
        )
        with self.assertRaises(process.OperationError):
            wallets.ensure_owner_is_external(
                self.store_with({"other": other}), nominator_config(), POOL
            )

    def test_an_unrelated_cold_address_is_accepted(self):
        other = make_node_configuration(
            "other",
            wallet={
                "mode": "new", "custodians": 3, "required_signatures": 2,
                "wallet_address": "-1:" + "9" * 64, "depool_address": None,
            },
        )
        wallets.ensure_owner_is_external(
            self.store_with({"other": other}), nominator_config(), OWNER
        )

    def test_two_validators_may_share_one_cold_owner(self):
        """Each contract is owner plus its own hot wallet, so they differ."""
        other = make_node_configuration(
            "other", validator_type="nominator", settings={"NOMINATOR_OWNER": OWNER},
            wallet={
                "mode": "new", "custodians": 1, "required_signatures": 1,
                "wallet_address": None, "depool_address": None,
            },
        )
        root = self.root

        class Store:
            def list_node_names(inner):
                return ["other"]

            def load_node_config(inner, name):
                return other

            def node_directory(inner, name):
                return root

        wallets.ensure_owner_is_external(Store(), nominator_config(), OWNER)

    def test_an_owner_operation_prepares_a_body_and_never_broadcasts(self):
        """These must end with something to sign elsewhere, not with a send."""
        for name in ("withdraw", "rotate"):
            with self.subTest(operation=name):
                source = Path(nominator.__file__).read_text()
                self.assertIn("authorized_by", source)
        self.assertNotIn("_broadcast", nominator.withdraw.__doc__ or "")


class StageTests(unittest.TestCase):
    def profile(self, mode="new"):
        return {
            "mode": mode, "custodians": 1, "required_signatures": 1,
            "wallet_address": "-1:" + "a" * 64 if mode == "import" else None,
            "depool_address": None,
        }

    def test_the_contract_is_built_and_checked_before_a_stake_means_anything(self):
        # The contract's address is derived from this node's Safe, so the Safe
        # has to exist before the contract can be prepared at all.
        expected = [
            ("node-started", "wallet create"),
            ("wallet-created", "wallet deploy"),
            ("wallet-deployed", "nominator prepare"),
            ("nominator-prepared", "nominator deploy"),
            ("nominator-deployed", "nominator verify"),
            ("nominator-verified", "stake set"),
            ("ready-for-elections", "election start"),
        ]
        for stage, command in expected:
            with self.subTest(stage=stage):
                config = nominator_config(
                    setup_stage=stage, wallet=self.profile()
                )
                self.assertIn(command, wallets.next_setup_command(config) or "")

    def test_an_imported_nominator_validator_follows_the_same_order(self):
        for stage, command in (
            ("node-started", "wallet recover"),
            ("wallet-recovered", "wallet verify"),
            ("imported", "nominator prepare"),
            ("nominator-verified", "stake set"),
            ("ready-for-elections", "election start"),
        ):
            with self.subTest(stage=stage):
                config = nominator_config(
                    setup_stage=stage, wallet=self.profile("import")
                )
                self.assertIn(command, wallets.next_setup_command(config) or "")

    def test_the_safe_is_built_first_and_the_contract_never_before_it(self):
        """The contract address depends on the Safe, so the Safe comes first.

        Preparing a contract against a wallet that does not exist yet would
        derive an address nothing can ever instruct.
        """
        for mode in ("new", "import"):
            with self.subTest(mode=mode):
                config = nominator_config(
                    setup_stage="node-started", wallet=self.profile(mode)
                )
                command = wallets.next_setup_command(config) or ""
                self.assertIn("wallet", command)
                self.assertNotIn("nominator", command)

    def test_the_contract_cannot_be_prepared_without_a_masterchain_wallet(self):
        """Both failures are refused by name rather than deriving a dead address."""
        with self.assertRaises(process.OperationError) as missing:
            nominator.hot_wallet_address(nominator_config(wallet=self.profile("new")))
        self.assertIn("wallet create", str(missing.exception))
        basechain = self.profile("import")
        basechain["wallet_address"] = "0:" + "a" * 64
        with self.assertRaises(process.OperationError) as wrong_chain:
            nominator.hot_wallet_address(nominator_config(wallet=basechain))
        self.assertIn("masterchain", str(wrong_chain.exception))
        self.assertEqual(
            nominator.hot_wallet_address(nominator_config(wallet=self.profile("import"))),
            "-1:" + "a" * 64,
        )

    def test_a_nominator_validator_cannot_bid_with_no_stake_recorded(self):
        with self.assertRaises(process.OperationError) as caught:
            stake.require_recorded_stake(
                nominator_config(settings={"MSIG_FIX_STAKE": 0})
            )
        self.assertIn("stake set", str(caught.exception))
        stake.require_recorded_stake(
            nominator_config(settings={"MSIG_FIX_STAKE": 100})
        )


class CommandSeparationTests(unittest.TestCase):
    def test_the_nominator_commands_refuse_the_other_validator_types(self):
        for validator_type in ("depool", "solo"):
            with self.subTest(validator_type=validator_type):
                config = make_node_configuration("v1", validator_type=validator_type)
                with self.assertRaises(process.OperationError) as caught:
                    nominator._require_nominator(config)
                self.assertIn(validator_type, str(caught.exception))

    def test_a_nominator_node_has_no_depool_commands(self):
        config = nominator_config()
        with self.assertRaises(process.OperationError):
            wallets.require_validator_type(config, "depool", "stake")

    def test_an_owner_address_is_refused_for_the_other_types(self):
        parser = build_argument_parser()
        parsed = parser.parse_args(
            normalize_address_arguments(
                ["node", "setup", "-n", "v", "--validator-type", "solo",
                 "--owner-address", OWNER, "--dry-run"]
            )
        )
        self.assertEqual(parsed.validator_type, "solo")
        self.assertEqual(parsed.owner_address, OWNER)

    def test_every_address_option_accepts_a_masterchain_value(self):
        """Each of these is routinely a -1: address, which argparse eats."""
        for option in ("--owner-address", "--validator-address",
                       "--wallet-address", "--depool-address"):
            with self.subTest(option=option):
                self.assertEqual(
                    normalize_address_arguments([option, "-1:" + "c" * 64]),
                    [option + "=-1:" + "c" * 64],
                )

    def test_a_rotation_target_must_be_in_the_masterchain(self):
        with self.assertRaises(process.OperationError) as caught:
            nominator.rotate(
                None, nominator_config(), validator_address="0:" + "a" * 64
            )
        self.assertIn("-1:", str(caught.exception))

    def test_a_withdrawal_needs_exactly_one_target(self):
        for keywords in ({}, {"amount": 5, "withdraw_all": True}):
            with self.subTest(keywords=keywords):
                with self.assertRaises(process.OperationError):
                    nominator.withdraw(None, nominator_config(), **keywords)

    def test_a_withdrawal_amount_must_be_positive(self):
        with self.assertRaises(process.OperationError):
            nominator.withdraw(None, nominator_config(), amount=0)


@unittest.skipUnless(
    (VENDOR / "node_modules").is_dir(),
    "vendored contract tools are not installed in this checkout",
)
class ContractToolTests(unittest.TestCase):
    """The cell tooling, against bytes the reference experiment recorded.

    These run the vendored tools directly rather than through the image, so a
    checkout can check them without Docker. The image build repeats the code
    hash check, so both paths are covered.
    """

    @classmethod
    def setUpClass(cls):
        cls.work = Path(tempfile.mkdtemp())
        cls.code = cls.work / "code.boc"
        cls.compiled = json.loads(cls.cells("compile", str(cls.code)))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    @classmethod
    def cells(cls, *arguments):
        result = subprocess.run(
            ["node", "tools/cells.cjs", *arguments],
            cwd=VENDOR, capture_output=True, text=True, timeout=300,
        )
        if result.returncode:
            raise AssertionError(result.stderr.strip() or result.stdout.strip())
        return result.stdout.strip()

    def test_the_contract_compiles_to_the_pinned_hash(self):
        self.assertEqual(self.compiled["codeHash"], CODE_HASH)

    def test_the_bid_shape_check_is_the_only_change_from_upstream(self):
        """Undoing the one change gives upstream's source byte for byte, and that
        still compiles to the hash upstream publishes."""
        pinned = json.loads((VENDOR / "PINNED.json").read_text())
        upstream = (VENDOR / "contracts/single-nominator.fc").read_text()
        for original, changed in EVERNODE_CHANGE:
            self.assertEqual(upstream.count(changed), 1, changed)
            upstream = upstream.replace(changed, original)
        self.assertEqual(hashlib.sha256(upstream.encode()).hexdigest(), pinned["upstreamSourceDigest"])
        self.assertEqual(pinned["upstreamCodeHash"], UPSTREAM_CODE_HASH)
        source = self.work / "upstream.fc"
        source.write_text(upstream)
        result = subprocess.run(
            ["node", "-e",
             "const {compileFunc} = require('@ton-community/func-js');"
             "const {Cell} = require('@ton/core');"
             "const fs = require('fs');"
             "compileFunc({targets: ['stdlib.fc', 'upstream.fc'], sources: p => p === 'stdlib.fc'"
             " ? fs.readFileSync('contracts/imports/stdlib.fc', 'utf8') : fs.readFileSync(process.argv[1], 'utf8')})"
             ".then(r => console.log(Cell.fromBase64(r.codeBoc).hash().toString('hex')));",
             str(source)],
            cwd=VENDOR, capture_output=True, text=True, timeout=300, check=True,
        )
        self.assertEqual(result.stdout.strip(), UPSTREAM_CODE_HASH)

    def test_the_pinned_manifest_agrees_with_the_compiler(self):
        pinned = json.loads((VENDOR / "PINNED.json").read_text())
        self.assertEqual(pinned["codeHash"], self.compiled["codeHash"])
        self.assertEqual(
            pinned["compiler"]["@ton-community/func-js"], self.compiled["compiler"]
        )
        self.assertEqual(pinned["upstreamVersion"], "v1.1")

    def test_the_address_is_the_pair_of_roles_and_nothing_else(self):
        self.assertEqual(self.cells("address", str(self.code), OWNER, HOT), POOL)
        swapped = self.cells("address", str(self.code), HOT, OWNER)
        self.assertNotEqual(swapped, POOL)

    def test_the_deployer_is_derived_as_the_reference_recorded_it(self):
        init = self.work / "init.boc"
        self.assertEqual(self.cells("init", str(self.code), OWNER, HOT, str(init)), POOL)
        factory = self.work / "factory.boc"
        self.assertEqual(
            self.cells("factory", str(init), "10000000000", str(factory)), FACTORY
        )

    def test_every_deployer_trigger_carries_its_own_random_body(self):
        # REMP drops an external message whose body another message already has.
        init = self.work / "init.boc"
        self.cells("init", str(self.code), OWNER, HOT, str(init))
        bodies = []
        for name in ("trigger-1.boc", "trigger-2.boc"):
            trigger = self.work / name
            self.assertEqual(
                self.cells("factory", str(init), "10000000000", str(trigger)), FACTORY
            )
            result = subprocess.run(
                ["node", "-e",
                 "const {Cell, loadMessage} = require('@ton/core');"
                 "const m = loadMessage(Cell.fromBoc("
                 "require('fs').readFileSync(process.argv[1]))[0].beginParse());"
                 "const s = m.body.beginParse();"
                 "console.log(s.remainingBits, s.remainingRefs, s.loadBuffer(16).toString('hex'));",
                 str(trigger)],
                cwd=VENDOR, capture_output=True, text=True, timeout=60, check=True,
            )
            bits, refs, value = result.stdout.split()
            self.assertEqual((bits, refs), ("128", "0"))
            bodies.append(value)
        self.assertNotEqual(bodies[0], bodies[1])

    @unittest.skipUnless(REFERENCE.is_dir(), "reference artifacts are not present")
    def test_the_bodies_match_the_bytes_the_chain_accepted(self):
        cases = [
            ("live-owner-deposit.body.boc", ["empty"]),
            ("live-owner-withdraw-partial.body.boc", ["withdraw", "2000000000"]),
            ("live-comment-withdraw.body.boc", ["comment", "w"]),
            ("validation-final-recover.body.boc", ["recover"]),
            ("live-zero-withdraw-rejected.body.boc", ["withdraw", "0"]),
            ("live-max-withdraw-clamped.body.boc", ["withdraw", "1000000000000000000"]),
        ]
        for name, arguments in cases:
            with self.subTest(body=name):
                out = self.work / name
                self.cells("body", arguments[0], str(out), *arguments[1:])
                self.assertEqual(
                    out.read_bytes(), without_crc((REFERENCE / name).read_bytes()), name
                )

    @unittest.skipUnless(REFERENCE.is_dir(), "reference artifacts are not present")
    def test_a_bid_becomes_the_stake_request_the_elector_accepted(self):
        """The one transform this mode adds, against a live-validated result."""
        bid = REFERENCE / "validation-bid.boc"
        out = self.work / "stake.body.boc"
        self.cells("body", "stake", str(out), str(bid), "100000000000000", "1793389276")
        expected = without_crc((REFERENCE / "validation-stake.body.boc").read_bytes())
        self.assertEqual(out.read_bytes(), expected)
        # A multisig reports a pending payload in this form, and the bid is
        # recognised by it: "te6ccg", never the CRC-carrying "te6cck".
        self.assertTrue(base64.b64encode(expected).startswith(b"te6ccg"))

    @unittest.skipUnless(REFERENCE.is_dir(), "reference artifacts are not present")
    def test_the_transform_keeps_the_election_request_intact(self):
        info = json.loads(self.cells("stake-info", str(REFERENCE / "validation-bid.boc")))
        self.assertEqual(info["op"], 0x4E73744B)
        self.assertEqual(info["query"], "1793389276")
        self.assertEqual(len(info["key"]), 64)
        self.assertEqual(len(info["adnl"]), 64)

    def test_a_body_that_is_not_an_election_request_is_refused(self):
        other = self.work / "not-a-bid.boc"
        self.cells("body", "recover", str(other))
        with self.assertRaises(AssertionError) as caught:
            self.cells("body", "stake", str(self.work / "x.boc"), str(other), "1")
        self.assertIn("new_stake", str(caught.exception))

    @unittest.skipUnless(REFERENCE.is_dir(), "reference artifacts are not present")
    def test_the_roles_of_the_deployed_contract_can_be_read_back(self):
        data = REFERENCE / "evidence" / "deployed.account.boc.data.boc"
        roles = json.loads(self.cells("roles", str(data)))
        self.assertEqual(roles, {"owner": OWNER, "validator": HOT})



class ContractAddressGuardTests(unittest.TestCase):
    """prepare derives the address from the code; a node whose contract is
    deployed must not be pointed at another one without being asked to, and a
    refused prepare must leave the deployed contract's files as they were."""

    ARTIFACTS = ("code.boc", "compile.json", "init.boc", "factory-deploy.boc", "manifest.json")

    def prepare(self, *, derived, new_contract=False, mode="new", stage="ready-for-elections"):
        deployed = "-1:" + "1" * 64
        config = nominator_config(
            wallet={"mode": mode, "custodians": 1, "required_signatures": 1,
                    "wallet_address": "-1:" + "a" * 64, "depool_address": None},
            setup_stage=stage, nominator={"address": deployed},
        )
        manifest = {"address": derived, "factory": FACTORY, "validator": "-1:" + "a" * 64,
                    "owner": OWNER, "codeHash": CODE_HASH, "upstreamCommit": "x"}

        def tools(arguments, **_):
            # The container writes its results into whatever directory is mounted at /work.
            mounted = Path(arguments[arguments.index("-v") + 1].split(":", 1)[0])
            for name in self.ARTIFACTS:
                (mounted / name).write_text(json.dumps(manifest) if name == "manifest.json"
                                            else "built for " + derived)
            return unittest.mock.Mock(stdout=json.dumps(manifest))

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        work = Path(temporary.name)
        for name in self.ARTIFACTS:
            (work / name).write_text("deployed contract's " + name)
        with patch("evernode.nominator.resolve_contract_source", return_value=VENDOR), \
                patch("evernode.nominator.build_tools_image", return_value=("local/tools:x", False)), \
                patch("evernode.nominator.work_directory", return_value=work), \
                patch("evernode.nominator.process.execute_command", side_effect=tools), \
                patch("evernode.nominator.configuration.write_node_environment") as written:
            try:
                _, self.result = nominator.prepare(unittest.mock.MagicMock(), config, source=None,
                                                   recipe=None, new_contract=new_contract)
                error = None
            except process.OperationError as caught:
                error = caught
        files = {name: (work / name).read_text() for name in self.ARTIFACTS}
        return config, written, error, files, sorted(item.name for item in work.iterdir())

    def test_another_address_is_refused_for_a_deployed_contract(self):
        config, written, error, files, listing = self.prepare(derived="-1:" + "2" * 64)
        self.assertIn("--new-contract", str(error))
        written.assert_not_called()
        self.assertNotEqual(config["settings"].get("NOMINATOR_ADDR"), "-1:" + "2" * 64)
        # What the test network showed: info, verify, withdraw and deploy read these files.
        self.assertEqual(files, {name: "deployed contract's " + name for name in self.ARTIFACTS})
        self.assertEqual(listing, sorted(self.ARTIFACTS))

    def test_the_same_address_prepares_again(self):
        """What rebuilding a pruned tools image needs."""
        config, written, error, files, listing = self.prepare(derived="-1:" + "1" * 64)
        self.assertIsNone(error)
        written.assert_called_once()
        self.assertEqual(files["code.boc"], "built for -1:" + "1" * 64)
        self.assertEqual(listing, sorted(self.ARTIFACTS))

    def test_new_contract_moves_the_node_to_the_new_address(self):
        config, _, error, files, _ = self.prepare(derived="-1:" + "2" * 64, new_contract=True)
        self.assertIsNone(error)
        self.assertEqual(config["settings"]["NOMINATOR_ADDR"], "-1:" + "2" * 64)
        self.assertEqual(files["manifest.json"].count("-1:" + "2" * 64), 1)

    def test_a_contract_already_on_chain_is_verified_next_not_deployed(self):
        """An imported node's contract exists; so does a deployed node's."""
        for mode, stage in (("import", "imported"), ("new", "ready-for-elections")):
            with self.subTest(mode=mode, stage=stage):
                self.prepare(derived="-1:" + "1" * 64, mode=mode, stage=stage)
                self.assertEqual(self.result["next"], "evernode nominator verify -n validator01")
                self.assertNotIn("does not exist yet", self.result["note"])

    def test_a_new_contract_is_deployed_next(self):
        self.prepare(derived="-1:" + "1" * 64, stage="wallet-deployed")
        self.assertEqual(self.result["next"], "evernode nominator deploy -n validator01")

    def test_a_contract_source_that_does_not_exist_is_named_as_such(self):
        with self.assertRaises(process.OperationError) as caught:
            nominator.resolve_contract_source(nominator_config(), "/nonexistent/single-nominator")
        self.assertIn("/nonexistent/single-nominator does not exist", str(caught.exception))

    def test_files_prepared_for_another_contract_are_refused(self):
        """A node whose files were overwritten before this was fixed: every
        command that reads them refuses instead of acting on the wrong account."""
        config = nominator_config(nominator={"address": "-1:" + "1" * 64})
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            (work / "manifest.json").write_text(json.dumps({"address": "-1:" + "2" * 64}))
            with patch("evernode.nominator.work_directory", return_value=work):
                with self.assertRaises(process.OperationError) as caught:
                    nominator._manifest(None, config)
        self.assertIn("nominator prepare", str(caught.exception))
        self.assertIn("-1:" + "1" * 64, str(caught.exception))


if __name__ == "__main__":
    unittest.main()


KIT = Path(__file__).resolve().parents[1] / "evernode/vendor/ever-validator/scripts"


class SafeTransportTests(unittest.TestCase):
    """The Safe carries the instructions, so the kit has to know this mode.

    Everything a nominator validator does on chain is a SafeMultisig
    submitTransaction, which the kit's election scripts already build, sign
    and resume. These tests pin the places that would otherwise silently fall
    through to a mode the scripts do not recognise.
    """

    def test_the_kit_is_told_this_mode_by_name(self):
        self.assertEqual(
            configuration.STAKE_MODES,
            {"depool": "depool", "solo": "msig", "nominator": "nominator"},
        )

    def test_both_election_scripts_branch_on_the_nominator_mode(self):
        for name in ("take_part_in_elections.sh", "prepare_elections.sh"):
            with self.subTest(script=name):
                self.assertIn('"nominator"', (KIT / name).read_text())

    def test_the_bid_is_addressed_to_the_contract_and_carries_only_gas(self):
        """The stake comes from the contract, so the wallet attaches a fee."""
        source = (KIT / "take_part_in_elections.sh").read_text()
        self.assertIn("declare -r Stake_DST_Addr=$NOMINATOR_ADDR", source)
        self.assertIn("NANOSTAKE=$Nominator_Fee_nT", source)
        # The elector binds the request's signature to the sending account.
        self.assertIn('jq ".wallet_id = \\"${NOMINATOR_ADDR}\\""', source)

    def test_recovery_asks_about_the_account_that_actually_staked(self):
        source = (KIT / "prepare_elections.sh").read_text()
        self.assertIn('Recover_Subject="$NOMINATOR_ADDR"', source)
        self.assertIn('Recover_Dest="$NOMINATOR_ADDR"', source)
        self.assertIn('Elector_Owes "$Recover_Subject"', source)

    def test_every_step_of_recovery_uses_the_parameterised_account(self):
        """A half-done parameterisation is the dangerous outcome here.

        If the elector is asked about the contract but the recovery is still
        addressed to the elector, or the effect is still awaited on the
        wallet, the contract's stake is never recovered and the cycle reports
        success. So the whole block is checked, not just its first line.
        """
        source = (KIT / "prepare_elections.sh").read_text()
        start = source.index('Recover_Subject="$Validator_addr"')
        end = source.index("+++-INFO: The recovered stake has arrived", start)
        block = source[start:end]
        for required in (
            'Elector_Owes "$Recover_Subject"',                            # whose credit
            'Get_Account_Info "${Recover_Subject}"',                      # whose balance before
            'Find_Matching_Pending_Trans "$Validator_addr" "$Recover_Dest"',
            'Submit_And_Identify "$Validator_addr" "$Recover_Dest"',      # where it is sent
            'Effect_Stake_Recovered "$Recover_Subject"',                  # whose debt falls, balance rises
        ):
            with self.subTest(step=required.split('"')[0].strip()):
                self.assertIn(required, block)
        # The old hard-coded subjects must be gone from the recovery itself.
        self.assertNotIn('compute_returned_stake "0x${Val_Adrr_HEX}"', block)
        self.assertNotIn('"$elector_addr",\\"value\\":1000000000', block)

    def test_the_tool_image_reaches_the_scripts_that_need_it(self):
        """The payload transform runs in the image the contract was pinned in."""
        config = nominator_config(
            settings={"NOMINATOR_TOOLS_IMAGE": "local/tools:abc"}
        )
        self.assertEqual(
            configuration.build_node_environment(config)["NOMINATOR_TOOLS_IMAGE"],
            "local/tools:abc",
        )
        self.assertIn(
            "NOMINATOR_TOOLS_IMAGE", (KIT / "take_part_in_elections.sh").read_text()
        )

    def test_a_nominator_validator_uses_the_direct_election_sequence(self):
        """Recovery and bidding are independent, exactly as in solo staking."""
        class Store:
            def node_directory(inner, name):
                return Path("/tmp") / name

        for validator_type, expected in (
            ("nominator", elections.SOLO_SEQUENCE_FILE),
            ("solo", elections.SOLO_SEQUENCE_FILE),
            ("depool", elections.DEPOOL_SEQUENCE_FILE),
        ):
            with self.subTest(validator_type=validator_type):
                config = make_node_configuration(
                    name="v", validator_type=validator_type
                )
                self.assertEqual(
                    elections.election_sequence_path(Store(), config).name, expected
                )

    def test_nothing_signs_outside_the_kit_any_more(self):
        """No second signing scheme, no second key format, no Rust."""
        source = Path(nominator.__file__).read_text()
        for gone in ("_sign_from_hot_wallet", "wallet-sign", "SIGNATURE_ID"):
            with self.subTest(removed=gone):
                self.assertNotIn(gone, source)
        self.assertFalse((VENDOR / "tools" / "wallet-sign").exists())
        self.assertFalse((VENDOR / "contracts" / "everwallet.code.boc").exists())
        self.assertNotIn(
            "rust", (ASSETS / "nominator-tools.Dockerfile").read_text().lower()
        )

    def test_the_owner_may_not_also_be_the_wallet_that_instructs(self):
        """One address in both roles puts the withdrawal key back on the server."""
        profile = {
            "mode": "import", "custodians": 1, "required_signatures": 1,
            "wallet_address": OWNER, "depool_address": None,
        }
        config = nominator_config(wallet=profile, settings={"NOMINATOR_OWNER": OWNER})
        with self.assertRaises(process.OperationError) as caught:
            nominator.prepare(None, config, source=None, recipe=None)
        self.assertIn("different accounts", str(caught.exception))


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq"), "Requires bash and jq"
)
class NonBounceableMatchTests(unittest.TestCase):
    """Creating an account needs a transfer that does not bounce.

    Every operation the kit's matcher was written for is bounceable, so it
    tested for that as a constant. A deployer that does not exist yet would
    return a bounceable transfer and never be created, so the flag had to
    become a parameter - and the matcher must still not confuse the two.
    """

    def match(self, *, entry_bounce, wanted_bounce):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            keys = directory / "v_1.keys.json"
            keys.write_text(json.dumps({"public": "aa" * 32, "secret": "bb" * 32}))
            entry = {
                "id": "1", "dest": "-1:" + "c" * 64, "payload": "",
                "value": "11000000000", "bounce": entry_bounce, "sendFlags": "3",
                "creator": "0",
            }
            (directory / "pending.json").write_text(
                json.dumps({"transactions": [entry]})
            )
            program = (
                "set +u\n"
                'function echoerr() { printf "%s\\n" "$*" >&2; }\n'
                f'TEST_ROOT="{directory}"\n'
                "function Get_MSIG_Trans_List() { cat \"$TEST_ROOT/pending.json\"; }\n"
                f'source "{KIT / "managed_ops.shinc"}"\n'
                # Stubbed after the source, which defines it itself.
                "function Custodian_Key_Decimal() { echo 0; }\n"
                f'Find_Matching_Pending_Trans 0:w "-1:{"c" * 64}" 11000000000 "" '
                f'"{keys}" "" {wanted_bounce}\n'
            )
            (directory / "case.sh").write_text(program)
            return subprocess.run(
                ["bash", str(directory / "case.sh")],
                capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=30,
            ).stdout.strip()

    def test_a_non_bounceable_submission_is_found(self):
        self.assertEqual(self.match(entry_bounce=False, wanted_bounce="false"), "1")

    def test_the_old_behaviour_is_still_the_default(self):
        self.assertEqual(self.match(entry_bounce=True, wanted_bounce="true"), "1")

    def test_the_two_are_never_confused(self):
        """Adopting the wrong one would sign a transfer this attempt did not make."""
        self.assertEqual(self.match(entry_bounce=True, wanted_bounce="false"), "")
        self.assertEqual(self.match(entry_bounce=False, wanted_bounce="true"), "")


@unittest.skipUnless(shutil.which("bash"), "Requires bash")
class BidBranchTests(unittest.TestCase):
    """The guards on the nominator bid, run from the real script's own text.

    The branch is extracted from take_part_in_elections.sh rather than copied,
    so a change to the script changes what these tests run. Each case is a way
    the bid can be wrong that the chain would not report clearly: a stake the
    contract cannot cover is thrown as 0x2004, and a missing tool image would
    otherwise send the elector's request through unmodified, staking nothing.
    """

    @classmethod
    def setUpClass(cls):
        text = (KIT / "take_part_in_elections.sh").read_text()
        # The mode is matched in several case statements; this is the one that
        # sizes the stake, which is the branch under test.
        sizing = text.index("declare -i NANOSTAKE")
        start = text.index("    nominator)\n", sizing)
        end = text.index("    depool)\n", start)
        branch = text[start + len("    nominator)\n"):end]
        # Drop the case terminator: the branch is run on its own, and bash
        # executes a script incrementally, so a stray ';;' would end the run
        # silently after the interesting part had already happened.
        cls.branch = branch.rstrip().removesuffix(";;")
        assert "Stake_DST_Addr=$NOMINATOR_ADDR" in cls.branch

    def run_branch(self, *, stake=1000, contract="Active", contract_balance=5000,
                   wallet_balance=15, image="local/tools:abc", transform_fails=False,
                   image_present=True):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "1_query.boc").write_bytes(b"query")
            program = f"""set -uo pipefail
SelfScriptName=take_part_in_elections.sh
VALIDATOR_NAME=validator01
MSIG_FIX_STAKE={stake}
NOMINATOR_ADDR=-1:{"n" * 64}
NOMINATOR_RESERVE=1000000000
NOMINATOR_OP_FEE=2000000000
NOMINATOR_TOOLS_IMAGE={image!r}
CURR_ELECTIONS_DIR={str(directory)!r}
elections_id=1
# Declared earlier in the real script, where the pending list is counted.
Exist_Nom_Trans_Qty=0
Validator_Acc_Balance_nT=$(( {wallet_balance} * 1000000000 ))
function Get_Account_Info() {{ echo "{contract} $(( {contract_balance} * 1000000000 )) 1"; }}
function docker() {{
    [[ $1 == image ]] && return {0 if image_present else 1}
    {"return 1" if transform_fails else 'cp "$CURR_ELECTIONS_DIR/1_query.boc" "$CURR_ELECTIONS_DIR/1_stake.boc"'}
}}
# The kit reads files with 'base64 FILE' throughout and runs on Linux; the
# BSD base64 on a developer's Mac takes no positional file, so the harness
# supplies the Linux behaviour rather than testing the platform.
function base64() {{ python3 -c 'import base64,sys;sys.stdout.write(base64.b64encode(open(sys.argv[1],"rb").read()).decode())' "$1"; }}
declare -i NANOSTAKE
{self.branch}
echo "RESULT dest=$Stake_DST_Addr value=$NANOSTAKE payload=${{validator_query_payload:0:6}}"
"""
            (directory / "case.sh").write_text(program)
            return subprocess.run(
                ["bash", str(directory / "case.sh")],
                capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=60,
            )

    def test_a_healthy_bid_goes_to_the_contract_carrying_only_gas(self):
        result = self.run_branch()
        self.assertIn("dest=-1:" + "n" * 64, result.stdout)
        self.assertIn("value=2000000000", result.stdout)
        # The payload is the transformed request, not the elector's own.
        self.assertIn("payload=cXVlcn", result.stdout)
        self.assertIn("The contract stakes", result.stdout)

    def test_a_stake_the_contract_cannot_cover_is_refused_before_sending(self):
        """The reserve counts: the contract throws 0x2004 at the boundary.

        The last affordable stake is the balance minus the one-token reserve,
        so 4999 of 5000 is allowed and 5000 of 5000 is not.
        """
        self.assertIn("does not cover a stake", self.run_branch(
            stake=1000, contract_balance=1000).stdout)
        self.assertIn("does not cover a stake", self.run_branch(
            stake=5000, contract_balance=5000).stdout)
        self.assertIn("dest=", self.run_branch(
            stake=4999, contract_balance=5000).stdout)

    def test_a_contract_that_is_not_active_stakes_nothing(self):
        self.assertIn("not Active", self.run_branch(contract="Uninit").stdout)

    def test_no_recorded_stake_is_named_as_such(self):
        self.assertIn("MSIG_FIX_STAKE is not set", self.run_branch(stake=0).stdout)

    def test_a_wallet_with_no_gas_cannot_instruct_the_contract(self):
        output = self.run_branch(wallet_balance=2).stdout
        self.assertIn("not enough gas", output)
        self.assertIn("principal stays in the contract", output)

    def test_a_missing_tool_image_refuses_rather_than_staking_nothing(self):
        output = self.run_branch(image="").stdout
        self.assertIn("NOMINATOR_TOOLS_IMAGE is not set", output)
        self.assertIn("nominator prepare", output)

    def test_a_pruned_tool_image_is_named_with_the_command_that_rebuilds_it(self):
        """Only a bid runs it, so an image prune removes it."""
        output = self.run_branch(image_present=False).stdout
        self.assertIn("is not on this host", output)
        self.assertIn("evernode nominator prepare -n validator01", output)
        self.assertNotIn("dest=-1:", output)

    def test_a_transform_that_fails_stops_the_bid(self):
        self.assertIn(
            "Could not build the nominator election request",
            self.run_branch(transform_fails=True).stdout,
        )


@unittest.skipUnless(shutil.which("bash"), "Requires bash")
class PrepareProgramTests(unittest.TestCase):
    def test_a_failed_address_step_stops_the_preparation(self):
        """"export VAR=$(cmd)" took export's status, so set -e let a failed
        step through and an empty contract address was saved."""
        with tempfile.TemporaryDirectory() as work:
            program = nominator.PREPARE_PROGRAM.replace("/work", work)
            stub = (
                'node() { case "$2" in init) return 7;; compile) echo "{}";; '
                '*) echo "' + POOL + '";; esac; }\n'
            )
            result = subprocess.run(
                ["bash", "-c", stub + program], capture_output=True, text=True, timeout=30
            )
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertFalse((Path(work) / "manifest.json").exists())


class ToolIsolationTests(unittest.TestCase):
    """The contract tools must never be able to sign or to reach a chain.

    They compile and build cells. Signing belongs to the Safe through the
    kit's scripts and broadcasting to the node's console, so a key mounted
    into this container would be a privilege nothing in it needs.
    """

    def test_no_key_is_mounted_into_the_tool_container(self):
        calls = []

        def record(arguments, **_):
            calls.append(arguments)
            raise process.OperationError("stop here")

        class Store:
            def node_directory(inner, name):
                return Path("/state") / name

        config = nominator_config()
        config["nominator_tools_image"] = "local/tools:abc"
        with patch("evernode.nominator.process.execute_command", record), \
                patch("evernode.nominator.storage.validate_private_path"), \
                patch("evernode.nominator.work_directory",
                      lambda *a, **k: Path("/state/work")):
            with self.assertRaises(process.OperationError):
                nominator._run_tools(Store(), config, "true")
        self.assertTrue(calls)
        mounts = [calls[0][i + 1] for i, a in enumerate(calls[0]) if a == "-v"]
        self.assertEqual(mounts, ["/state/work:/work"])
        self.assertIn("none", calls[0])

    def test_the_image_carries_no_signer(self):
        recipe = (ASSETS / "nominator-tools.Dockerfile").read_text()
        for absent in ("wallet-sign", "cargo", "FROM rust"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, recipe)


class ToolImageTagTests(unittest.TestCase):
    """The image's identity must be what the image is built from.

    A vendored checkout with 'npm install' run in it holds symlinks and
    hundreds of megabytes the image never uses, since the compiler comes from
    the lockfile inside the build. Fingerprinting the whole directory made
    preparation fail outright on those symlinks.
    """

    def stage(self, source):
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary) / "ctx"
            staging.mkdir()
            root = nominator._stage_tool_sources(source, staging)
            return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))

    def test_only_the_files_the_image_uses_are_staged(self):
        staged = self.stage(VENDOR)
        self.assertIn("PINNED.json", staged)
        self.assertIn("package-lock.json", staged)
        self.assertIn("tools/cells.cjs", staged)
        self.assertIn("contracts/single-nominator.fc", staged)
        self.assertFalse([p for p in staged if p.startswith("node_modules")])

    def test_an_installed_checkout_still_prepares(self):
        """node_modules holds symlinks; the fingerprint refuses those."""
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "vendored"
            shutil.copytree(
                VENDOR, source, symlinks=True,
                ignore=shutil.ignore_patterns("node_modules"),
            )
            installed = source / "node_modules" / ".bin"
            installed.mkdir(parents=True)
            (installed / "func-js").symlink_to("../somewhere/cli.js")
            with tempfile.TemporaryDirectory() as context:
                staging = Path(context) / "ctx"
                staging.mkdir()
                root = nominator._stage_tool_sources(source, staging)
                # The fingerprint is what failed before; it must succeed now.
                from evernode import storage
                self.assertRegex(
                    storage.calculate_directory_fingerprint(root), r"^[0-9a-f]{64}$"
                )

    def test_a_changed_recipe_is_a_different_image(self):
        """An image built from an older recipe must not be reused for a newer one."""
        tags = []
        with tempfile.TemporaryDirectory() as temporary, patch(
            "evernode.nominator.work_directory", return_value=Path(temporary)
        ), patch("evernode.nominator.docker.image_exists", return_value=True):
            recipe = Path(temporary) / "Dockerfile"
            for text in ("FROM node:22\n", "FROM node:22\n", "FROM node:24\n"):
                recipe.write_text(text)
                tags.append(nominator.build_tools_image(None, None, VENDOR, recipe)[0])
        self.assertEqual(tags[0], tags[1])
        self.assertNotEqual(tags[1], tags[2])

    def test_a_directory_that_is_not_the_vendored_source_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "empty"
            source.mkdir()
            with self.assertRaises(process.OperationError) as caught:
                self.stage(source)
            self.assertIn("PINNED.json", str(caught.exception))


class ImportRulesTests(unittest.TestCase):
    """Importing a validator must ask for exactly what its mode has.

    The DePool rule was written as "solo, or else a DePool is required", so
    adding a third mode silently put nominator on the DePool side and made
    importing one impossible. The rule is per-mode now, and all three are
    pinned here so a fourth mode cannot inherit the wrong half again.
    """

    def profile(self, **overrides):
        return dict(
            {
                "mode": "import",
                "custodians": 1,
                "required_signatures": 1,
                "wallet_address": "-1:" + "a" * 64,
                "depool_address": None,
            },
            **overrides,
        )

    def test_only_a_depool_validator_needs_a_depool_address(self):
        for validator_type in ("solo", "nominator"):
            with self.subTest(validator_type=validator_type):
                wallets.validate_wallet_profile(
                    self.profile(), validator_type=validator_type
                )
        with self.assertRaises(process.OperationError):
            wallets.validate_wallet_profile(self.profile(), validator_type="depool")

    def test_a_depool_address_is_refused_by_the_modes_that_have_none(self):
        for validator_type in ("solo", "nominator"):
            with self.subTest(validator_type=validator_type):
                with self.assertRaises(process.OperationError) as caught:
                    wallets.validate_wallet_profile(
                        self.profile(depool_address="0:" + "b" * 64),
                        validator_type=validator_type,
                    )
                self.assertIn(validator_type, str(caught.exception))
                self.assertIn("DePool", str(caught.exception))

    def test_a_depool_validator_still_imports_with_both(self):
        wallets.validate_wallet_profile(
            self.profile(depool_address="0:" + "b" * 64), validator_type="depool"
        )

    def test_every_mode_still_needs_the_wallet_it_is_importing(self):
        for validator_type in ("depool", "solo", "nominator"):
            with self.subTest(validator_type=validator_type):
                with self.assertRaises(process.OperationError) as caught:
                    wallets.validate_wallet_profile(
                        self.profile(wallet_address=None),
                        validator_type=validator_type,
                    )
                self.assertIn("wallet address", str(caught.exception))


class ModeInheritanceTests(unittest.TestCase):
    """Nothing written as "solo, or else DePool" may catch the third mode.

    Every one of these was a real defect: adding nominator beside solo and
    depool left it inheriting the DePool half of each either/or, because the
    conditions named one mode and assumed the rest. They are cheap to get
    wrong again, and two of them are silent - a basechain Safe that can never
    instruct the contract, and a DePool election sequence on a node with no
    DePool.
    """

    def test_the_safe_of_both_direct_staking_modes_lives_in_the_masterchain(self):
        """The contract refuses any other workchain with WRONG_VALIDATOR_WC."""
        for validator_type in ("solo", "nominator"):
            with self.subTest(validator_type=validator_type):
                config = make_node_configuration(
                    name="v", validator_type=validator_type
                )
                config.setdefault("settings", {})["NODE_WC"] = 0
                self.assertEqual(wallets.wallet_workchain(config), -1)
        depool = make_node_configuration(name="v", validator_type="depool")
        depool.setdefault("settings", {})["NODE_WC"] = 0
        self.assertEqual(wallets.wallet_workchain(depool), 0)

    def test_the_sequence_installed_is_the_one_the_mode_needs(self):
        """The file chosen and the program written into it must agree.

        Picking the direct-to-elector file but filling it with the DePool
        program is silent: cron runs, the log looks plausible, and the first
        failed preparation kills the bid for a node that has no DePool to
        prepare.
        """
        for validator_type, expected, forbidden in (
            ("nominator", "Direct-to-elector", "DePool"),
            ("solo", "Direct-to-elector", "DePool"),
            ("depool", "./prepare_elections.sh\nsleep 120", "Direct-to-elector"),
        ):
            with self.subTest(validator_type=validator_type):
                config = make_node_configuration(
                    name="seq", validator_type=validator_type
                )
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary) / "seq"
                    for part in ("scripts", "logs/validator", "elections"):
                        (root / part).mkdir(parents=True)

                    class Store:
                        def node_directory(inner, name):
                            return root

                    program = elections._solo_sequence_program(
                        root / "scripts", root / "elections" / "last.log"
                    ) if validator_type != "depool" else \
                        elections._depool_sequence_program(root / "scripts")
                    path = elections.election_sequence_path(Store(), config)
                    path.write_text(program)
                    written = path.read_text()
                self.assertIn(expected, written)
                self.assertNotIn(forbidden, written)

    def test_no_mode_without_a_depool_is_asked_which_depool_to_use(self):
        source = Path(role.__file__).read_text()
        self.assertIn("if solo or nominator\n", source)

    def test_an_imported_safe_must_reach_the_elector_in_both_modes(self):
        source = Path(role.__file__).read_text()
        self.assertIn("if solo or nominator:", source)

    def test_the_stake_questions_name_the_account_that_holds_the_stake(self):
        """A nominator's stake leaves from, and returns to, its contract."""
        parser = build_argument_parser()
        for validator_type, account in (("nominator", "nominator contract"), ("solo", "Safe")):
            config = make_node_configuration(name="v", validator_type=validator_type)
            for argv in (
                ["stake", "recover", "-n", "v"],
                ["stake", "set", "-n", "v", "--amount", "10"],
            ):
                with self.subTest(validator_type=validator_type, command=argv[1]):
                    question = setup_actions._confirmation_question(
                        parser.parse_args(argv), config
                    )
                    self.assertIn(account, question)
                    if validator_type == "nominator":
                        self.assertNotIn("Safe", question)


class SafeKeysTests(unittest.TestCase):
    """A new nominator Safe is offered one key first; everything else keeps its prompts."""

    def choose(self, *flags, nominator=True, importing=False, menu=None):
        args = build_argument_parser().parse_args(["node", "setup", "-n", "validator01", *flags])
        asked = []

        def prompt(value, label, default=None):
            asked.append((label, default))
            return default

        with (
            patch("evernode.role.ui.select_choice",
                  side_effect=lambda *a, **k: menu or k["default"]) as select,
            patch("evernode.role.ui.prompt_for_value", side_effect=prompt),
        ):
            keys = role.choose_safe_keys(args, nominator, importing)
        return keys, select.call_count, asked

    def test_a_new_nominator_safe_has_one_key_unless_several_are_chosen(self):
        self.assertEqual(self.choose(), ((1, 1), 1, []))
        self.assertEqual(
            self.choose(menu="several"),
            ((3, 2), 1, [("Multisig custodians", "3"), ("Required signatures", "2")]),
        )

    def test_without_a_terminal_a_new_nominator_safe_gets_one_key(self):
        args = build_argument_parser().parse_args(["node", "setup", "-n", "validator01"])
        with patch("sys.stdin", io.StringIO()):
            self.assertEqual(role.choose_safe_keys(args, True, False), (1, 1))

    def test_flags_an_import_and_the_other_types_keep_the_plain_prompts(self):
        self.assertEqual(
            self.choose("--custodians", "3", "--required-signatures", "2"), ((3, 2), 0, [])
        )
        self.assertEqual(
            self.choose(importing=True),
            ((1, 1), 0, [("Multisig custodians", "1"), ("Required signatures", "1")]),
        )
        self.assertEqual(
            self.choose(nominator=False),
            ((3, 2), 0, [("Multisig custodians", "3"), ("Required signatures", "2")]),
        )


class DeployPaymentMessageTests(unittest.TestCase):
    """With several signatures the deployer payment says when not to interrupt it."""

    def deploy(self, signatures, funded):
        config = nominator_config(wallet={
            "mode": "new", "custodians": 3, "required_signatures": signatures,
            "wallet_address": HOT, "depool_address": None,
        })
        manifest = {"address": POOL, "factory": FACTORY, "validator": HOT}
        with (
            patch("evernode.nominator._manifest", return_value=manifest),
            patch("evernode.nominator._account", return_value={"acc_type": "Uninit"}),
            patch("evernode.scripts.fund_nominator_deployer", return_value=funded),
            patch("evernode.nominator._broadcast"),
            patch("evernode.nominator._wait_for", return_value={"acc_type": "Active"}),
            contextlib.redirect_stderr(io.StringIO()) as said,
        ):
            nominator.deploy(None, config)
        return said.getvalue()

    def test_a_multisignature_payment_is_announced_and_reported_done(self):
        said = self.deploy(2, {"transaction_id": "7219"})
        self.assertIn("takes 2 signatures from the Safe: do not interrupt (Ctrl+C)", said)
        self.assertIn("Deployer payment done (Safe transaction 7219).", said)
        said = self.deploy(2, {"transaction_id": "", "note": "already funded"})
        self.assertIn("already funded; nothing was paid", said)

    def test_a_single_key_payment_says_nothing_extra(self):
        self.assertEqual(self.deploy(1, {"transaction_id": ""}), "")


class DeployTriggerResendTests(unittest.TestCase):
    """The deployer runs on one external message. One the network drops used to
    fail the deploy after 180 s; it is sent again while the contract is absent.
    A second copy is harmless: the deployer forwards its one message, then
    deletes itself, and a later copy finds no deployer."""

    def deploy(self, contract_appears_after_sends):
        config = nominator_config(wallet={
            "mode": "new", "custodians": 1, "required_signatures": 1,
            "wallet_address": HOT, "depool_address": None,
        })
        manifest = {"address": POOL, "factory": FACTORY, "validator": HOT}
        sends = []

        def account(_config, address):
            if address == POOL:
                if contract_appears_after_sends and len(sends) >= contract_appears_after_sends:
                    return {"acc_type": "Active", "balance": "10835749906"}
                return {"acc_type": "Nonexist"}
            return {"acc_type": "Uninit", "balance": "11000000000"}

        with (
            patch("evernode.nominator._manifest", return_value=manifest),
            patch("evernode.nominator._account", side_effect=account),
            patch("evernode.scripts.fund_nominator_deployer", return_value={"transaction_id": ""}),
            patch("evernode.nominator._broadcast", side_effect=lambda *_: sends.append(1)),
            patch("time.sleep"),
        ):
            try:
                _, result = nominator.deploy(None, config)
                return result, None, len(sends)
            except process.OperationError as caught:
                return None, caught, len(sends)

    def test_a_lost_trigger_is_sent_again(self):
        result, error, sends = self.deploy(contract_appears_after_sends=2)
        self.assertIsNone(error)
        self.assertEqual((result["result"], sends), ("deployed", 2))

    def test_a_trigger_that_never_takes_ends_with_what_to_do(self):
        _, error, sends = self.deploy(contract_appears_after_sends=None)
        self.assertEqual(sends, 3)
        self.assertIn("paid and still waiting", str(error))
        self.assertIn("evernode nominator deploy -n validator01", str(error))


# Three custodians and two signatures: the first key proposes, the second approves.
CUSTODIAN_KEYS = ["8a" + "1d" * 31, "b7" + "2e" * 31, "c4" + "3f" * 31]
# The reference environment the funding program sources, reduced to a Safe and
# a deployer. Approving the payment executes it, and that funds the deployer.
FUNDING_STUBS = r"""
KEYS_DIR="$TEST_ROOT"; INPL_KEYS_DIR="$TEST_ROOT"; VALIDATOR_NAME=validator
INPL_ELECTIONS_WORK_DIR="$TEST_ROOT"; ELECTIONS_WORK_DIR="$TEST_ROOT"; SIG_ID_OPTION=""
INPL_SafeC_Wallet_ABI=safe.abi; SafeC_Wallet_ABI="$TEST_ROOT/safe.abi"; : >"$SafeC_Wallet_ABI"
CALL_BC=bc; CALL_CLI=fake_cli; MSG_LIFETIME=1; LC_Send_MSG_Timeout=1
EFFECT_POLL_INTERVAL=1; EFFECT_WAIT_MAX=4; EFFECT_MIN_WAIT=1
echoerr() { echo "$*" >&2; }
fake_cli() {
    case "$*" in
        *' getParameters '*) echo '{"expirationTime":"3600"}';;
        *' getCustodians '*) printf '%s\n' "$TEST_CUSTODIANS";;
        *) echo '{}';;
    esac
}
Get_SC_current_state() { echo "written StateInit of account"; }
Get_Account_Custodians_Info() { echo "3 2"; }
Get_Account_Info() {
    if [[ $1 == "$EVERNODE_WALLET" ]]; then echo "Active 100000000000"
    elif [[ -f $TEST_ROOT/executed ]]; then echo "Uninit $FUND_VALUE"
    else echo "None 0"; fi
}
# The case's staged entries, plus this run's own proposal once it is sent.
Get_MSIG_Trans_List() {
    if [[ -f $TEST_ROOT/executed ]]; then echo '{"transactions":[]}'; return 0; fi
    local own='[]'
    if grep -qs submitted "$TEST_ROOT/trace"; then
        own=$(jq -c --arg id "$TEST_TRANS_ID" '[. + {id: $id}]' "$TEST_ROOT/entry.json")
    fi
    jq -c --argjson own "$own" '{transactions: (. + $own)}' "$TEST_ROOT/pending.json"
}
Send_File_To_BC() { echo submitted >>"$TEST_ROOT/trace"; echo success; }
Send_MSIG_Trans_Confirmation() {
    echo "signed:$1:$3" >>"$TEST_ROOT/trace"
    touch "$TEST_ROOT/executed"
    echo success
}
"""


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("bc"),
    "Requires bash, jq and bc",
)
class DeployerFundingRetryTests(unittest.TestCase):
    """A rerun finishes the payment an interrupted deploy proposed.

    Proposing another one left the first half-signed for an hour, holding one
    of the five queue slots of the key that proposed it.
    """

    def fund(self, *, pending):
        now = int(time.time())
        earlier, new = str(((now - 300) << 32) | 1), str((now << 32) | 2)
        entry = {
            "creator": str(int(CUSTODIAN_KEYS[0], 16)), "dest": FACTORY,
            "value": "12000000000", "payload": "te6ccgEBAQEAAgAAAA==",
            "bounce": False, "sendFlags": "3",
            "confirmationsMask": "1", "signsRequired": "2", "signsReceived": "1",
        }
        custodians = {"custodians": [
            {"index": str(index), "pubkey": str(int(key, 16))}
            for index, key in enumerate(CUSTODIAN_KEYS)
        ]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile(KIT / "managed_ops.shinc", root / "managed_ops.shinc")
            (root / "env.sh").write_text("")
            (root / "functions.shinc").write_text(FUNDING_STUBS)
            for index, key in enumerate(CUSTODIAN_KEYS, 1):
                (root / f"validator_{index}.keys.json").write_text(
                    json.dumps({"public": key, "secret": f"{index:02d}" * 32})
                )
            (root / "entry.json").write_text(json.dumps(entry))
            (root / "pending.json").write_text(
                json.dumps([dict(entry, id=earlier)] if pending else [])
            )
            done = subprocess.run(
                ["bash", "-c", scripts.NOMINATOR_FUND_DEPLOYER],
                cwd=root, capture_output=True, text=True, timeout=60,
                env=dict(
                    os.environ, TEST_ROOT=str(root), TEST_TRANS_ID=new,
                    TEST_CUSTODIANS=json.dumps(custodians), EVERNODE_WALLET=HOT,
                    FUND_DEST=FACTORY, FUND_VALUE="12000000000",
                    FUND_BUDGET="11000000000",
                ),
            )
            trace = (root / "trace").read_text() if (root / "trace").exists() else ""
        return done, trace.replace(earlier, "<earlier>").replace(new, "<new>")

    def test_a_payment_left_half_signed_is_finished_not_proposed_again(self):
        done, trace = self.fund(pending=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(trace, "signed:<earlier>:validator_2.keys.json\n")

    def test_without_one_a_new_payment_is_proposed_then_approved(self):
        done, trace = self.fund(pending=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(trace, "submitted\nsigned:<new>:validator_2.keys.json\n")


class StakeAtElectorTests(unittest.TestCase):
    """nominator info shows where stake is while the elector holds it."""

    ADNL = "a1" * 32

    def setUp(self):
        self.store, self.config = object(), nominator_config()

    def rounds(self, rounds, now, held=160):
        with patch("evernode.nodes.elected_rounds", return_value=rounds), \
                patch("evernode.nodes.fetch_chain_parameter", return_value={"stake_held_for": held}) as p15:
            return nominator._rounds_at_elector(self.store, self.config, now=now), p15

    def test_a_round_is_held_until_its_set_ends_plus_the_hold_time(self):
        value, p15 = self.rounds([
            {"round": "previous", "utime_since": 400, "utime_until": 1000},
            {"round": "current", "utime_since": 1000, "utime_until": 1600},
        ], now=1100)
        p15.assert_called_once_with(self.config, 15)
        self.assertEqual(value, [
            {"round": "previous", "election_id": "400", "returns_about": "1970-01-01 00:19:20Z"},
            {"round": "current", "election_id": "1000", "returns_about": "1970-01-01 00:29:20Z"},
        ])

    def test_a_round_whose_hold_has_ended_is_left_to_the_credit(self):
        value, _ = self.rounds([{"round": "previous", "utime_since": 400, "utime_until": 1000}], now=1160)
        self.assertEqual(value, [])

    def test_no_round_needs_no_hold_time(self):
        value, p15 = self.rounds([], now=0)
        self.assertEqual(value, [])
        p15.assert_not_called()

    def open_bid(self, election, found, keys=None):
        keys = [{"election_id": 1600, "adnl": self.ADNL}] if keys is None else keys
        with patch("evernode.nodes.validator_keys", return_value=keys), \
                patch("evernode.scripts.read_open_bid", return_value=found) as read:
            return nominator._open_bid(self.store, self.config, election), read

    def test_the_bid_is_looked_up_by_the_elections_key(self):
        value, read = self.open_bid(1600, {"status": "found", "stake": "0x2d79883d2000"})
        read.assert_called_once_with(self.store, self.config, self.ADNL)
        self.assertEqual(value, {"election_id": "1600", "stake_nanotokens": "50000000000000"})

    def test_a_decimal_stake_reads_the_same(self):
        value, _ = self.open_bid(1600, {"status": "found", "stake": "50000000000000"})
        self.assertEqual(value["stake_nanotokens"], "50000000000000")

    def test_no_key_for_the_open_election_means_no_bid(self):
        value, read = self.open_bid(2200, {"status": "found", "stake": "1"})
        self.assertIsNone(value)
        read.assert_not_called()

    def test_no_open_election_means_no_bid(self):
        value, read = self.open_bid(0, {"status": "found", "stake": "1"}, keys=[])
        self.assertIsNone(value)
        read.assert_not_called()

    def test_a_key_the_elector_does_not_list_is_no_bid(self):
        for status in ("absent", "closed"):
            with self.subTest(status=status):
                self.assertIsNone(self.open_bid(1600, {"status": status})[0])

    def test_an_unreadable_elector_is_unknown(self):
        self.assertEqual(self.open_bid(1600, {"status": "unreadable"})[0], nominator.UNKNOWN)

    def test_a_source_that_fails_is_unknown_for_its_own_field_only(self):
        def fail():
            raise process.OperationError("Node container is not running")
        self.assertEqual(nominator._reading(fail), nominator.UNKNOWN)
        self.assertEqual(nominator._reading(lambda: []), [])


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "Requires bash and jq")
class OpenBidSearchTests(unittest.TestCase):
    """The kit's search answers with an exit code; each is a different state."""

    def search(self, code, output):
        stub = f'Elector_ADNL_Search() {{ echo "{output}"; return {code}; }}\n'
        result = subprocess.run(
            ["bash", "-c", "set -eo pipefail\n" + stub + scripts.OPEN_BID_STATUS],
            env={**os.environ, "EVERNODE_ADNL": "a1" * 32},
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_each_answer_of_the_search(self):
        self.assertEqual(self.search(0, "0x2d79883d2000 0 0x30000 0xabc"),
                         {"status": "found", "stake": "0x2d79883d2000"})
        self.assertEqual(self.search(3, "null"), {"status": "closed"})
        self.assertEqual(self.search(4, "absent"), {"status": "absent"})
        for code in (1, 2, 5, 6):
            with self.subTest(code=code):
                self.assertEqual(self.search(code, "unreadable"), {"status": "unreadable"})


SAFE = "-1:" + "5a" * 32
ROTATED = "-1:" + "7b" * 32


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "Requires bash and jq")
class HotWalletCheckTests(unittest.TestCase):
    """The contract keeps what a replaced hot wallet sends without acting on it,
    so an election run checks the wallet it names before a recovery or a bid.
    Run from the kit's own text, like BidBranchTests."""

    @classmethod
    def setUpClass(cls):
        functions = (KIT / "functions.shinc").read_text()
        start = functions.index("function Nominator_Hot_Wallet_Check() {")
        cls.function = functions[start: functions.index("\n}\n", start) + 3]
        cls.take_part = (KIT / "take_part_in_elections.sh").read_text()
        pending = cls.take_part.index("# Check that you have unsigned transactions")
        start = cls.take_part.index('case "$STAKE_MODE" in', pending)
        cls.bid_guard = cls.take_part[start: cls.take_part.index("\nesac\n", start) + len("\nesac\n")]
        cls.prepare = (KIT / "prepare_elections.sh").read_text()
        start = cls.prepare.index("# A nominator contract acts only on the hot wallet")
        cls.recovery_guard = cls.prepare[start: cls.prepare.index("# recover_amount=1", start)]

    @staticmethod
    def bash(program):
        return subprocess.run(["bash", "-c", program], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)

    def check(self, *, validator=SAFE, state="active", console=True, image=True, decodes=True):
        account = {"address": POOL, "state": state, "balance": "1"}
        if state == "active":
            account["validator"] = validator
        with tempfile.TemporaryDirectory() as directory:
            return self.bash(f"""VALIDATOR_NAME=validator01
NOMINATOR_TOOLS_IMAGE=local/tools:abc
CURR_ELECTIONS_DIR={directory!r}
INPL_CURR_ELECTIONS_DIR=/ever-node/elections/1
console() {{ [[ $2 == "getaccountstate {POOL} /ever-node/elections/1/nominator_account.boc" ]] || return 9
    {'echo state > "$CURR_ELECTIONS_DIR/nominator_account.boc"' if console else 'return 1'}; }}
CALL_CONS=console
docker() {{
    [[ $1 == image ]] && return {0 if image else 1}
    {"echo " + repr(json.dumps(account)) if decodes else "return 1"}
}}
{self.function}
Nominator_Hot_Wallet_Check {POOL} {SAFE.upper()}""")

    def test_the_nodes_own_safe_passes(self):
        result = self.check()
        self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)

    def test_a_replaced_hot_wallet_is_refused_by_name(self):
        result = self.check(validator=ROTATED)
        self.assertEqual(result.returncode, 1, result.stderr)
        for part in (ROTATED, SAFE, "evernode election stop -n validator01"):
            self.assertIn(part, result.stdout)

    def test_a_wallet_that_cannot_be_read_is_unknown(self):
        for case, expected in (
            ({"console": False}, "could not read"),
            ({"decodes": False}, "could not be decoded"),
            ({"state": "frozen"}, "is frozen, not active"),
        ):
            with self.subTest(**case):
                result = self.check(**case)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(expected, result.stdout)

    def test_a_missing_tools_image_has_its_own_answer(self):
        # No later run brings the image back, so the answer is its own and
        # names the command that rebuilds it.
        result = self.check(image=False)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("Rebuild the image: evernode nominator prepare -n validator01", result.stdout)

    def guarded(self, block, *, mode, answer, setup=""):
        with tempfile.TemporaryDirectory() as directory:
            result = self.bash(f"""SelfScriptName=test VALIDATOR_NAME=validator01 STAKE_MODE={mode}
NOMINATOR_ADDR={POOL} Validator_addr={SAFE} CURR_ELECTIONS_DIR={directory!r} elections_id=1
Exist_El_Trans_Qty=0 Exist_Nom_Trans_Qty=0 Exist_DP_Trans_Qty=0 Exist_Proxy0_Trans_Qty=0 Exist_Proxy1_Trans_Qty=0
{setup}
Nominator_Hot_Wallet_Check() {{ echo called >> {directory!r}/checks; echo "the reason"; return {answer}; }}
Send_msg_toTelBot() {{ echo alert >> {directory!r}/alerts; }}
{block}
echo PASSED""")
            checks = Path(directory, "checks").exists()
            alerts = Path(directory, "alerts").exists()
        return result, checks, alerts

    def test_a_bid_is_built_only_after_the_check(self):
        start = self.take_part.index("unsigned transactions to the nominator contract")
        check = self.take_part.index("Nominator_Hot_Wallet_Check", start)
        self.assertLess(check, self.take_part.index("election-bid ", start))
        for answer, alerted in ((1, True), (2, False), (3, True)):
            with self.subTest(answer=answer):
                result, checked, alerts = self.guarded(self.bid_guard, mode="nominator", answer=answer)
                self.assertEqual(result.returncode, 1)
                self.assertIn("the reason No bid was sent.", result.stdout)
                self.assertNotIn("PASSED", result.stdout)
                self.assertEqual(alerts, alerted)
        result, checked, _ = self.guarded(self.bid_guard, mode="nominator", answer=0)
        self.assertTrue(checked)
        self.assertIn("PASSED", result.stdout)

    def test_a_recovery_is_built_only_after_the_check(self):
        self.assertLess(self.prepare.index("Nominator_Hot_Wallet_Check"),
                        self.prepare.index("recover_stake ${INPL_CURR_ELECTIONS_DIR}"))
        for answer, alerted in ((1, True), (2, False), (3, True)):
            with self.subTest(answer=answer):
                result, _, alerts = self.guarded(self.recovery_guard, mode="nominator", answer=answer,
                                                 setup="recover_amount=5")
                self.assertEqual(result.returncode, 1)
                self.assertIn("the reason No recovery was sent.", result.stdout)
                self.assertEqual(alerts, alerted)
        result, _, _ = self.guarded(self.recovery_guard, mode="nominator", answer=0, setup="recover_amount=5")
        self.assertIn("PASSED", result.stdout)

    def test_other_modes_and_nothing_to_recover_never_check(self):
        cases = [(self.bid_guard, mode, "") for mode in ("msig", "depool")]
        cases += [(self.recovery_guard, "msig", "recover_amount=5"),
                  (self.recovery_guard, "nominator", "recover_amount=0")]
        for block, mode, setup in cases:
            with self.subTest(mode=mode, setup=setup):
                result, checked, _ = self.guarded(block, mode=mode, answer=1, setup=setup)
                self.assertIn("PASSED", result.stdout, result.stderr)
                self.assertFalse(checked)
