"""The denominator validator type, end to end apart from the chain itself.

test_denominator_isolation.py proves the other types are unchanged. These tests
prove the new type is handled wherever a validator type is branched on, and that
what it adds does what docs/denominator and docs/spec/denominator.md say: its
setup flags, the pool tools (run directly, as test_nominator.py runs the
single-nominator ones), the Oberton links, the commands that move the
validator's funds or retire the pools, and the cycle script's decisions, run
from the script's own text with the chain stubbed.
"""

import base64
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evernode import (  # noqa: E402
    configuration, denominator, elections, nodes, process, setup_actions, stake, storage, wallets,
)
from evernode.arguments import build_argument_parser  # noqa: E402
from evernode.cli import main  # noqa: E402
from support import make_node_configuration  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "evernode" / "vendor" / "ever-validator" / "scripts"
POOL_SOURCE = ROOT / "evernode" / "vendor" / "nominator-pool"
PINNED = json.loads((POOL_SOURCE / "PINNED.json").read_text())
NANO = 10**9

SAFE = "-1:" + "a" * 64
POOL_A = "-1:" + "1" * 64
POOL_B = "-1:" + "2" * 64
STAKER = "0:" + "ab" * 32
SETUP = ["--workchain", "0", "--cron-interval", "10", "--custodians", "1", "--required-signatures", "1"]


def pool_config(name="v-pools", stage="ready-for-elections", bidding=False, **settings):
    """A denominator node as denominator prepare, deploy and verify leave it."""
    return make_node_configuration(
        name,
        phase="console-ready",
        setup_stage=stage,
        validator_type="denominator",
        wallet={"mode": "new", "custodians": 1, "required_signatures": 1,
                "wallet_address": SAFE, "depool_address": None},
        settings={**denominator.DEFAULTS, "DENOMINATOR_ADDRS": f"{POOL_A} {POOL_B}",
                  "DENOMINATOR_TOOLS_IMAGE": "local/evernode-pool-tools:0123456789abcdef", **settings},
        denominator={
            "pools": [
                {"label": "A", "address": POOL_A, "factory": "-1:" + "e" * 64, "salt": 0},
                {"label": "B", "address": POOL_B, "factory": "-1:" + "f" * 64, "salt": 1},
            ],
            "bidding": bidding,
        },
    )


def pool_account(state=0, balance=5000, validator_funds=1000, requests=0, changes=0,
                 change_time=0, held=32768, sent=0, stake_at=0, stakers_funds=0, min_validator=1000):
    """A pool as the pool tool's 'account' reads it; amounts in tokens."""
    return {
        "address": POOL_A, "state": "active", "balance": str(int(balance * NANO)),
        "codeHash": PINNED["codeHash"], "validator": SAFE,
        "pool": {
            "state": state, "nominatorsCount": 0, "stakeAmountSent": str(sent * NANO),
            "validatorAmount": str(validator_funds * NANO),
            "config": {"validator": SAFE, "rewardShare": 4000, "maxNominators": 40,
                       "minValidatorStake": str(min_validator * NANO), "minNominatorStake": str(1000 * NANO)},
            "nominators": [], "nominatorsTotal": str(stakers_funds * NANO), "pendingTotal": "0",
            "withdrawRequests": requests, "stakeAt": stake_at, "validatorSetHash": "0" * 64,
            "validatorSetChanges": changes, "validatorSetChangeTime": change_time,
            "stakeHeldFor": held, "votings": 0,
        },
    }


@contextlib.contextmanager
def node_store():
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


def add_node(store, config):
    directory = store.node_directory(config["name"])
    for part in ("scripts", "logs/validator", "elections"):
        (directory / part).mkdir(parents=True, exist_ok=True)
    shutil.copy(KIT / "env.sh", directory / "scripts" / "env.sh")
    if configuration.has_role(config):
        configuration.write_node_environment(
            directory / "scripts" / "env.sh", configuration.build_node_environment(config)
        )
    store.save_node_config(config)
    return directory


