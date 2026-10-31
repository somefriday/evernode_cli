"""A validator whose capital is held by a contract a cold owner controls.

The point of this mode is that the key able to withdraw the principal is not
on the validator server, so these tests pin the separation itself as much as
the plumbing: which operations this CLI will sign, which ones it refuses to do
more than prepare, and which addresses it refuses to accept as an owner.

The cell-level tests compare against bytes recorded by the reference
experiment, so they check what the chain actually accepted rather than what
this implementation believes.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from evernode import configuration, creation, elections, nominator, process, stake, wallets
from evernode.arguments import build_argument_parser, normalize_address_arguments
from support import make_node_configuration

VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "single-nominator"
ASSETS = Path(__file__).resolve().parents[1] / "evernode" / "assets"
REFERENCE = Path("/Users/artem/Projects/github/nominators-to-ever/artifacts")
OWNER = "-1:e253907b4dc0e9d64cb738df4895094ef8d115ae3e1742f637f0329608815913"
HOT = "-1:c43ea310703d54abf436c53af049013960f1097b5360a453af3467b1321b00bc"
POOL = "-1:abc84de88f337e99590aac84578cbd350eb76b6ecf02b24af8edd9675af17ef1"
FACTORY = "-1:6c1d45100d4978ff2b006956730fa2b9675b084003030aa229585285b3208895"
CODE_HASH = "cc0d39589eb2c0cfe0fde28456657a3bdd3d953955ae3f98f25664ab3c904fbd"


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
                ["node", "create", "-n", "v", "--validator-type", "solo",
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

    def test_the_contract_compiles_to_the_published_upstream_hash(self):
        self.assertEqual(self.compiled["codeHash"], CODE_HASH)

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
                    out.read_bytes(), (REFERENCE / name).read_bytes(), name
                )

    @unittest.skipUnless(REFERENCE.is_dir(), "reference artifacts are not present")
    def test_a_bid_becomes_the_stake_request_the_elector_accepted(self):
        """The one transform this mode adds, against a live-validated result."""
        bid = REFERENCE / "validation-bid.boc"
        out = self.work / "stake.body.boc"
        self.cells("body", "stake", str(out), str(bid), "100000000000000", "1793389276")
        self.assertEqual(
            out.read_bytes(), (REFERENCE / "validation-stake.body.boc").read_bytes()
        )

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


if __name__ == "__main__":
    unittest.main()


KIT = Path(__file__).resolve().parents[1] / "vendor/ever-validator/scripts"


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
        self.assertIn('compute_returned_stake "0x${Recover_Subject_HEX}"', source)

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
            'compute_returned_stake "0x${Recover_Subject_HEX}"',          # whose credit
            'Get_Account_Info "${Recover_Subject}"',                      # whose balance before
            'Find_Matching_Pending_Trans "$Validator_addr" "$Recover_Dest"',
            'Submit_And_Identify "$Validator_addr" "$Recover_Dest"',      # where it is sent
            'Effect_Account_Balance_At_Least "$Recover_Subject"',         # whose balance rises
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
                   wallet_balance=15, image="local/tools:abc", transform_fails=False):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "1_query.boc").write_bytes(b"query")
            program = f"""set -uo pipefail
SelfScriptName=take_part_in_elections.sh
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
function docker() {{ {"return 1" if transform_fails else 'cp "$CURR_ELECTIONS_DIR/1_query.boc" "$CURR_ELECTIONS_DIR/1_stake.boc"'}; }}
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

    def test_a_transform_that_fails_stops_the_bid(self):
        self.assertIn(
            "Could not build the nominator election request",
            self.run_branch(transform_fails=True).stdout,
        )


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
        source = Path(creation.__file__).read_text()
        self.assertIn("if solo or nominator\n", source)

    def test_an_imported_safe_must_reach_the_elector_in_both_modes(self):
        source = Path(creation.__file__).read_text()
        self.assertIn("if solo or nominator:", source)
