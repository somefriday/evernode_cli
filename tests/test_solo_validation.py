"""A validator that stakes from its own Safe, with no DePool.

The reference scripts call this mode 'msig'. The elector accepts a stake only
from the masterchain, and every DePool notion - proxies, assurance, reward
fraction - is absent, so these tests pin the choices that follow from that.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from evernode import configuration, elections, process, stake, storage, wallets
from evernode.arguments import build_argument_parser, normalize_address_arguments
from support import make_node_configuration


def solo_config(**changes):
    value = make_node_configuration("validator01", validator_type="solo")
    value.update(changes)
    return value


class ValidatorTypeTests(unittest.TestCase):
    def test_a_node_saved_before_solo_existed_stakes_through_a_depool(self):
        self.assertEqual(configuration.validator_type({}), "depool")

    def test_an_unknown_type_is_refused(self):
        with self.assertRaises(process.OperationError):
            configuration.validator_type({"validator_type": "lottery"})

    def test_the_reference_scripts_are_told_which_mode_to_run(self):
        cases = [("depool", "depool"), ("solo", "msig")]
        for validator_type, expected in cases:
            with self.subTest(validator_type=validator_type):
                config = make_node_configuration(
                    "validator01", validator_type=validator_type
                )
                environment = configuration.build_node_environment(config)
                self.assertEqual(environment["STAKE_MODE"], expected)

    def test_settings_saved_before_solo_existed_keep_loading(self):
        """An older node.json has none of the solo keys and must still load."""
        older = {
            "NODE_WC": 0,
            "CRONTAB_INTERVAL": 10,
            "DePool_TYPE": "EverX",
            "ValidatorAssuranceT": 50000,
            "MinStakeT": 10,
            "ParticipantRewardFraction": 65,
            "BalanceThresholdT": 20,
        }
        config = make_node_configuration("validator01", settings=dict(older))
        configuration.validate_saved_node_config(config, "validator01")
        environment = configuration.build_node_environment(config)
        self.assertEqual(environment["MSIG_FIX_STAKE"], 0)
        self.assertEqual(environment["MAX_FACTOR"], 3)

    def test_an_unknown_setting_is_still_refused(self):
        config = make_node_configuration(
            "validator01", settings={"NODE_WC": 0, "Nonsense": 1}
        )
        with self.assertRaises(process.OperationError):
            configuration.validate_saved_node_config(config, "validator01")


class AddressArgumentTests(unittest.TestCase):
    """A masterchain address begins with a minus, which argparse reads as a flag.

    Every solo validator's Safe is in workchain -1, so this is not an edge
    case: it is the only form a solo import can take.
    """

    def normalize(self, *argv):
        return build_argument_parser().parse_args(
            normalize_address_arguments(list(argv))
        )

    def test_a_masterchain_wallet_can_be_written_after_a_space(self):
        parsed = self.normalize(
            "node", "create", "-n", "v", "--validator-type", "solo",
            "--import-wallet", "--wallet-address", "-1:" + "d" * 64,
        )
        self.assertEqual(parsed.wallet_address, "-1:" + "d" * 64)

    def test_the_joined_form_still_works(self):
        parsed = self.normalize(
            "node", "create", "-n", "v", "--wallet-address=-1:" + "d" * 64
        )
        self.assertEqual(parsed.wallet_address, "-1:" + "d" * 64)

    def test_a_basechain_address_is_untouched(self):
        parsed = self.normalize(
            "node", "create", "-n", "v", "--wallet-address", "0:" + "d" * 64
        )
        self.assertEqual(parsed.wallet_address, "0:" + "d" * 64)

    def test_a_masterchain_depool_address_is_accepted_too(self):
        parsed = self.normalize(
            "node", "create", "-n", "v", "--depool-address", "-1:" + "b" * 64
        )
        self.assertEqual(parsed.depool_address, "-1:" + "b" * 64)

    def test_something_that_is_not_an_address_keeps_argparses_own_error(self):
        for following in ("--import-wallet", "-1:zz", "-n"):
            with self.subTest(following=following):
                self.assertEqual(
                    normalize_address_arguments(["--wallet-address", following]),
                    ["--wallet-address", following],
                )

    def test_unrelated_arguments_are_left_alone(self):
        argv = ["node", "stop", "-n", "x", "--force", "--timeout", "30"]
        self.assertEqual(normalize_address_arguments(argv), argv)

    def test_a_trailing_option_with_no_value_is_left_to_argparse(self):
        self.assertEqual(
            normalize_address_arguments(["--wallet-address"]), ["--wallet-address"]
        )


class ImportedProfileTests(unittest.TestCase):
    """What an import has to name depends on whether there is a DePool."""

    def profile(self, **changes):
        value = {
            "mode": "import",
            "custodians": 3,
            "required_signatures": 2,
            "wallet_address": "-1:" + "a" * 64,
            "depool_address": None,
        }
        value.update(changes)
        return value

    def test_a_solo_import_is_complete_with_only_its_safe(self):
        wallets.validate_wallet_profile(self.profile(), validator_type="solo")

    def test_a_depool_import_still_requires_its_depool(self):
        with self.assertRaises(process.OperationError):
            wallets.validate_wallet_profile(self.profile())

    def test_a_solo_node_refuses_a_depool_address(self):
        with self.assertRaises(process.OperationError):
            wallets.validate_wallet_profile(
                self.profile(depool_address="0:" + "b" * 64), validator_type="solo"
            )

    def test_any_import_still_requires_its_wallet_address(self):
        for validator_type in ("solo", "depool"):
            with self.subTest(validator_type=validator_type):
                with self.assertRaises(process.OperationError):
                    wallets.validate_wallet_profile(
                        self.profile(wallet_address=None),
                        validator_type=validator_type,
                    )

    def test_a_saved_solo_import_loads_through_the_type_aware_check(self):
        config = solo_config(wallet=self.profile(), setup_stage="imported")
        self.assertEqual(
            wallets.node_wallet_profile(config)["wallet_address"],
            "-1:" + "a" * 64,
        )


class WalletWorkchainTests(unittest.TestCase):
    """The elector takes a stake only from the masterchain."""

    def test_a_solo_wallet_is_in_the_masterchain_whatever_the_node_does(self):
        for node_workchain in (-1, 0):
            with self.subTest(node_workchain=node_workchain):
                config = solo_config(
                    settings=dict(
                        configuration.DEFAULT_ENVIRONMENT, NODE_WC=node_workchain
                    )
                )
                self.assertEqual(wallets.wallet_workchain(config), -1)

    def test_a_depool_wallet_follows_the_node(self):
        for workchain in (-1, 0):
            with self.subTest(workchain=workchain):
                config = make_node_configuration(
                    "validator01",
                    validator_type="depool",
                    settings=dict(
                        configuration.DEFAULT_ENVIRONMENT, NODE_WC=workchain
                    ),
                )
                self.assertEqual(wallets.wallet_workchain(config), workchain)


class CommandSeparationTests(unittest.TestCase):
    """DePool commands and stake commands apply to different validators."""

    def test_every_depool_operation_refuses_a_solo_validator(self):
        operations = [
            ("depool_prepare", {}),
            ("depool_deploy", {}),
            ("depool_stake_initial", {}),
            ("depool_info", {}),
            ("depool_verify", {}),
            ("depool_stake", {"amount": 10}),
            ("depool_replenish", {"amount": 10}),
            ("depool_confirm", {"transaction_id": "1"}),
            ("depool_withdraw", {"amount": 10}),
        ]
        config = solo_config(wallet={
            "mode": "new", "custodians": 3, "required_signatures": 2,
            "wallet_address": "-1:" + "a" * 64, "depool_address": None,
        })
        for name, keywords in operations:
            with self.subTest(operation=name):
                with self.assertRaises(process.OperationError) as caught:
                    getattr(wallets, name)(None, config, **keywords)
                message = str(caught.exception)
                self.assertIn("solo validator", message)
                self.assertIn("evernode stake", message)

    def test_a_stake_command_refuses_a_depool_validator(self):
        config = make_node_configuration("validator01", validator_type="depool",
                                         wallet={"mode": "new", "custodians": 3,
                                                 "required_signatures": 2,
                                                 "wallet_address": "0:" + "a" * 64,
                                                 "depool_address": "0:" + "b" * 64})
        with self.assertRaises(process.OperationError) as caught:
            stake.describe_stake(None, config)
        self.assertIn("depool validator", str(caught.exception))
        self.assertIn("evernode depool", str(caught.exception))


class ConfirmationTests(unittest.TestCase):
    """A half-signed transaction is a Safe problem, not a DePool problem."""

    def profile(self):
        return {
            "mode": "new",
            "custodians": 3,
            "required_signatures": 2,
            "wallet_address": "-1:" + "a" * 64,
            "depool_address": None,
        }

    def test_a_solo_validator_can_finish_its_own_half_signed_transaction(self):
        config = solo_config(wallet=self.profile(), setup_stage="ready-for-elections")
        with patch("evernode.wallets.scripts.execute_validator_script") as execute:
            _, result = wallets.stake_confirm(None, config, transaction_id="123")
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[2], "Sign_Trans.sh")
        self.assertEqual(result["transaction_id"], "123")
        self.assertIn("stake status", result["next"])

    def test_confirmation_is_refused_for_the_other_validator_type(self):
        solo = solo_config(wallet=self.profile(), setup_stage="ready-for-elections")
        depool = make_node_configuration(
            "validator01",
            validator_type="depool",
            wallet=dict(self.profile(), wallet_address="0:" + "a" * 64,
                        depool_address="0:" + "b" * 64),
            setup_stage="ready-for-elections",
        )
        with self.assertRaises(process.OperationError):
            wallets.stake_confirm(None, depool, transaction_id="123")
        with self.assertRaises(process.OperationError):
            wallets.depool_confirm(None, solo, transaction_id="123")

    def test_an_id_that_is_not_a_number_is_refused(self):
        config = solo_config(wallet=self.profile(), setup_stage="ready-for-elections")
        for value in ("abc", "1 2", "", "-5"):
            with self.subTest(value=value):
                with self.assertRaises(process.OperationError):
                    wallets.stake_confirm(None, config, transaction_id=value)

    def test_confirmation_needs_a_finished_setup(self):
        config = solo_config(wallet=self.profile(), setup_stage="wallet-deployed")
        with self.assertRaises(process.OperationError) as caught:
            wallets.stake_confirm(None, config, transaction_id="123")
        self.assertIn("finished setup", str(caught.exception))


class StageTests(unittest.TestCase):
    """A solo validator has no DePool stages and needs a stake on record."""

    def profile(self, mode="new"):
        return {
            "mode": mode,
            "custodians": 3,
            "required_signatures": 2,
            "wallet_address": "-1:" + "a" * 64,
            "depool_address": None,
        }

    def test_a_deployed_solo_wallet_is_asked_for_its_stake(self):
        config = solo_config(wallet=self.profile(), setup_stage="wallet-deployed")
        self.assertIn("stake set", wallets.next_setup_command(config))

    def test_a_deployed_depool_wallet_is_asked_to_prepare_a_depool(self):
        config = make_node_configuration(
            "validator01",
            validator_type="depool",
            wallet=dict(self.profile(), depool_address=None),
            setup_stage="wallet-deployed",
        )
        self.assertIn("depool prepare", wallets.next_setup_command(config))

    def test_an_imported_solo_validator_never_verifies_a_depool(self):
        for stage in ("wallet-recovered", "imported"):
            with self.subTest(stage=stage):
                config = solo_config(
                    wallet=self.profile("import"), setup_stage=stage
                )
                command = wallets.next_setup_command(config) or ""
                self.assertNotIn("depool", command)


class SetStakeTests(unittest.TestCase):
    """Recording what a solo validator stakes in each election.

    A mode is arithmetic done once against the balance and the chain's own
    limits, written down as a fixed number. The scripts never re-evaluate it,
    so these tests pin the arithmetic, the limits that refuse a number, and
    the cost a number implies.
    """

    # 1 000 and 10 000 000 tokens, as the local reference network reports them.
    LIMITS = {
        "min_stake_dec": "1000000000000",
        "max_stake_dec": "10000000000000000",
        "max_stake_factor": 196608,
    }
    TIMINGS = {
        "validators_elected_for": 300,
        "elections_start_before": 200,
        "elections_end_before": 80,
        "stake_held_for": 80,
    }

    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o077))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = solo_config(
            wallet={"mode": "new", "custodians": 3, "required_signatures": 2,
                    "wallet_address": "-1:" + "a" * 64, "depool_address": None},
            setup_stage="wallet-deployed",
        )
        scripts_dir = self.root / "scripts"
        scripts_dir.mkdir(parents=True)
        # The real env.sh, because writing a setting it does not declare is
        # refused: that is how an unsupported orchestration source is caught.
        vendor = Path(__file__).resolve().parents[1] / "vendor/ever-validator/scripts"
        shutil.copyfile(vendor / "env.sh", scripts_dir / "env.sh")

        class Store:
            def node_directory(inner, name):
                return self.root

        self.store = Store()
        self.observed = {
            "wallet_balance_nanotokens": str(250_000 * stake.NANO),
            "recoverable_nanotokens": "0",
        }
        self.timings = dict(self.TIMINGS)

    def set(self, **keywords):
        def parameter(config, number):
            return dict(self.LIMITS) if number == 17 else dict(self.timings)

        with (
            patch("evernode.scripts.read_solo_stake_state",
                  side_effect=lambda *a, **k: dict(self.observed)),
            patch("evernode.nodes.fetch_chain_parameter", side_effect=parameter),
        ):
            return stake.set_stake(self.store, dict(self.config), **keywords)

    def test_a_recorded_stake_reaches_the_reference_environment(self):
        config, result = self.set(amount=100000)
        self.assertEqual(config["settings"]["MSIG_FIX_STAKE"], 100000)
        self.assertEqual(result["stake_tokens"], 100000)
        self.assertEqual(result["mode"], "exact")
        self.assertIn("MSIG_FIX_STAKE=100000", (self.root / "scripts/env.sh").read_text())

    def test_recording_a_stake_is_what_readies_a_new_solo_validator(self):
        config, result = self.set(amount=100000)
        self.assertEqual(config["setup_stage"], "ready-for-elections")
        self.assertIn("election start", result["next"])

    def test_a_recovered_solo_validator_also_has_to_record_a_stake(self):
        """An imported node must not reach elections with no decided amount."""
        self.config = solo_config(
            wallet={"mode": "import", "custodians": 3, "required_signatures": 2,
                    "wallet_address": "-1:" + "a" * 64, "depool_address": None},
            setup_stage="imported",
        )
        self.assertIn("stake set", wallets.next_setup_command(self.config))
        config, result = self.set(amount=100000)
        self.assertEqual(config["setup_stage"], "ready-for-elections")
        self.assertIn("election start", result["next"])

    def test_balanced_halves_the_spendable_balance(self):
        config, result = self.set(mode="balanced")
        # 250 000 held, 50 reserved for fees, split across two rounds.
        self.assertEqual(result["stake_tokens"], 124975)
        self.assertEqual(config["settings"]["MSIG_FIX_STAKE"], 124975)
        self.assertEqual(result["arithmetic"]["spendable_tokens"], 249950)
        self.assertEqual(result["arithmetic"]["divided_by"], 2)
        self.assertEqual(result["rounds_funded"], 2)
        self.assertEqual(result["funding"], "funds consecutive rounds")

    def test_single_round_takes_all_of_it(self):
        _, result = self.set(mode="single")
        self.assertEqual(result["stake_tokens"], 249950)
        self.assertEqual(result["rounds_funded"], 1)
        self.assertEqual(result["funding"], "funds every other round")

    def test_a_mode_is_written_as_a_number_never_as_the_scripts_balance_mode(self):
        """MSIG_FIX_STAKE=0 is the mode with the hidden threshold."""
        for mode in ("balanced", "single"):
            with self.subTest(mode=mode):
                config, _ = self.set(mode=mode)
                self.assertGreater(config["settings"]["MSIG_FIX_STAKE"], 0)

    def test_money_at_the_elector_counts_and_is_declared(self):
        self.observed = {
            "wallet_balance_nanotokens": str(50_000 * stake.NANO),
            "recoverable_nanotokens": str(100_000 * stake.NANO),
        }
        _, result = self.set(mode="single")
        self.assertEqual(result["arithmetic"]["recoverable_tokens"], 100000)
        self.assertEqual(result["stake_tokens"], 149950)
        self.assertTrue(any("still at the elector" in w for w in result["warnings"]))

    def test_an_amount_that_cannot_fund_two_rounds_says_so(self):
        _, result = self.set(amount=200000)
        self.assertEqual(result["rounds_funded"], 1)
        self.assertTrue(
            any("one round at a time" in w for w in result["warnings"]), result
        )

    def test_an_amount_the_wallet_cannot_fund_at_all_says_so(self):
        _, result = self.set(amount=400000)
        self.assertEqual(result["rounds_funded"], 0)
        self.assertTrue(any("cannot fund one bid" in w for w in result["warnings"]))

    def test_a_stake_below_the_electors_minimum_is_refused(self):
        with self.assertRaises(process.OperationError) as caught:
            self.set(amount=999)
        self.assertIn("1000", str(caught.exception))

    def test_a_named_amount_above_the_electors_maximum_is_refused(self):
        with self.assertRaises(process.OperationError):
            self.set(amount=10_000_001)

    def test_a_mode_caps_at_the_electors_maximum_and_says_so(self):
        """Arithmetic should cap visibly, not leave the operator with no answer."""
        self.observed = {
            "wallet_balance_nanotokens": str(30_000_000 * stake.NANO),
            "recoverable_nanotokens": "0",
        }
        _, result = self.set(mode="single")
        self.assertEqual(result["stake_tokens"], 10_000_000)
        self.assertTrue(any("Capped at" in w for w in result["warnings"]))

    def test_a_wallet_too_small_for_any_stake_says_how_small(self):
        self.observed = {
            "wallet_balance_nanotokens": str(600 * stake.NANO),
            "recoverable_nanotokens": "0",
        }
        with self.assertRaises(process.OperationError) as caught:
            self.set(mode="single")
        self.assertIn("550 tokens spendable", str(caught.exception))

    def test_balanced_is_refused_when_half_is_below_the_minimum(self):
        self.observed = {
            "wallet_balance_nanotokens": str(1500 * stake.NANO),
            "recoverable_nanotokens": "0",
        }
        with self.assertRaises(process.OperationError) as caught:
            self.set(mode="balanced")
        self.assertIn("--mode single", str(caught.exception))

    def test_an_empty_wallet_is_refused_by_name_not_by_arithmetic(self):
        self.observed = {
            "wallet_balance_nanotokens": "0",
            "recoverable_nanotokens": "0",
        }
        with self.assertRaises(process.OperationError) as caught:
            self.set(mode="single")
        self.assertIn("fund it first", str(caught.exception))

    def test_a_stake_of_zero_or_less_is_refused(self):
        for amount in (0, -5):
            with self.subTest(amount=amount):
                with self.assertRaises(process.OperationError):
                    self.set(amount=amount)

    def test_an_amount_and_a_mode_together_are_refused(self):
        with self.assertRaises(process.OperationError):
            self.set(amount=100, mode="single")

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(process.OperationError):
            self.set(mode="martingale")

    def test_the_chain_decides_whether_two_stakes_are_enough(self):
        _, result = self.set(mode="balanced")
        self.assertTrue(result["chain"]["stake_returns_within_next_election"])
        # A network that releases a stake after the next election closes
        # cannot fund consecutive rounds from two stakes alone.
        self.timings = dict(self.TIMINGS, stake_held_for=280)
        _, result = self.set(mode="balanced")
        self.assertFalse(result["chain"]["stake_returns_within_next_election"])
        self.assertTrue(any("released too late" in w for w in result["warnings"]))

    def test_the_truncation_factor_is_reported_as_a_readable_multiple(self):
        """196608 is 16.16 fixed point for 3.0 and reads as nothing."""
        _, result = self.set(mode="balanced")
        self.assertEqual(result["chain"]["max_stake_multiple"], 3.0)

    def test_limits_are_read_from_the_decimal_form_only(self):
        """The hex companion field is length-prefixed, not a plain integer."""
        self.LIMITS = {"min_stake": "09e8d4a51000", "max_stake": "0d2386f26fc10000"}
        with self.assertRaises(process.OperationError) as caught:
            self.set(amount=100000)
        self.assertIn("min_stake_dec", str(caught.exception))

    def test_a_solo_node_cannot_bid_before_a_stake_is_decided(self):
        with self.assertRaises(process.OperationError) as caught:
            stake.require_recorded_stake(solo_config(settings={"MSIG_FIX_STAKE": 0}))
        self.assertIn("stake set", str(caught.exception))
        stake.require_recorded_stake(solo_config(settings={"MSIG_FIX_STAKE": 100}))
        # A DePool validator has no such setting and is not subject to it.
        stake.require_recorded_stake(make_node_configuration("validator01"))

    def test_the_parser_refuses_two_ways_of_naming_one_stake(self):
        parser = build_argument_parser()
        for extra in (["--all-balance"], ["--mode", "single"]):
            with self.subTest(extra=extra):
                with self.assertRaises(SystemExit):
                    parser.parse_args(
                        ["stake", "set", "-n", "v1", "--amount", "10", *extra]
                    )

    def test_the_deprecated_flag_still_names_a_single_round(self):
        parser = build_argument_parser()
        parsed = parser.parse_args(["stake", "set", "-n", "v1", "--all-balance"])
        self.assertTrue(parsed.all_balance)
        self.assertIsNone(parsed.mode)


class ElectionSequenceTests(unittest.TestCase):
    """The scheduled cycle, which is a different sequence for each type.

    A DePool bid is placed into the DePool that preparation ticks, so there
    preparation failing has to stop the cycle. A solo bid goes to the elector,
    so nothing it needs comes from preparation, and the election open right
    now is the one thing the node cannot come back to later.
    """

    def setUp(self):
        self.mask = os.umask(0o077)
        self.addCleanup(os.umask, self.mask)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = storage.NodeStateStore(self.tmp.name)
        guard = patch.object(storage.NodeStateStore, "validate_state_root")
        guard.start()
        self.addCleanup(guard.stop)
        self.cron = Path(self.tmp.name) / "cron"
        path = patch(
            "evernode.elections.node_election_cron_path", return_value=self.cron
        )
        path.start()
        self.addCleanup(path.stop)

    def install(self, validator_type):
        config = make_node_configuration(
            "validator01", validator_type=validator_type, setup_stage="ready-for-elections"
        )
        directory = self.store.node_directory("validator01")
        for part in ("scripts", "logs/validator", "elections"):
            (directory / part).mkdir(parents=True, exist_ok=True)
        elections.enable_node_election_schedule(self.store, config)
        sequence = elections.election_sequence_path(self.store, config)
        return config, directory, sequence

    def test_each_type_gets_its_own_sequence_file(self):
        solo, _, solo_sequence = self.install("solo")
        self.assertEqual(solo_sequence.name, "run-solo-election-sequence.sh")
        self.cron.unlink()
        depool, _, depool_sequence = self.install("depool")
        self.assertEqual(depool_sequence.name, "run-election-sequence.sh")
        self.assertNotEqual(solo_sequence, depool_sequence)

    def test_the_installed_schedule_names_the_sequence_it_runs(self):
        config, _, sequence = self.install("solo")
        status = elections.election_schedule_status(self.store, config)
        self.assertEqual(status["sequence"], str(sequence))

    def test_cron_runs_the_sequence_of_this_nodes_type(self):
        config, directory, sequence = self.install("solo")
        runner = (directory / "run-elections.sh").read_text()
        self.assertIn(str(sequence), runner)
        self.assertNotIn("run-election-sequence.sh", runner)
        self.assertIn(str(directory / "run-elections.sh"), self.cron.read_text())

    def test_both_sequences_prepare_then_bid_and_run_no_inspection(self):
        for validator_type in ("solo", "depool"):
            with self.subTest(validator_type=validator_type):
                _, _, sequence = self.install(validator_type)
                text = sequence.read_text()
                self.assertIn("./prepare_elections.sh", text)
                self.assertIn("./take_part_in_elections.sh", text)
                self.assertNotIn("./part_check.sh", text)
                self.assertEqual(sequence.stat().st_mode & 0o777, 0o700)
                self.cron.unlink()

    def test_a_depool_cycle_still_stops_when_preparation_fails(self):
        _, _, sequence = self.install("depool")
        self.assertIn("set -euo pipefail", sequence.read_text())

    def run_solo_sequence(self, prepare_exit, recover_amount, bid_exit=0, settle=0):
        """Run the generated solo sequence against stub reference scripts."""
        _, directory, sequence = self.install("solo")
        scripts_dir = directory / "scripts"
        (scripts_dir / "prepare_elections.sh").write_text(
            "#!/bin/bash\n"
            f"echo '---INFO: recover_amount = {recover_amount} nanotokens ( x Tokens )'\n"
            f"exit {prepare_exit}\n"
        )
        (scripts_dir / "take_part_in_elections.sh").write_text(
            f"#!/bin/bash\necho BID-RAN\nexit {bid_exit}\n"
        )
        for name in ("prepare_elections.sh", "take_part_in_elections.sh"):
            os.chmod(scripts_dir / name, 0o700)
        finished = subprocess.run(
            ["bash", str(sequence)],
            capture_output=True,
            text=True,
            timeout=120,
            env=dict(os.environ, ELECTION_SETTLE_SECONDS=str(settle)),
        )
        return finished

    def test_a_solo_cycle_bids_whatever_preparation_reported(self):
        # 0 is a clean recovery, 3 a request already pending, 1 a real failure.
        for prepare_exit in (0, 1, 3):
            with self.subTest(prepare_exit=prepare_exit):
                finished = self.run_solo_sequence(prepare_exit, 0)
                self.assertIn("BID-RAN", finished.stdout)
                self.assertEqual(finished.returncode, 0)
                self.cron.unlink()

    def test_a_pending_request_is_reported_as_the_bid_finishing_it(self):
        finished = self.run_solo_sequence(3, 0)
        self.assertIn("pending request to the elector", finished.stdout)

    def test_a_failed_preparation_says_the_bid_does_not_depend_on_it(self):
        finished = self.run_solo_sequence(1, 0)
        self.assertIn("preparation exited 1", finished.stdout)

    def test_the_verdict_of_the_cycle_is_the_verdict_of_the_bid(self):
        self.assertEqual(self.run_solo_sequence(0, 0, bid_exit=7).returncode, 7)

    def test_only_a_submitted_recovery_is_waited_for(self):
        waited = self.run_solo_sequence(0, 100044375278680, settle=1)
        self.assertIn("waiting 1s", waited.stdout)
        self.cron.unlink()
        skipped = self.run_solo_sequence(0, 0, settle=1)
        self.assertNotIn("waiting", skipped.stdout)

    def test_the_preparation_log_is_kept_for_the_operator(self):
        _, directory, _ = self.install("solo")
        self.cron.unlink()
        self.run_solo_sequence(0, 7)
        log = directory / "elections" / "last-preparation.log"
        self.assertIn("recover_amount = 7", log.read_text())


if __name__ == "__main__":
    unittest.main()