def run_cli(root, *words):
    out, err = io.StringIO(), io.StringIO()
    with (
        patch("evernode.cli.require_root_privileges"),
        patch("sys.stdin.isatty", return_value=False),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        code = main(["--state-dir", root, *words])
    return code, out.getvalue(), err.getvalue()


class TypeCoverageTests(unittest.TestCase):
    """Every function that branches on the validator type handles this one
    itself, and none of them takes it for a DePool validator."""

    def test_the_safe_is_created_in_the_masterchain(self):
        self.assertEqual(wallets.wallet_workchain(pool_config()), -1)

    def test_each_setup_stage_names_its_next_command(self):
        name = "v-pools"
        expected = {
            ("new", "wallet-deployed"): f"evernode denominator prepare -n {name}",
            ("new", "denominator-prepared"): f"evernode denominator deploy -n {name}",
            ("new", "denominator-deployed"): f"evernode denominator verify -n {name}",
            ("new", "denominator-verified"): f"evernode denominator deposit -n {name} --amount TOKENS",
            ("new", "ready-for-elections"): f"evernode election start -n {name}",
            ("import", "imported"): f"evernode denominator prepare -n {name}",
            ("import", "denominator-prepared"): f"evernode denominator deploy -n {name}",
            ("import", "denominator-deployed"): f"evernode denominator verify -n {name}",
            ("import", "denominator-verified"): f"evernode denominator deposit -n {name} --amount TOKENS",
            ("import", "ready-for-elections"): f"evernode election start -n {name}",
        }
        for (mode, stage), command in expected.items():
            config = pool_config(stage=stage)
            config["wallet"]["mode"] = mode
            with self.subTest(mode=mode, stage=stage):
                self.assertEqual(wallets.next_setup_command(config), command)

    def test_the_cron_runs_the_pools_cycle_and_nothing_else(self):
        with node_store() as (store, _):
            for bidding, mode in ((True, "bid"), (False, "maintain")):
                config = pool_config(bidding=bidding)
                directory = add_node(store, config)
                elections.write_election_programs(store, config)
                sequence = elections.election_sequence_path(store, config)
                self.assertEqual(sequence, directory / elections.DENOMINATOR_SEQUENCE_FILE)
                program = sequence.read_text()
                with self.subTest(bidding=bidding):
                    self.assertIn(f"./denominator_cycle.sh cycle {mode}\n", program)
                    self.assertNotIn("prepare_elections.sh", program)
                    self.assertNotIn("take_part_in_elections.sh", program)

    def test_the_stake_commands_take_the_pools_path(self):
        config = pool_config()
        with patch.object(denominator, "stake_status", return_value={"pools": "status"}):
            self.assertEqual(stake.describe_stake(None, config), {"pools": "status"})
        with patch.object(denominator, "maintain", return_value={"pools": "maintained"}):
            self.assertEqual(stake.recover(None, config), {"pools": "maintained"})
        for refused in (lambda: stake.set_stake(None, config, amount=10), lambda: stake.ask_mode(None, config)):
            with self.assertRaisesRegex(process.OperationError, "no stake to set"):
                refused()
        # Nothing to record: a pool stakes everything it holds.
        stake.require_recorded_stake(config)

    def test_stake_set_is_refused_before_anything_is_asked(self):
        config = pool_config()
        with self.assertRaisesRegex(process.OperationError, "no stake to set"):
            setup_actions._confirmation_question(
                type("Args", (), {"group": "stake", "action": "set"})(), config
            )
        question = setup_actions._confirmation_question(
            type("Args", (), {"group": "stake", "action": "recover"})(), config
        )
        self.assertIn("pool maintenance", question)

    def test_its_safe_transactions_are_confirmed_as_stake_transactions(self):
        config = pool_config()
        wallets.require_confirmable(config, "stake", "5")
        with self.assertRaises(process.OperationError):
            wallets.require_confirmable(config, "depool", "5")

    def test_the_pools_are_among_its_own_contracts(self):
        with patch("evernode.nodes.fetch_special_address", side_effect=lambda config, index: f"-1:{index}"):
            contracts = setup_actions._own_contracts(None, pool_config())
        self.assertEqual(contracts[POOL_A], "this node's pool A")
        self.assertEqual(contracts[POOL_B], "this node's pool B")

    def test_env_sh_names_the_pools_on_its_own_nodes_only(self):
        values = configuration.build_node_environment(pool_config())
        self.assertEqual(values["STAKE_MODE"], "denominator")
        self.assertEqual(values["DENOMINATOR_ADDRS"], f"{POOL_A} {POOL_B}")
        self.assertEqual(values["DENOMINATOR_TOOLS_IMAGE"], "local/evernode-pool-tools:0123456789abcdef")
        self.assertEqual(values["DENOMINATOR_OP_FEE"], 2 * NANO)
        solo = make_node_configuration("v-solo", validator_type="solo", wallet=dict(
            pool_config()["wallet"]), settings={"MSIG_FIX_STAKE": 100})
        self.assertFalse([key for key in configuration.build_node_environment(solo) if key.startswith("DENOMINATOR_")])
        with node_store() as (store, _):
            env = (add_node(store, pool_config()) / "scripts" / "env.sh").read_text()
        self.assertIn(POOL_A, env)

    def test_its_settings_are_saved_for_its_own_nodes_only(self):
        with node_store() as (store, _):
            add_node(store, pool_config())
            saved = store.load_node_config("v-pools")["settings"]
            self.assertEqual(saved["DENOMINATOR_ADDRS"], f"{POOL_A} {POOL_B}")
            solo = make_node_configuration("v-solo", validator_type="solo", settings={"DENOMINATOR_POOLS": 2})
            with self.assertRaisesRegex(process.OperationError, "Invalid saved environment settings"):
                store.save_node_config(solo)
            for wrong in ({"DENOMINATOR_POOLS": "2"}, {"DENOMINATOR_MAX_STAKERS": -1},
                          {"DENOMINATOR_ADDRS": None}, {"DENOMINATOR_REWARD_SHARE": True}):
                with self.subTest(wrong=wrong):
                    with self.assertRaisesRegex(process.OperationError, "Invalid saved denominator settings"):
                        store.save_node_config(pool_config(**wrong))

    def test_prepare_refuses_terms_outside_the_safe_range_before_anything_is_built(self):
        for terms in ({"DENOMINATOR_POOLS": 3}, {"DENOMINATOR_MAX_STAKERS": 41}):
            with self.subTest(terms=terms), patch.object(denominator, "build_tools_image") as build:
                with self.assertRaises(process.OperationError):
                    denominator.prepare(None, pool_config(stage="wallet-deployed", **terms))
                build.assert_not_called()

    def test_the_kits_bid_script_refuses_the_mode_by_its_own_name(self):
        """Only the cycle bids, through a view that names a pool: a stray run of
        the kit's bid script with this node's env.sh stops at an unknown mode."""
        text = (KIT / "take_part_in_elections.sh").read_text()
        self.assertNotIn("denominator", text)
        self.assertIn("Unknown staking mode", text)


class SetupFlagTests(unittest.TestCase):
    """node setup records the pools' terms, which no pool can change later."""

    def plan(self, *flags):
        with node_store() as (store, root):
            add_node(store, make_node_configuration("s-pools", phase="console-ready", setup_stage="node-started"))
            return run_cli(root, "node", "setup", "-n", "s-pools", "--dry-run", "--yes", *flags)

    def test_the_defaults_are_a_pair_of_pools_on_the_documented_terms(self):
        code, out, err = self.plan("--validator-type", "denominator", *SETUP)
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.assertEqual(plan["validator_type"], "denominator")
        self.assertEqual(
            {key: plan["settings"][key] for key in plan["settings"] if key.startswith("DENOMINATOR_")},
            {"DENOMINATOR_POOLS": 2, "DENOMINATOR_REWARD_SHARE": 40, "DENOMINATOR_MAX_STAKERS": 40,
             "DENOMINATOR_MIN_STAKER_STAKE": 1000, "DENOMINATOR_MIN_VALIDATOR_STAKE": 1000,
             "DENOMINATOR_ADDRS": "", "DENOMINATOR_TOOLS_IMAGE": ""},
        )

    def test_the_terms_given_are_recorded(self):
        code, out, err = self.plan(
            "--validator-type", "denominator", *SETUP, "--pools", "1", "--pool-reward-share", "25",
            "--pool-max-stakers", "10", "--pool-min-staker-stake", "500", "--pool-min-validator-stake", "2000",
        )
        self.assertEqual(code, 0, err)
        settings = json.loads(out)["settings"]
        self.assertEqual(
            [settings[key] for key in ("DENOMINATOR_POOLS", "DENOMINATOR_REWARD_SHARE", "DENOMINATOR_MAX_STAKERS",
                                       "DENOMINATOR_MIN_STAKER_STAKE", "DENOMINATOR_MIN_VALIDATOR_STAKE")],
            [1, 25, 10, 500, 2000],
        )

    def test_setup_saves_the_terms_and_gives_env_sh_the_pools_keys(self):
        with node_store() as (store, root):
            directory = add_node(
                store, make_node_configuration("s-pools", phase="console-ready", setup_stage="node-started")
            )
            code, out, err = run_cli(root, "node", "setup", "-n", "s-pools", "--yes",
                                     "--validator-type", "denominator", *SETUP, "--pool-max-stakers", "20")
            self.assertEqual(code, 0, err)
            saved = store.load_node_config("s-pools")
            env = (directory / "scripts" / "env.sh").read_text()
        self.assertEqual((saved["validator_type"], saved["settings"]["DENOMINATOR_MAX_STAKERS"]), ("denominator", 20))
        self.assertIn("export STAKE_MODE=denominator\n", env)
        self.assertIn("export DENOMINATOR_ADDRS=''\n", env)
        self.assertIn('"next": "evernode wallet create -n s-pools"', out)

    def test_terms_outside_the_safe_range_are_refused(self):
        for flags, reason in (
            (("--pool-max-stakers", "41"), "1 to 40 stakers"),
            (("--pool-max-stakers", "0"), "1 to 40 stakers"),
            (("--pool-reward-share", "101"), "percentage from 0 to 100"),
            (("--pool-min-staker-stake", "99"), "at least 100 tokens"),
            (("--pool-min-validator-stake", "999"), "at least 1000 tokens"),
        ):
            with self.subTest(flags=flags):
                code, _, err = self.plan("--validator-type", "denominator", *SETUP, *flags)
                self.assertEqual(code, 1)
                self.assertIn(reason, err)

    def test_the_refusals_name_the_flags_the_parser_has(self):
        parser = build_argument_parser()
        groups = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
        setup = next(a for a in groups.choices["node"]._actions
                     if a.__class__.__name__ == "_SubParsersAction").choices["setup"]
        dests = {option: action.dest for action in setup._actions for option in action.option_strings}
        self.assertEqual({dests[flag]: flag for flag in denominator.SETUP_FLAGS.values()}, denominator.SETUP_FLAGS)

    def test_pool_terms_belong_to_this_type_and_depool_terms_do_not(self):
        code, _, err = self.plan("--validator-type", "solo", *SETUP, "--pool-max-stakers", "10")
        self.assertEqual(code, 1)
        self.assertIn("--pool-max-stakers apply only to a denominator validator", err)
        code, _, err = self.plan("--validator-type", "denominator", *SETUP, "--validator-assurance", "50000")
        self.assertEqual(code, 1)
        self.assertIn("DePool parameters do not apply to a denominator validator", err)


class PrepareTests(unittest.TestCase):
    """denominator prepare: what it asks before it drops a pool that holds anything.

    The build and the compile are stubbed: the compile yields the addresses each
    test names. A dropped pool is read through the node (stubbed per test).
    """

    NEW_A = "-1:" + "3" * 64
    NEW_B = "-1:" + "4" * 64
    FUNDED = {POOL_A: {"acc_type": "Active", "balance": 13797964098}}

    @contextlib.contextmanager
    def stubs(self, addresses, *, accounts=None, pools=None, credit=0, pending=(), tty=False, answer="n"):
        events = []

        def compile_pools(command, timeout):
            staging = Path(command[command.index("-v") + 1].split(":", 1)[0])
            (staging / "manifest.json").write_text("{}\n")
            manifest = {"maxStakers": 40, "codeHash": PINNED["codeHash"], "upstreamCommit": "c",
                        "pools": [{"salt": salt, "address": address, "factory": "-1:" + "9" * 64}
                                  for salt, address in enumerate(addresses)]}
            return subprocess.CompletedProcess(command, 0, json.dumps(manifest), "")

        @contextlib.contextmanager
        def lock(store, name):
            events.append("lock")
            yield

        def account(config, address):
            events.append(f"read {address}")
            found = (accounts or {}).get(address, {"acc_type": "Nonexist"})
            if isinstance(found, Exception):
                raise found
            return found

        def ask(prompt):
            events.append("question")
            events.append(prompt)
            return answer

        with (
            patch.object(denominator, "build_tools_image", return_value=("local/evernode-pool-tools:new", True)),
            patch("evernode.process.execute_command", side_effect=compile_pools),
            patch.object(elections, "hold_election_lock", side_effect=lock),
            patch.object(denominator, "_account", side_effect=account),
            patch.object(denominator, "read_pool", side_effect=lambda store, config, address: pools[address]),
            patch("evernode.scripts.read_elector_credit", return_value={"recoverable_nanotokens": str(credit)}),
            patch("evernode.scripts.read_wallet_pending", return_value={"pending_transactions": list(pending)}),
            patch("sys.stdin.isatty", return_value=tty),
            patch("builtins.input", side_effect=ask),
        ):
            yield events

    @staticmethod
    def staked(stakers=5, queued=0):
        account = pool_account(state=2, sent=13367, requests=queued)
        account["pool"]["nominatorsCount"] = stakers
        return account

    def prepare(self, config, addresses, *, force=False, new_contract=True, **stubs):
        """(config, result) or None, the error or None, the events, and env.sh afterwards."""
        with node_store() as (store, _), self.stubs(addresses, **stubs) as events:
            add_node(store, config)
            env = store.node_directory(config["name"]) / "scripts" / "env.sh"
            try:
                done = denominator.prepare(store, config, new_contract=new_contract, force=force)
            except process.OperationError as error:
                return None, error, events, env.read_text()
            return done, None, events, env.read_text()

    def test_a_prepare_that_keeps_the_pools_reads_and_asks_nothing(self):
        done, error, events, _ = self.prepare(pool_config(), [POOL_A, POOL_B], new_contract=False)
        self.assertIsNone(error)
        config, result = done
        self.assertEqual(events, ["lock"])
        self.assertNotIn("dropped_pools", result)
        self.assertEqual(config["setup_stage"], "ready-for-elections")

    def test_no_to_the_question_keeps_every_pool_and_takes_no_lock(self):
        done, error, events, env = self.prepare(
            pool_config(), [self.NEW_A, self.NEW_B], accounts=self.FUNDED, pools={POOL_A: self.staked()},
            tty=True, answer="n",
        )
        self.assertIsNone(done)
        self.assertIn("use --force", str(error))
        self.assertNotIn("lock", events)
        question = events[events.index("question") + 1]
        self.assertIn(f"Pool A ({POOL_A}) still holds 13.797964098 tokens in the pool and 13,367 at the elector",
                      question)
        self.assertIn("5 staker(s) and 0 queued withdrawal(s)", question)
        self.assertIn("Drop this pool from this node for good?", question)
        self.assertNotIn("Pool B", question)
        self.assertIn(POOL_A, env)
        self.assertNotIn(self.NEW_A, env)

    def test_yes_drops_the_pools_records_them_and_starts_the_setup_again(self):
        done, error, events, env = self.prepare(
            pool_config(), [self.NEW_A, self.NEW_B], accounts=self.FUNDED, pools={POOL_A: self.staked()},
            tty=True, answer="y",
        )
        self.assertIsNone(error)
        config, result = done
        self.assertEqual(events[0], f"read {POOL_A}")
        self.assertLess(events.index("question"), events.index("lock"))
        self.assertEqual(
            [(entry["pool"], entry["balance_nanotokens"], entry["at_elector_nanotokens"], entry["stakers"])
             for entry in result["dropped_pools"]],
            [("A", "13797964098", str(13367 * NANO), 5), ("B", "0", "0", 0)],
        )
        self.assertEqual(config["setup_stage"], "denominator-prepared")
        self.assertEqual(result["next"], "evernode denominator deploy -n v-pools")
        self.assertIn(self.NEW_A, env)
        self.assertNotIn(POOL_A, env)

    def test_without_a_terminal_only_force_drops_a_pool_that_holds_funds(self):
        done, error, events, _ = self.prepare(
            pool_config(), [self.NEW_A, self.NEW_B], accounts=self.FUNDED, pools={POOL_A: self.staked()},
        )
        self.assertIsNone(done)
        self.assertIn("13,367 at the elector", str(error))
        self.assertIn("--force drops them without asking", str(error))
        self.assertNotIn("lock", events)
        done, error, events, _ = self.prepare(
            pool_config(), [self.NEW_A, self.NEW_B], accounts=self.FUNDED, pools={POOL_A: self.staked()},
            force=True,
        )
        self.assertIsNone(error)
        self.assertNotIn("question", events)
        self.assertEqual(done[1]["dropped_pools"][0]["at_elector_nanotokens"], str(13367 * NANO))

    def test_yes_on_the_command_line_does_not_answer_the_question(self):
        with node_store() as (store, root), self.stubs([self.NEW_A, self.NEW_B], accounts=self.FUNDED,
                                                       pools={POOL_A: self.staked()}) as events:
            add_node(store, pool_config())
            code, out, err = run_cli(root, "denominator", "prepare", "-n", "v-pools", "--new-contract", "--yes")
            self.assertNotEqual(code, 0)
            self.assertIn("--force drops them without asking", err)
            self.assertEqual(store.load_node_config("v-pools")["settings"]["DENOMINATOR_ADDRS"], f"{POOL_A} {POOL_B}")
            code, out, err = run_cli(root, "denominator", "prepare", "-n", "v-pools", "--new-contract", "--yes",
                                     "--force")
            self.assertEqual(code, 0, err)
            saved = store.load_node_config("v-pools")
        self.assertEqual(saved["settings"]["DENOMINATOR_ADDRS"], f"{self.NEW_A} {self.NEW_B}")
        self.assertEqual(saved["setup_stage"], "denominator-prepared")
        self.assertEqual([entry["pool"] for entry in json.loads(out)["dropped_pools"]], ["A", "B"])
        self.assertIn("lock", events)

    def test_a_pool_that_cannot_be_read_blocks_the_change_once_deployed(self):
        down = {POOL_A: process.OperationError("The node container is not running")}
        done, error, events, _ = self.prepare(pool_config(), [self.NEW_A, self.NEW_B], accounts=down)
        self.assertIsNone(done)
        self.assertIn(f"pool A ({POOL_A}) could not be read: The node container is not running", str(error))
        self.assertIn("--force", str(error))
        self.assertNotIn("lock", events)
        done, error, _, _ = self.prepare(pool_config(), [self.NEW_A, self.NEW_B], accounts=down, force=True)
        self.assertIsNone(error)
        self.assertEqual(done[1]["dropped_pools"][1]["not_checked"], "The node container is not running")
        # Before deploy, prepare works without the node and reports what it could not check.
        done, error, events, _ = self.prepare(
            pool_config(stage="denominator-prepared"), [self.NEW_A, self.NEW_B], accounts=down, new_contract=False,
        )
        self.assertIsNone(error)
        self.assertNotIn("question", events)
        self.assertIn("not_checked", done[1]["dropped_pools"][1])

    def test_a_pool_that_never_existed_is_dropped_without_a_question(self):
        done, error, events, _ = self.prepare(
            pool_config(stage="denominator-prepared"), [self.NEW_A, self.NEW_B], new_contract=False,
        )
        self.assertIsNone(error)
        self.assertNotIn("question", events)
        self.assertEqual(done[1]["dropped_pools"][0]["balance_nanotokens"], "0")
        self.assertEqual(done[0]["setup_stage"], "denominator-prepared")

    def test_a_pair_that_becomes_one_pool_reads_only_the_pool_it_drops(self):
        funded = {POOL_B: {"acc_type": "Active", "balance": 30 * NANO}}
        done, error, events, _ = self.prepare(
            pool_config(), [POOL_A], accounts=funded, pools={POOL_B: pool_account(balance=30)},
            tty=True, answer="y",
        )
        self.assertIsNone(error)
        self.assertEqual([event for event in events if event.startswith("read ")], [f"read {POOL_B}"])
        self.assertEqual([entry["pool"] for entry in done[1]["dropped_pools"]], ["B"])
        # Pool A is deployed already, so the node stays ready.
        self.assertEqual(done[0]["setup_stage"], "ready-for-elections")

    def test_an_unsigned_safe_transaction_to_a_pool_is_asked_about(self):
        done, error, events, _ = self.prepare(
            pool_config(stage="denominator-prepared"), [self.NEW_A, self.NEW_B], new_contract=False,
            pending=[{"id": "7", "dest": POOL_A}], tty=True, answer="n",
        )
        self.assertIsNone(done)
        question = events[events.index("question") + 1]
        self.assertIn(f"Pool A ({POOL_A}) still holds 0 tokens", question)
        self.assertIn("the Safe holds 1 unsigned transaction(s) to it", question)


class LinkTests(unittest.TestCase):
    """denominator link: every Oberton link, built offline from fixed bodies."""

    def links(self, **options):
        with patch.object(denominator, "_console", side_effect=AssertionError("a link reads nothing")):
            return denominator.links(pool_config(**options.pop("settings", {})), **options)

    def test_every_pool_gets_every_kind_of_link(self):
        result = self.links()
        self.assertEqual([pool["pool"] for pool in result["pools"]], ["A", "B"])
        for pool in result["pools"]:
            self.assertEqual([link["kind"] for link in pool["links"]],
                             ["stake", "withdraw", "update", "recover", "pay"])

    def test_every_link_but_the_stake_attaches_two_tokens(self):
        for link in self.links()["pools"][0]["links"]:
            with self.subTest(kind=link["kind"]):
                expected = 1001 if link["kind"] == "stake" else 2
                self.assertEqual(link["attach_tokens"], expected)

    def test_the_stake_link_attaches_the_smallest_deposit_and_the_pools_fee(self):
        result = self.links(settings={"DENOMINATOR_MIN_STAKER_STAKE": 500})
        self.assertEqual(result["pools"][0]["links"][0]["attach_tokens"], 501)

    def test_a_link_is_a_transfer_to_the_pool_carrying_its_body(self):
        for pool in self.links()["pools"]:
            for link in pool["links"]:
                parsed = urllib.parse.urlsplit(link["link"])
                query = dict(urllib.parse.parse_qsl(parsed.query))
                with self.subTest(pool=pool["pool"], kind=link["kind"]):
                    self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", denominator.WALLET_LINK)
                    self.assertEqual(query, {"t": "p", "da": pool["address"], "v": str(link["attach_tokens"]),
                                             "p": link["body_base64"]})
                    self.assertNotIn("=", parsed.query.split("&p=", 1)[1])

    def test_each_body_is_the_operation_it_is_named_for(self):
        query = bytes(8)
        expected = {
            "stake": bytes(4) + b"d",
            "withdraw": bytes(4) + b"w",
            "update": (6).to_bytes(4, "big") + query,
            "recover": (0x47657424).to_bytes(4, "big") + query,
            "pay": (2).to_bytes(4, "big") + query + bytes([denominator.LINK_PAY_LIMIT]),
        }
        for kind, data in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(denominator._single_cell_boc(data), denominator.BODIES[kind])

    def test_a_staker_gets_a_link_that_pays_its_queued_withdrawal(self):
        self.assertNotIn("pay-one", [link["kind"] for link in self.links()["pools"][0]["links"]])
        link = self.links(staker=STAKER)["pools"][0]["links"][-1]
        self.assertEqual(link["kind"], "pay-one")
        self.assertEqual(link["attach_tokens"], 2)
        self.assertEqual(
            link["body_base64"],
            denominator._single_cell_boc((3).to_bytes(4, "big") + bytes(8) + bytes.fromhex("ab" * 32)),
        )

    def test_a_staker_outside_the_basechain_is_refused(self):
        for staker in ("-1:" + "ab" * 32, "0:" + "ab" * 31, "0:" + "zz" * 32):
            with self.subTest(staker=staker), self.assertRaises(process.OperationError):
                self.links(staker=staker)

    def test_one_pool_can_be_named(self):
        self.assertEqual([pool["address"] for pool in self.links(label="B")["pools"]], [POOL_B])
        with self.assertRaisesRegex(process.OperationError, "no pool C"):
            self.links(label="C")

    def test_the_command_needs_neither_the_node_nor_a_confirmation(self):
        with node_store() as (store, root):
            add_node(store, pool_config())
            with patch("evernode.scripts.require_node_console", side_effect=AssertionError("console")):
                code, out, err = run_cli(root, "denominator", "link", "-n", "v-pools", "--pool", "A")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["pools"][0]["address"], POOL_A)


