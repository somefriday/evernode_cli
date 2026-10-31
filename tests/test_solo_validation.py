"""A validator that stakes from its own Safe, with no DePool.

The reference scripts call this mode 'msig'. The elector accepts a stake only
from the masterchain, and every DePool notion - proxies, assurance, reward
fraction - is absent, so these tests pin the choices that follow from that.
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from evernode import configuration, process, stake, wallets
from evernode.arguments import build_argument_parser
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
        scripts = self.root / "scripts"
        scripts.mkdir(parents=True)
        # The real env.sh, because writing a setting it does not declare is
        # refused: that is how an unsupported orchestration source is caught.
        vendor = Path(__file__).resolve().parents[1] / "vendor/ever-validator/scripts"
        shutil.copyfile(vendor / "env.sh", scripts / "env.sh")

        class Store:
            def node_directory(inner, name):
                return self.root

        self.store = Store()

    def set(self, **keywords):
        return stake.set_stake(self.store, dict(self.config), **keywords)

    def test_a_recorded_stake_reaches_the_reference_environment(self):
        config, result = self.set(amount=100000)
        self.assertEqual(config["settings"]["MSIG_FIX_STAKE"], 100000)
        self.assertEqual(result["stake_tokens"], 100000)
        self.assertIn("MSIG_FIX_STAKE=100000", (self.root / "scripts/env.sh").read_text())

    def test_recording_a_stake_is_what_readies_a_new_solo_validator(self):
        config, result = self.set(amount=100000)
        self.assertEqual(config["setup_stage"], "ready-for-elections")
        self.assertIn("election start", result["next"])

    def test_the_whole_balance_is_the_reference_scripts_own_mode(self):
        config, result = self.set(all_balance=True)
        self.assertEqual(config["settings"]["MSIG_FIX_STAKE"], 0)
        self.assertEqual(result["stake_tokens"], "all-balance")

    def test_a_stake_of_zero_or_less_is_refused(self):
        for amount in (0, -5):
            with self.subTest(amount=amount):
                with self.assertRaises(process.OperationError):
                    self.set(amount=amount)

    def test_the_two_rounds_in_flight_cost_is_stated_not_implied(self):
        _, result = self.set(amount=100000)
        self.assertIn("twice", result["note"])

    def test_the_parser_refuses_a_stake_that_is_both_fixed_and_whole_balance(self):
        parser = build_argument_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["stake", "set", "-n", "v1", "--amount", "10", "--all-balance"]
            )


if __name__ == "__main__":
    unittest.main()