class FundsAndRetirementTests(unittest.TestCase):
    """The validator's own funds, the cycle's switch and its removal."""

    def test_a_deposit_needs_verified_pools_and_a_positive_amount(self):
        with self.assertRaisesRegex(process.OperationError, "deployed and verified first"):
            denominator.deposit(None, pool_config(stage="denominator-deployed"), amount=1000)
        with self.assertRaisesRegex(process.OperationError, "positive"):
            denominator.deposit(None, pool_config(), amount=0)

    def test_a_deposit_adds_the_pools_fee_and_readies_funded_pools(self):
        config = pool_config(stage="denominator-verified")
        with (
            patch.object(denominator, "_send", return_value={"transaction_id": "1"}) as send,
            patch.object(denominator, "read_pool", return_value=pool_account(validator_funds=1000)),
        ):
            config, result = denominator.deposit(None, config, amount=1000)
        self.assertEqual(
            [call.args[2:] for call in send.call_args_list],
            [(POOL_A, 1001 * NANO, "deposit-validator"), (POOL_B, 1001 * NANO, "deposit-validator")],
        )
        self.assertEqual(result["stage"], "ready-for-elections")
        with (
            patch.object(denominator, "_send", return_value={}),
            patch.object(denominator, "read_pool", return_value=pool_account(validator_funds=999)),
        ):
            config, result = denominator.deposit(None, pool_config(stage="denominator-verified"), amount=1)
        self.assertEqual(result["stage"], "denominator-verified")

    def test_a_withdrawal_takes_only_what_the_stakers_the_reserve_and_storage_do_not_own(self):
        config = pool_config()
        with patch.object(denominator, "read_pool", return_value=pool_account(state=2)):
            with self.assertRaisesRegex(process.OperationError, "stake is out"):
                denominator.withdraw(None, config, amount=1, label="A")
        idle = pool_account(balance=3010, stakers_funds=2000)
        with (
            patch.object(denominator, "read_pool", return_value=idle),
            patch.object(denominator, "storage_year", return_value=50 * NANO),
        ):
            with self.assertRaisesRegex(process.OperationError,
                                        r"holds 950 tokens .* a year of its storage \(50 tokens\)"):
                denominator.withdraw(None, config, amount=951, label="A")
            with patch.object(denominator, "_send", return_value={}) as send:
                denominator.withdraw(None, config, amount=950, label="A")
        send.assert_called_once_with(None, config, POOL_A, NANO, "withdraw-validator", 950 * NANO)

    def test_a_year_of_storage_is_priced_for_a_full_pool_at_the_current_prices(self):
        prices = {"p18": [
            {"utime_since": 0, "mc_bit_price_ps_dec": "1000", "mc_cell_price_ps_dec": "500000"},
            # A price the network has voted for but that is not in force yet.
            {"utime_since": 2_000_000_000, "mc_bit_price_ps_dec": "9", "mc_cell_price_ps_dec": "9"},
        ]}
        reply = subprocess.CompletedProcess([], 0, json.dumps(prices), "")
        with patch.object(denominator, "_console", return_value=reply) as console:
            year = denominator.storage_year(pool_config(), now=1_800_000_000)
        console.assert_called_once_with(pool_config(), "getconfig 18")
        # (36,000 bits x 1000 + 150 cells x 500,000) a second, over 2^16, for 365 days, rounded up.
        self.assertEqual(year, 53413330079)
        for broken in ("{}", json.dumps({"p18": []}), json.dumps({"p18": [{"utime_since": 0}]}), "no json"):
            with self.subTest(broken=broken), patch.object(
                denominator, "_console", return_value=subprocess.CompletedProcess([], 0, broken, "")
            ):
                with self.assertRaisesRegex(process.OperationError, "storage prices"):
                    denominator.storage_year(pool_config(), now=1_800_000_000)

    def test_info_leaves_a_year_of_storage_out_of_what_the_validator_can_withdraw(self):
        idle = pool_account(balance=3010, stakers_funds=2000)
        with (
            patch.object(denominator, "read_pool", return_value=idle),
            patch("evernode.scripts.read_elector_credit", return_value={"recoverable_nanotokens": "0"}),
        ):
            known = denominator._pool_report(None, pool_config(), {"label": "A", "address": POOL_A}, 0, 10**6,
                                             50 * NANO)
            unread = denominator._pool_report(None, pool_config(), {"label": "A", "address": POOL_A}, 0, 10**6,
                                              denominator.UNKNOWN)
        self.assertEqual(known["validator_withdrawable_nanotokens"], str(950 * NANO))
        self.assertEqual(unread["validator_withdrawable_nanotokens"], denominator.UNKNOWN)

    def test_info_names_the_step_the_cycle_takes_next(self):
        now = 10**6
        cases = (
            (pool_account(state=0, requests=3), None, "pay 3 queued withdrawal(s)"),
            (pool_account(state=0), None, "bid in the next election it can"),
            (pool_account(state=2, changes=0, change_time=100), 200, "count the new validator set (op 6)"),
            (pool_account(state=2, changes=1, change_time=200), 200, "wait for 1 more new validator set(s)"),
            (pool_account(state=2, changes=2, change_time=now - 100, held=1000), now - 100, "recover the stake after"),
            (pool_account(state=2, changes=2, change_time=now - 2000, held=1000, sent=600), now - 2000,
             "recover the stake"),
            (pool_account(state=2, changes=3, change_time=now - 50, sent=600), now - 50, "recover the stake"),
        )
        credit = 600 * NANO
        bidding = pool_config(bidding=True)
        for account, since, expected in cases:
            with self.subTest(expected=expected):
                self.assertTrue(
                    denominator._next_action(bidding, account["pool"], credit, since, now).startswith(expected)
                )
        waiting = pool_account(state=2, changes=2, change_time=now - 2000, held=1000, sent=600)["pool"]
        self.assertEqual(denominator._next_action(bidding, waiting, credit - 1, now - 2000, now),
                         "wait for the elector to release the stake")
        # After election stop an idle pool waits; its queued withdrawals are still paid.
        stopped = pool_config(bidding=False)
        self.assertEqual(denominator._next_action(stopped, pool_account()["pool"], 0, None, now),
                         "nothing: bidding is stopped")
        self.assertEqual(denominator._next_action(stopped, pool_account(requests=2)["pool"], 0, None, now),
                         "pay 2 queued withdrawal(s)")

    def test_a_stake_the_elector_holds_again_reads_returned(self):
        sent = 26227 * NANO
        cases = (
            (pool_account(state=2, changes=0, sent=26227), sent, "returned"),
            (pool_account(state=2, changes=2, sent=26227), sent, "returned"),
            (pool_account(state=2, changes=1, sent=26227), sent - 1, "staked"),
            (pool_account(state=2, changes=0, sent=26227), 0, "staked"),
            (pool_account(state=1, changes=0, sent=26227), 0, "bid sent"),
            (pool_account(state=0), 0, "idle"),
            (pool_account(state=2, changes=0, sent=26227), denominator.UNKNOWN, "staked"),
        )
        for account, credit, phase in cases:
            with self.subTest(phase=phase, credit=credit, changes=account["pool"]["validatorSetChanges"]):
                self.assertEqual(denominator._phase(account["pool"], credit), phase)

    def test_a_pool_that_never_bid_reports_no_election(self):
        """Before its first bid, stake_at holds the pool's salt, not an election."""
        for stake_at, expected in ((0, None), (1, None), (1_800_000_000, 1_800_000_000)):
            with (
                self.subTest(stake_at=stake_at),
                patch.object(denominator, "read_pool", return_value=pool_account(stake_at=stake_at)),
                patch("evernode.scripts.read_elector_credit", return_value={"recoverable_nanotokens": "0"}),
            ):
                report = denominator._pool_report(None, pool_config(), {"label": "B", "address": POOL_B}, 0, 10**6)
                self.assertEqual(report["election_id"], expected)

    @staticmethod
    def pending(*transactions):
        return patch("evernode.scripts.read_wallet_pending",
                     return_value={"pending_transactions": list(transactions)})

    def test_retire_refuses_while_the_cycle_still_has_work(self):
        with self.assertRaisesRegex(process.OperationError, "still bids"):
            denominator.retire(None, pool_config(bidding=True))
        accounts = {POOL_A: pool_account(state=2), POOL_B: pool_account(requests=2)}
        with (
            patch.object(denominator, "read_pool", side_effect=lambda store, config, address: accounts[address]),
            patch("evernode.scripts.read_elector_credit", return_value={"recoverable_nanotokens": "0"}),
            self.pending(),
            patch.object(elections, "disable_node_election_schedule") as disable,
        ):
            with self.assertRaises(process.OperationError) as caught:
                denominator.retire(None, pool_config())
        self.assertIn("pool A's stake is out", str(caught.exception))
        self.assertIn("pool B has 2 queued withdrawal(s)", str(caught.exception))
        disable.assert_not_called()

    def test_retire_refuses_while_the_safe_holds_an_unsigned_transaction_to_a_pool(self):
        """A half-signed bid signed after the cycle is gone would send a stake out
        that nothing brings back, though every pool reads idle."""
        with (
            patch.object(denominator, "read_pool", return_value=pool_account()),
            patch("evernode.scripts.read_elector_credit", return_value={"recoverable_nanotokens": "0"}),
            self.pending({"id": "0x5", "dest": POOL_B}, {"id": "0x6", "dest": STAKER}),
            patch.object(elections, "disable_node_election_schedule") as disable,
        ):
            with self.assertRaises(process.OperationError) as caught:
                denominator.retire(None, pool_config())
        self.assertIn("the Safe holds 1 unsigned transaction(s) to pool B (id 0x5)", str(caught.exception))
        self.assertIn("evernode stake confirm -n v-pools --transaction-id ID, or let them expire",
                      str(caught.exception))
        self.assertNotIn("pool A", str(caught.exception))
        disable.assert_not_called()

    def test_retire_removes_the_cycle_once_every_stake_is_back(self):
        """A transaction the Safe holds to anything but the pools does not hold it up."""
        with (
            patch.object(denominator, "read_pool", return_value=pool_account()),
            patch("evernode.scripts.read_elector_credit", return_value={"recoverable_nanotokens": "0"}),
            self.pending({"id": "0x6", "dest": STAKER}),
            patch.object(elections, "disable_node_election_schedule") as disable,
        ):
            _, result = denominator.retire(None, pool_config())
        disable.assert_called_once_with("v-pools")
        self.assertEqual(result["cycle"], "removed")

    def test_retire_stops_when_the_safe_cannot_be_read(self):
        with (
            patch("evernode.scripts.read_wallet_pending",
                  side_effect=process.OperationError("Could not read the pending transactions")),
            patch.object(elections, "disable_node_election_schedule") as disable,
        ):
            with self.assertRaisesRegex(process.OperationError, "Could not read the pending transactions"):
                denominator.retire(None, pool_config())
        disable.assert_not_called()

    def test_start_needs_funded_verified_pools(self):
        with self.assertRaisesRegex(process.OperationError, "denominator deposit -n v-pools"):
            denominator.require_ready(pool_config(stage="denominator-verified"))

    def test_stop_turns_bids_off_and_start_turns_them_back_on(self):
        with node_store() as (store, root), tempfile.TemporaryDirectory() as cron:
            config = pool_config(bidding=True)
            directory = add_node(store, config)
            cron_file = Path(cron) / "evernode-v-pools"
            with patch.object(elections, "node_election_cron_path", return_value=cron_file):
                cron_file.write_text("# Managed by evernode\n")
                elections.write_election_programs(store, config)
                code, out, err = run_cli(root, "election", "stop", "--dry-run", "-n", "v-pools")
                self.assertEqual(json.loads(out), {"disable_managed_schedules": [],
                                                   "stop_bidding_keep_pool_cycle": ["v-pools"]})
                code, out, err = run_cli(root, "election", "stop", "-n", "v-pools")
                self.assertEqual(code, 0, err)
                self.assertIn("bidding stopped; the pools' cycle keeps running", out)
                self.assertTrue(cron_file.exists())
                self.assertFalse(denominator.bidding(store.load_node_config("v-pools")))
                sequence = directory / elections.DENOMINATOR_SEQUENCE_FILE
                self.assertIn("cycle maintain", sequence.read_text())
                with patch("evernode.docker.image_exists", return_value=True):
                    config = denominator.start_bidding(store, store.load_node_config("v-pools"))
                self.assertTrue(denominator.bidding(config))
                self.assertIn("cycle bid", sequence.read_text())


class ToolsImageTests(unittest.TestCase):
    """A pruned pool tools image stops every read, every send and the whole cycle,
    so whatever needs it names it in the operator's terminal, with the remedy."""

    def assert_names_the_image_and_its_remedy(self, error):
        text = str(error)
        self.assertIn("The pool tools image local/evernode-pool-tools:0123456789abcdef is missing", text)
        self.assertIn("their cycle does no maintenance", text)
        self.assertIn("Restore it with: evernode denominator prepare -n v-pools", text)

    def test_reading_a_pool_names_the_missing_image(self):
        with (
            patch("evernode.docker.image_exists", return_value=False),
            patch.object(denominator, "_console"),
            patch("evernode.process.execute_command", side_effect=AssertionError("ran without the image")),
            node_store() as (store, _),
        ):
            add_node(store, pool_config())
            with self.assertRaises(process.OperationError) as caught:
                denominator.read_pool(store, pool_config(), POOL_A)
        self.assert_names_the_image_and_its_remedy(caught.exception)

    def test_a_send_names_the_missing_image_before_the_safe_is_asked(self):
        with (
            patch("evernode.docker.image_exists", return_value=False),
            patch("evernode.scripts.execute_validator_script", side_effect=AssertionError("sent")),
        ):
            with self.assertRaises(process.OperationError) as caught:
                denominator._send(None, pool_config(), POOL_A, NANO, "withdraw-validator", NANO)
        self.assert_names_the_image_and_its_remedy(caught.exception)

    def test_info_and_stake_status_name_the_missing_image(self):
        with node_store() as (store, root):
            add_node(store, pool_config())
            with (
                patch("evernode.docker.image_exists", return_value=False),
                patch("evernode.scripts.require_node_console"),
                patch.object(denominator, "_console",
                             return_value=subprocess.CompletedProcess([], 0, json.dumps({"balance": "1"}), "")),
                patch("evernode.scripts.read_wallet_pending", return_value={"pending_transactions": []}),
                patch("evernode.nodes.fetch_chain_parameter", return_value={"utime_since": 1}),
            ):
                for words in (("denominator", "info"), ("stake", "status")):
                    with self.subTest(command=" ".join(words)):
                        code, out, err = run_cli(root, *words, "-n", "v-pools")
                        self.assertNotEqual(code, 0)
                        self.assert_names_the_image_and_its_remedy(err)

    def test_a_node_without_a_recorded_image_is_told_to_prepare(self):
        with self.assertRaisesRegex(process.OperationError, "no pool tools image recorded; run: evernode "
                                                            "denominator prepare -n v-pools"):
            denominator.require_tools_image(pool_config(DENOMINATOR_TOOLS_IMAGE=""))


class ScheduleGateTests(unittest.TestCase):
    """What waits for election stop waits until the schedule cannot bid.

    For every other validator type that is exactly while the cron file exists,
    as before. A denominator validator's schedule outlives election stop to
    bring its pools' stakes back, so for it the advice to stop elections first
    has to work too: wallet send, node restart and image maintenance follow
    bidding, and node remove waits for denominator retire instead.
    """

    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o077))
        guard = patch.object(storage.NodeStateStore, "validate_state_root")
        guard.start()
        self.addCleanup(guard.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = storage.NodeStateStore(temporary.name)
        self.cron = Path(temporary.name) / "cron"
        cron = patch.object(elections, "node_election_cron_path", side_effect=lambda name: self.cron)
        cron.start()
        self.addCleanup(cron.stop)

    def solo(self):
        return make_node_configuration("v-solo", setup_stage="ready-for-elections", validator_type="solo",
                                       wallet=dict(pool_config()["wallet"]), settings={"MSIG_FIX_STAKE": 100})

    def test_only_a_schedule_that_can_bid_blocks(self):
        cases = (
            (self.solo(), False, False), (self.solo(), True, True),
            (pool_config(bidding=True), True, True), (pool_config(bidding=False), True, False),
            (pool_config(bidding=False), False, False),
        )
        for config, installed, blocks in cases:
            self.cron.unlink(missing_ok=True)
            if installed:
                self.cron.write_text("# Managed by evernode\n")
            with self.subTest(type=config["validator_type"], bidding=denominator.bidding(config), installed=installed):
                self.assertEqual(elections.schedule_can_bid(config), blocks)
                self.assertEqual(elections.pools_cycle_installed(config),
                                 installed and config["validator_type"] == "denominator")

    def review_send(self, config, address):
        args = type("Args", (), {"address": address, "amount": 1, "no_bounce": False})()
        with patch("evernode.nodes.fetch_special_address", side_effect=lambda config, index: f"-1:{index}"):
            return setup_actions._review_send(self.store, config, args)

    def test_wallet_send_waits_for_bidding_to_stop(self):
        self.cron.write_text("# Managed by evernode\n")
        with self.assertRaisesRegex(process.OperationError, "election schedule is installed"):
            self.review_send(pool_config(bidding=True), STAKER)
        with self.assertRaisesRegex(process.OperationError, "election schedule is installed"):
            self.review_send(self.solo(), STAKER)
        # With bidding stopped the send goes on to its other checks: never to a pool.
        with self.assertRaisesRegex(process.OperationError, "never pays this node's pool A"):
            self.review_send(pool_config(bidding=False), POOL_A)

    def lifecycle(self, config, action, force=False):
        add_node(self.store, dict(config, phase="console-ready", initialized=True))
        with (
            patch.object(nodes, "_refuse_while_validating"),
            patch.object(nodes, "_refuse_with_pending_bid"),
            patch.object(nodes, "stop_node") as stop,
            patch.object(nodes, "start_node"),
            patch.object(nodes, "remove_node") as remove,
            patch.object(elections, "hold_election_lock", return_value=contextlib.nullcontext()),
        ):
            nodes.apply_node_lifecycle_operation(self.store, config["name"], action, force=force)
        self.stopped_with = stop.call_args
        return stop.called or remove.called

    def test_restart_and_image_maintenance_wait_for_bidding_to_stop(self):
        self.cron.write_text("# Managed by evernode\n")
        for config, allowed in ((self.solo(), False), (pool_config(bidding=True), False),
                                (pool_config(bidding=False), True)):
            with self.subTest(type=config["validator_type"], bidding=denominator.bidding(config)):
                if allowed:
                    self.assertTrue(self.lifecycle(config, "restart"))
                else:
                    with self.assertRaisesRegex(process.OperationError, "Disable the election schedule"):
                        self.lifecycle(config, "restart")
                add_node(self.store, dict(config, phase="console-ready", initialized=True))
                with patch.object(nodes, "get_node_status", return_value={"container": "absent"}):
                    reasons = nodes.image_maintenance_blockers(self.store, config["name"])
                self.assertEqual(any("election schedule is installed" in r for r in reasons), not allowed)

    def test_a_stop_restart_or_update_keeps_the_pools_cycle(self):
        """Every other type's schedule goes with a stop, as before; a denominator
        validator's stays, with its bids turned off as election stop turns them off."""
        self.cron.write_text("# Managed by evernode\n")
        self.assertTrue(self.lifecycle(pool_config(bidding=False), "restart"))
        self.assertIs(self.stopped_with.kwargs["disable_elections"], False)
        self.assertTrue(self.lifecycle(pool_config(bidding=True), "stop"))
        self.assertIs(self.stopped_with.kwargs["disable_elections"], False)
        self.assertFalse(denominator.bidding(self.store.load_node_config("v-pools")))
        sequence = self.store.node_directory("v-pools") / elections.DENOMINATOR_SEQUENCE_FILE
        self.assertIn("cycle maintain", sequence.read_text())
        # Another type's stop is the call it always was, which removes its schedule.
        self.assertTrue(self.lifecycle(self.solo(), "stop"))
        self.assertEqual(self.stopped_with.kwargs, {})
        add_node(self.store, dict(pool_config(bidding=False), phase="console-ready", initialized=True))
        with (
            patch.object(nodes, "image_maintenance_blockers", return_value=[]),
            patch("evernode.process.execute_command"),
            patch.object(nodes, "stop_node") as stop,
            patch("evernode.docker.build_node_compose_config", return_value={}),
            patch.object(nodes, "start_node"),
            patch.object(nodes, "fetch_node_statistics", return_value={}),
            patch.object(nodes, "is_node_synchronized", return_value=True),
            patch("evernode.nodes.time.sleep"),
            patch.object(elections, "hold_election_lock", return_value=contextlib.nullcontext()),
        ):
            nodes.update_node_image(self.store, "v-pools", {"image": "local/node:2", "image_id": "sha256:" + "b" * 64})
        self.assertIs(stop.call_args.kwargs["disable_elections"], False)
        self.assertTrue(self.cron.exists())

    def test_remove_waits_for_denominator_retire_unless_forced(self):
        self.cron.write_text("# Managed by evernode\n")
        with self.assertRaisesRegex(process.OperationError, "denominator retire -n v-pools"):
            self.lifecycle(pool_config(bidding=False), "remove")
        self.assertTrue(self.lifecycle(pool_config(bidding=False), "remove", force=True))
        self.assertTrue(self.lifecycle(self.solo(), "remove"))
        self.cron.unlink()
        self.assertTrue(self.lifecycle(pool_config(bidding=False), "remove"))


@unittest.skipUnless(
    (POOL_SOURCE / "node_modules").is_dir() and shutil.which("node"),
    "the vendored pool tools are not installed in this checkout",
)
class PoolToolTests(unittest.TestCase):
    """The pool tool, run directly: the image runs the same file."""

    @classmethod
    def setUpClass(cls):
        cls.work = Path(tempfile.mkdtemp())
        cls.code = cls.work / "code.boc"
        cls.compiled = json.loads(cls.tool("compile", str(cls.code)))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    @classmethod
    def tool(cls, *arguments, check=True):
        result = subprocess.run(["node", "tools/cells.cjs", *arguments], cwd=POOL_SOURCE,
                                capture_output=True, text=True, timeout=300)
        if check and result.returncode:
            raise AssertionError(result.stderr.strip() or result.stdout.strip())
        return result.stdout.strip() if check else result

    @classmethod
    def script(cls, program, *arguments, **environment):
        """A snippet against @ton/core; '-1:' addresses go in the environment,
        where 'node -e' cannot take them for options."""
        return json.loads(subprocess.run(
            ["node", "-e", program, *arguments], cwd=POOL_SOURCE, capture_output=True, text=True,
            timeout=300, check=True, env=dict(os.environ, **environment),
        ).stdout)

    def address(self, salt, validator=SAFE, out=None):
        terms = ("4000", "40", str(1000 * NANO), str(1000 * NANO), str(salt))
        if out:
            return self.tool("init", str(self.code), validator, *terms, str(out))
        return self.tool("address", str(self.code), validator, *terms)

    def body(self, kind, *arguments):
        descriptor, out = tempfile.mkstemp(dir=self.work, prefix=kind + "-", suffix=".boc")
        os.close(descriptor)
        self.tool("body", kind, out, *arguments)
        return Path(out)

    def test_the_contract_compiles_to_the_pinned_hash(self):
        self.assertEqual(self.compiled["codeHash"], PINNED["codeHash"])
        self.assertEqual(self.compiled["codeHashBase64"], PINNED["codeHashBase64"])
        self.assertEqual(self.compiled["compiler"], PINNED["compiler"]["@ton-community/func-js"])

    def test_the_sources_are_upstreams_unchanged(self):
        import hashlib
        for name, digest in PINNED["sourceDigests"].items():
            with self.subTest(source=name):
                self.assertEqual(hashlib.sha256((POOL_SOURCE / name).read_bytes()).hexdigest(), digest)

    def test_a_pair_differs_by_its_salt_alone(self):
        first, second = self.address(0), self.address(1)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("-1:") and second.startswith("-1:"))
        self.assertEqual(self.address(0), first)
        self.assertEqual(self.address(0, out=self.work / "init-0.boc"), first)
        self.assertNotEqual(self.address(0, validator="-1:" + "b" * 64), first)

    def test_a_validator_wallet_outside_the_masterchain_is_refused(self):
        result = self.tool("address", str(self.code), "0:" + "a" * 64, "4000", "40", "1", "1", "0", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("masterchain", result.stderr)

    def test_each_fixed_body_is_the_one_the_tool_builds(self):
        built = {
            "stake": self.body("comment", "d"),
            "withdraw": self.body("comment", "w"),
            "update": self.body("update"),
            "recover": self.body("recover"),
            "pay": self.body("pay", str(denominator.LINK_PAY_LIMIT)),
        }
        for kind, path in built.items():
            with self.subTest(kind=kind):
                self.assertEqual(self.tool("base64", str(path)), denominator.BODIES[kind])
        self.assertEqual(self.tool("base64", str(self.body("pay-one", STAKER))), denominator._pay_one_body(STAKER))

    def test_a_bid_keeps_the_electors_request_and_inserts_the_amount(self):
        request = self.work / "request.boc"
        self.script(
            "const {beginCell} = require('@ton/core');"
            "const [out, query, at] = process.argv.slice(1);"
            "const signature = beginCell().storeBuffer(Buffer.alloc(64, 7)).endCell();"
            "require('fs').writeFileSync(out, beginCell().storeUint(0x4e73744b, 32).storeUint(BigInt(query), 64)"
            ".storeUint(BigInt('0x' + '5'.repeat(64)), 256).storeUint(Number(at), 32).storeUint(196608, 32)"
            ".storeUint(BigInt('0x' + '6'.repeat(64)), 256).storeRef(signature).endCell().toBoc());"
            "console.log('{}');",
            str(request), "1793389276", "1793400000",
        )
        for query, expected in (("1793389276", "1793389276"), ("0", "1793400000")):
            if query == "0":
                self.script(
                    "const {beginCell, Cell} = require('@ton/core'); const fs = require('fs');"
                    "const s = Cell.fromBoc(fs.readFileSync(process.argv[1]))[0].beginParse();"
                    "s.loadUint(32); s.loadUintBig(64);"
                    "fs.writeFileSync(process.argv[1], beginCell().storeUint(0x4e73744b, 32).storeUint(0, 64)"
                    ".storeSlice(s).endCell().toBoc()); console.log('{}');",
                    str(request),
                )
            stake_body = self.body("stake", str(request), str(12345 * NANO))
            info = json.loads(self.tool("stake-info", str(stake_body), "pool"))
            with self.subTest(query=query):
                self.assertEqual(info, {"op": 0x4e73744b, "query": expected, "amount": str(12345 * NANO),
                                        "key": "5" * 64, "at": 1793400000, "factor": 196608, "adnl": "6" * 64})
                signature = self.script(
                    "const {Cell} = require('@ton/core');"
                    "const c = Cell.fromBoc(require('fs').readFileSync(process.argv[1]))[0];"
                    "console.log(JSON.stringify({refs: c.refs.length,"
                    " signature: c.refs[0].beginParse().loadBuffer(64).toString('hex')}));",
                    str(stake_body),
                )
                self.assertEqual(signature, {"refs": 1, "signature": "07" * 64})

    def test_the_deployer_creates_the_pool_with_a_top_up_it_accepts(self):
        init = self.work / "init-deploy.boc"
        address = self.address(1, out=init)
        triggers = []
        for name in ("trigger-1.boc", "trigger-2.boc"):
            trigger = self.work / name
            factory = self.tool("factory", str(init), str(12 * NANO), str(trigger))
            # The message the deployer sends has no source yet: the chain fills it in.
            triggers.append(self.script(
                "const {Cell, loadMessage, loadMessageRelaxed} = require('@ton/core');"
                "const m = loadMessage(Cell.fromBoc(require('fs').readFileSync(process.argv[1]))[0].beginParse());"
                "const inner = loadMessageRelaxed(m.init.data.beginParse().loadRef().beginParse());"
                "const body = inner.body.beginParse();"
                "console.log(JSON.stringify({factory: m.info.dest.toRawString(), to: inner.info.dest.toRawString(),"
                " value: inner.info.value.coins.toString(), bounce: inner.info.bounce,"
                " code: inner.init.code.hash().toString('hex'), op: body.loadUint(32),"
                " query: body.loadUintBig(64).toString(), left: body.remainingBits,"
                " trigger: m.body.hash().toString('hex')}));",
                str(trigger),
            ))
            self.assertEqual(triggers[-1]["factory"], factory)
        first, second = triggers
        self.assertEqual(
            {key: first[key] for key in ("to", "value", "bounce", "code", "op", "query", "left")},
            {"to": address, "value": str(12 * NANO), "bounce": False, "code": PINNED["codeHash"],
             "op": 1, "query": "0", "left": 0},
        )
        # Each trigger differs, or the node's message queue drops the second as a repeat.
        self.assertEqual(first["factory"], second["factory"])
        self.assertNotEqual(first["trigger"], second["trigger"])

    def test_a_pool_account_reads_as_the_pool_and_nothing_else_does(self):
        init = self.work / "init-account.boc"
        address = self.address(1, out=init)
        program = (
            "const fs = require('fs');"
            "const {beginCell, storeAccount, Address, Cell, loadStateInit} = require('@ton/core');"
            "const init = loadStateInit(Cell.fromBoc(fs.readFileSync(process.argv[1]))[0].beginParse());"
            "if (process.argv[3] === 'other') init.code = beginCell().storeUint(7, 8).endCell();"
            "const account = {addr: Address.parseRaw(process.env.ADDRESS),"
            " storageStats: {used: {cells: 1n, bits: 1n}, storageExtra: null, lastPaid: 0, duePayment: null},"
            " storage: {lastTransLt: 0n, balance: {coins: 25000000000n}, state: {type: 'active', state: init}}};"
            "fs.writeFileSync(process.argv[2], beginCell().storeBit(1).store(storeAccount(account)).endCell().toBoc());"
            "console.log('{}');"
        )
        state = self.work / "account.boc"
        self.script(program, str(init), str(state), "pool", ADDRESS=address)
        account = json.loads(self.tool("account", str(state)))
        self.assertEqual((account["address"], account["state"], account["balance"]), (address, "active", "25000000000"))
        self.assertEqual(account["validator"], SAFE)
        pool = account["pool"]
        self.assertEqual(pool["config"], {"validator": SAFE, "rewardShare": 4000, "maxNominators": 40,
                                          "minValidatorStake": str(1000 * NANO), "minNominatorStake": str(1000 * NANO)})
        self.assertEqual((pool["state"], pool["stakeAt"], pool["nominators"], pool["withdrawRequests"]), (0, 1, [], 0))
        self.script(program, str(init), str(state), "other", ADDRESS=address)
        other = json.loads(self.tool("account", str(state)))
        self.assertNotIn("pool", other)
        self.assertNotIn("validator", other)


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "Requires bash and jq")
class CycleTests(unittest.TestCase):
    """denominator_cycle.sh's decisions, run from the script's own text.

    The constants and functions are cut from the real script, so a change to it
    changes what runs here. The chain is stubbed: each pool's account, what the
    Safe holds unsigned and what the elector owes come from files, and every
    body built and message sent is logged instead.
    """

    NOW = 2_000_000_000
    STUBS = r"""
function date() { echo "$NOW"; }
function Pool_Read() { cat "$STATE/${1##*:}.json"; }
function Pending_To() { cat "$STATE/${1##*:}.pending" 2>/dev/null || echo 0; }
function Elector_Owes() { cat "$STATE/${1##*:}.owed" 2>/dev/null || echo 0; }
function Pool_Body() { echo "BODY $*" >> "$LOG"; echo "body-$1.boc"; }
function Pool_Send() { [[ -e $STATE/send.fail ]] && return 1; echo "SEND $1 $2 $3" >> "$LOG"; echo '{}'; }
function Get_Current_Elections_ID() { echo "$ELECTION"; }
function node_console() {
    case "$2" in
        "getconfig 34") printf '{"p34":{"utime_since":%s}}\n' "$P34" ;;
        "getconfig 17") printf '{"p17":{"min_stake_dec":"%s"}}\n' "$P17" ;;
    esac
}
CALL_CONS=node_console
"""
    # The kit the bid view links: a base env.sh and a bid script that reports
    # what it was run with, sourcing env.sh the way the real one does.
    FAKE_BID = r"""#!/usr/bin/env bash
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
source "${SCRIPT_DIR}/env.sh"
here=$(dirname "${BASH_SOURCE[0]}")
linked=no; [[ -L "$here/functions.shinc" ]] && linked=yes
echo "TAKE_PART view=$(basename "$here") base=$BASE_LOADED kit=$SCRIPT_DIR mode=$STAKE_MODE pool=$NOMINATOR_ADDR stake=$MSIG_FIX_STAKE reserve=$NOMINATOR_RESERVE fee=$NOMINATOR_OP_FEE image=$NOMINATOR_TOOLS_IMAGE linked=$linked" >> "$LOG"
"""
    FAKE_ENV = 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"\nBASE_LOADED=yes\nSTAKE_MODE=denominator\nMSIG_FIX_STAKE=0\n'

    @classmethod
    def setUpClass(cls):
        text = (KIT / "denominator_cycle.sh").read_text()
        cls.functions = text[text.index("declare -ir NANO="):text.index('case "${1:-}" in')]

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.state = self.root / "state"
        self.kit = self.root / "kit"
        self.state.mkdir()
        self.kit.mkdir()
        (self.kit / "env.sh").write_text(self.FAKE_ENV)
        (self.kit / "functions.shinc").write_text("")
        bid = self.kit / "take_part_in_elections.sh"
        bid.write_text(self.FAKE_BID)
        bid.chmod(0o700)
        self.log = self.root / "log"
        self.log.write_text("")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def pools(self, a=None, b=None, owed=None, pending=None):
        for address, account in ((POOL_A, a), (POOL_B, b)):
            if account is not None:
                (self.state / f"{address[3:]}.json").write_text(json.dumps(account))
        for address, value in (owed or {}).items():
            (self.state / f"{address[3:]}.owed").write_text(str(value))
        for address, value in (pending or {}).items():
            (self.state / f"{address[3:]}.pending").write_text(str(value))

    def run_cycle(self, command, election=0, p34=0, p17=0):
        variables = {
            "LOG": self.log, "STATE": self.state, "NOW": self.NOW, "ELECTION": election, "P34": p34, "P17": p17,
            "ELECTIONS_WORK_DIR": self.root / "elections", "INPL_ELECTIONS_WORK_DIR": "/ever-node/elections",
            "DENOMINATOR_ADDRS": f"{POOL_A} {POOL_B}", "DENOMINATOR_TOOLS_IMAGE": "local/evernode-pool-tools:test",
            "SelfScriptName": "denominator_cycle.sh", "KIT_DIR": self.kit,
        }
        program = (
            "set +u\nset -o pipefail\nexport LOG\n"
            + "".join(f"{key}={shlex.quote(str(value))}\n" for key, value in variables.items())
            + "function echoerr() { printf '%s\\n' \"$*\" >&2; }\n"
            + self.functions
            + f"Safe_addr={SAFE}\n"
            + self.STUBS
            + command + "\n"
        )
        case = self.root / "case.sh"
        case.write_text(program)
        result = subprocess.run(["bash", str(case)], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=60)
        return result, self.log.read_text().splitlines()

    def maintain(self, account, p34=0, **state):
        self.pools(a=account, **state)
        result, log = self.run_cycle(f'Pool_Maintain {POOL_A} A; echo "STATUS $?"', p34=p34)
        lines = result.stdout.strip().splitlines()
        return json.loads(lines[-2]), lines[-1], log

    # ---- maintenance ----

    def test_a_new_validator_set_is_counted_once_its_round_has_begun(self):
        line, status, log = self.maintain(pool_account(state=2, changes=0, change_time=1000), p34=2000)
        self.assertEqual((line["action"], status), ("update", "STATUS 0"))
        self.assertEqual(log, [f"BODY update {self.NOW}", f"SEND {POOL_A} {NANO} body-update.boc"])

    def test_a_set_already_counted_is_not_counted_again(self):
        line, _, log = self.maintain(pool_account(state=2, changes=1, change_time=2000), p34=2000)
        self.assertEqual(line["action"], "none")
        self.assertIn("1 new validator set(s) counted", line["reason"])
        self.assertEqual(log, [])

    def test_the_stake_is_recovered_once_the_guard_allows_and_the_elector_owes_it_all(self):
        held = 32768
        account = pool_account(state=2, changes=2, change_time=self.NOW - held - 61, held=held, sent=600)
        line, _, log = self.maintain(account, p34=self.NOW - held - 61, owed={POOL_A: 600 * NANO})
        self.assertEqual(line["action"], "recover")
        self.assertEqual(log, [f"BODY recover {self.NOW}", f"SEND {POOL_A} {2 * NANO} body-recover.boc"])

    def test_nothing_is_recovered_before_the_guard_allows(self):
        held = 32768
        account = pool_account(state=2, changes=2, change_time=self.NOW - held - 60, held=held, sent=600)
        line, _, log = self.maintain(account, p34=self.NOW - held - 60, owed={POOL_A: 600 * NANO})
        self.assertEqual((line["action"], log), ("none", []))

    def test_a_part_of_the_stake_is_left_until_the_third_set(self):
        """Recovering the election's surplus alone would book the rest as the validator's loss."""
        held = 32768
        account = pool_account(state=2, changes=2, change_time=self.NOW - held - 61, held=held, sent=600)
        line, _, log = self.maintain(account, p34=self.NOW - held - 61, owed={POOL_A: 600 * NANO - 1})
        self.assertEqual((line["action"], log), ("none", []))
        self.assertIn("elector owes", line["reason"])
        third = pool_account(state=2, changes=3, change_time=self.NOW - 10, held=held, sent=600)
        line, _, _ = self.maintain(third, p34=self.NOW, owed={POOL_A: 1})
        self.assertEqual(line["action"], "recover")
        line, _, _ = self.maintain(third, p34=self.NOW, owed={POOL_A: 0})
        self.assertEqual(line["action"], "none")

    def test_queued_withdrawals_are_paid_forty_at_a_time(self):
        for requests, limit in ((45, 40), (3, 3)):
            self.log.write_text("")
            line, _, log = self.maintain(pool_account(state=0, requests=requests))
            with self.subTest(requests=requests):
                self.assertEqual(line["action"], "pay")
                self.assertEqual(log, [f"BODY pay {limit} {self.NOW}",
                                       f"SEND {POOL_A} {NANO + limit * 150_000_000} body-pay.boc"])

    def test_an_unsigned_transaction_to_the_pool_stops_a_second_one(self):
        account = pool_account(state=2, changes=0, change_time=1000)
        line, _, log = self.maintain(account, p34=2000, pending={POOL_A: 1})
        self.assertEqual((line["action"], log), ("none", []))
        self.assertIn("evernode stake confirm", line["reason"])

    def test_an_idle_pool_with_nothing_queued_is_left_alone(self):
        line, status, log = self.maintain(pool_account(state=0))
        self.assertEqual((line["action"], status, log), ("none", "STATUS 0", []))

    def test_a_failed_send_is_reported_and_fails_the_run(self):
        (self.state / "send.fail").write_text("")
        line, status, _ = self.maintain(pool_account(state=0, requests=2))
        self.assertEqual((line["action"], status), ("pay failed", "STATUS 1"))

    # ---- bidding ----

    def bid(self, a, b, election=1_800_000_000, p17=0, owed=None):
        self.pools(a=a, b=b, owed=owed)
        result, log = self.run_cycle("Pool_Bid", election=election, p17=p17)
        return result.stdout, log

    def test_no_election_means_no_bid(self):
        out, log = self.bid(pool_account(), pool_account(), election=0)
        self.assertIn("There is no elections now", out)
        self.assertEqual(log, [])

    def test_a_pool_that_bid_in_this_election_stops_the_other(self):
        out, log = self.bid(pool_account(), pool_account(state=2, stake_at=1_800_000_000))
        self.assertIn("pool B", out)
        self.assertIn("already participated in these elections (1800000000)", out)
        self.assertEqual(log, [])

    def test_the_idle_pool_bids_through_the_kits_own_script(self):
        out, log = self.bid(pool_account(state=2, stake_at=1_700_000_000), pool_account(balance=5000.5))
        self.assertIn("pool B", out)
        self.assertEqual(len(log), 1)
        report = dict(item.split("=", 1) for item in log[0].split()[1:])
        self.assertEqual(report, {
            "view": "bid-B", "base": "yes", "kit": str(self.kit.resolve()), "mode": "nominator",
            "pool": POOL_B, "stake": "4989", "reserve": str(11 * NANO), "fee": str(2 * NANO),
            "image": "local/evernode-pool-tools:test", "linked": "yes",
        })
        self.assertFalse((self.root / "elections" / "denominator" / "bid-B").exists())

    def test_the_first_ready_pool_bids_when_both_are(self):
        _, log = self.bid(pool_account(), pool_account())
        self.assertIn(f"pool={POOL_A}", log[0])

    def test_a_pool_that_is_not_ready_says_why(self):
        cases = (
            (pool_account(requests=2), {}, 0, "2 queued withdrawal(s) to pay first"),
            (pool_account(), {POOL_A: 5}, 0, "the elector still owes it 5; recover it first"),
            (pool_account(validator_funds=100, min_validator=50), {}, 0, "below the 101 it needs"),
            (pool_account(balance=5000), {}, 10_000 * NANO, "below the 10000 the election needs"),
        )
        for account, owed, p17, reason in cases:
            for path in self.state.iterdir():
                path.unlink()
            with self.subTest(reason=reason):
                out, log = self.bid(account, pool_account(state=2, stake_at=1), p17=p17, owed=owed)
                self.assertIn("no pool bids in elections", out)
                self.assertIn(reason, out)
                self.assertEqual(log, [])


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "Requires bash and jq")
class SendTests(unittest.TestCase):
    """denominator_cycle.sh send, the whole script, in a kit whose chain functions are stubs.

    What a send waits for must be something its message changes. A deposit's
    value leaves the Safe; a withdrawal's comes back to it with the amount, so
    only the pool's books can show that it landed.
    """

    # The kit files the script sources; everything that would reach the chain is a stub.
    FUNCTIONS = r"""
function echoerr() { printf '%s\n' "$*" >&2; }
function Require_Node_Console() { return 0; }
function Get_Account_Custodians_Info() { echo "1 1"; }
function Get_Account_Info() { echo "Active 5000000000000 0"; }
function node_console() { echo state > "${2##* }"; }
function node_cli() { return 0; }
CALL_CONS=node_console
CALL_CLI=node_cli
# The tools image: 'account' prints the pool from the test's file, 'body' writes a body.
function docker() {
    [[ $1 == image ]] && return 0
    local command="${*: -1}"
    case "$command" in
        *" account "*) cat "$POOL_STATE" ;;
        *" body "*) local out; out=$(awk '{print $5}' <<< "$command"); echo body > "$WORK/${out##*/}" ;;
    esac
}
"""
    MANAGED_OPS = r"""
function Submit_And_Identify() { echo ""; }
function Require_Queue_Space() { return 0; }
function Complete_And_Await() { shift 4; echo "AWAIT $*" >> "$LOG"; }
"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        kit = self.root / "kit"
        kit.mkdir()
        shutil.copy(KIT / "denominator_cycle.sh", kit / "denominator_cycle.sh")
        (kit / "env.sh").write_text(
            'ELECTIONS_WORK_DIR="$ROOT/elections"; INPL_ELECTIONS_WORK_DIR="$ROOT/elections"\n'
            'KEYS_DIR="$ROOT/keys"; INPL_KEYS_DIR="$ROOT/keys"; VALIDATOR_NAME=v; MSG_LIFETIME=60\n'
            f'DENOMINATOR_ADDRS="{POOL_A} {POOL_B}"; DENOMINATOR_TOOLS_IMAGE=local/evernode-pool-tools:test\n'
        )
        (kit / "functions.shinc").write_text(self.FUNCTIONS)
        (kit / "managed_ops.shinc").write_text(self.MANAGED_OPS)
        (self.root / "keys").mkdir()
        (self.root / "keys" / "v.addr").write_text(SAFE)
        self.state = self.root / "pool.json"
        self.state.write_text(json.dumps(pool_account(validator_funds=1000)))
        self.log = self.root / "log"
        self.log.write_text("")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def send(self, *arguments):
        environment = dict(os.environ, ROOT=str(self.root), POOL_STATE=str(self.state), LOG=str(self.log),
                           WORK=str(self.root / "elections" / "denominator"))
        result = subprocess.run(["bash", str(self.root / "kit" / "denominator_cycle.sh"), "send", *arguments],
                                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60, env=environment)
        return result, self.log.read_text().split()

    def test_a_deposit_waits_for_the_value_to_leave_the_safe(self):
        result, awaited = self.send(POOL_A, str(1001 * NANO), "deposit-validator")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(awaited, ["AWAIT", "Effect_Account_Balance_At_Most", SAFE, str(5000 * NANO - 1)])

    def test_a_withdrawal_waits_for_the_pools_books(self):
        result, awaited = self.send(POOL_A, str(NANO), "withdraw-validator", str(100 * NANO))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(awaited, ["AWAIT", "Effect_Pool_Validator_Funds_At_Most", POOL_A, str(900 * NANO)])
        self.log.write_text("")
        _, awaited = self.send(POOL_A, str(NANO), "withdraw-validator", str(2000 * NANO))
        self.assertEqual(awaited[-1], "0")

    def test_only_this_nodes_pools_and_the_validators_own_operations(self):
        result, awaited = self.send("-1:" + "9" * 64, str(NANO), "deposit-validator")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is not one of this node's pools", result.stderr)
        result, awaited = self.send(POOL_A, str(NANO), "recover")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only deposit-validator or withdraw-validator", result.stderr)
        self.assertEqual(awaited, [])


if __name__ == "__main__":
    unittest.main()
