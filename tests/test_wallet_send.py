"""wallet send: its checks, its question, its locks and the program it runs.

docs/spec/wallet-operations.md, "Send to an address". The send stays apart from
the node's own work: it refuses while the election schedule is installed and
never pays the node's own accounts. It judges only that the Safe paid, never
what the destination did with it.
"""

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from evernode import arguments, cli, process, scripts, setup_actions, storage, ui, wallets
from support import make_node_configuration

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "evernode/vendor/ever-validator/scripts"
NANO = 10**9
SAFE = "0:" + "a" * 64  # a DePool validator's Safe
SOLO_SAFE = "-1:" + "e" * 64  # a solo or nominator validator's Safe
DEPOOL = "0:" + "b" * 64
PROXIES = ["-1:" + "c" * 64, "-1:" + "d" * 64]
ELECTOR = "-1:" + "3" * 64
CONFIG = "-1:" + "5" * 64
NOMINATOR = "-1:" + "f" * 64
DEST = "0:" + "9" * 64
MC_DEST = "-1:" + "8" * 64


def node(kind="depool", stage="ready-for-elections", **wallet):
    profile = {
        "mode": "new",
        "custodians": 3,
        "required_signatures": 2,
        "wallet_address": SAFE if kind == "depool" else SOLO_SAFE,
        "depool_address": DEPOOL if kind == "depool" else None,
    }
    profile.update(wallet)
    changes = dict(setup_stage=stage, wallet=profile, validator_type=kind)
    if kind == "nominator":
        changes["settings"] = {"NOMINATOR_ADDR": NOMINATOR}
    return make_node_configuration("validator01", **changes)


@contextlib.contextmanager
def chain(depool_status="Active", depool_state=None, proxies=PROXIES):
    """What the checks read: the elector and the config contract, the DePool's
    status (None: it could not be read) and the DePool's own state
    (DEPOOL_STATE: what reading it raises instead)."""
    with (
        patch(
            "evernode.setup_actions.nodes.fetch_special_address",
            side_effect=lambda _config, number: ELECTOR if number == 1 else CONFIG,
        ) as special,
        patch(
            "evernode.setup_actions.scripts.read_account_status",
            return_value=depool_status,
        ) as status,
        patch(
            "evernode.setup_actions.scripts.read_depool_state",
            return_value={"proxies": proxies},
            side_effect=depool_state,
        ) as state,
    ):
        yield special, status, state


def subcommands(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


class ParserTests(unittest.TestCase):
    def parse(self, *argv):
        return arguments.build_argument_parser().parse_args(
            arguments.normalize_address_arguments(list(argv))
        )

    def test_a_masterchain_address_can_follow_a_space(self):
        args = self.parse(
            "wallet", "send", "-n", "validator01", "--address", MC_DEST, "--amount", "5"
        )
        self.assertEqual(
            (args.group, args.action, args.name, args.address, args.amount),
            ("wallet", "send", "validator01", MC_DEST, 5),
        )
        self.assertFalse(args.no_bounce)
        self.assertFalse(args.yes)

    def test_no_bounce_and_yes_are_switches(self):
        args = self.parse(
            "wallet", "send", "-n", "validator01", "--address", DEST,
            "--amount", "1", "--no-bounce", "--yes",
        )
        self.assertTrue(args.no_bounce)
        self.assertTrue(args.yes)

    def test_the_name_the_address_and_the_amount_are_required(self):
        full = ["wallet", "send", "-n", "validator01", "--address", DEST, "--amount", "1"]
        for missing in ("-n", "--address", "--amount"):
            argv = list(full)
            del argv[argv.index(missing): argv.index(missing) + 2]
            with self.subTest(missing=missing), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                self.parse(*argv)

    def test_the_amount_is_whole_tokens(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parse("wallet", "send", "-n", "v", "--address", DEST, "--amount", "1.5")

    def test_only_wallet_send_has_an_address_option(self):
        """Joining '--address -1:...' into one argument happens before any
        parser sees the command line, so no other command may own the name."""
        owners = []
        for group, parser in subcommands(arguments.build_argument_parser()).items():
            for action, sub in (subcommands(parser) or {group: parser}).items():
                if any("--address" in item.option_strings for item in sub._actions):
                    owners.append((group, action))
        for parser in (
            arguments.build_vote_option_parser(),
            *(arguments.build_vote_schedule_parser(c) for c in ("join", "leave", "history")),
        ):
            if any("--address" in item.option_strings for item in parser._actions):
                owners.append(("vote", parser.prog))
        self.assertEqual(owners, [("wallet", "send")])

    def test_only_an_address_value_is_joined(self):
        self.assertEqual(
            arguments.normalize_address_arguments(["--address", MC_DEST]),
            [f"--address={MC_DEST}"],
        )
        for argv in (
            ["wallet", "send", "--address", DEST],
            ["wallet", "send", "--address", "-5"],
            ["node", "lsync", "--from", "a", "--to", "b"],
        ):
            with self.subTest(argv=argv):
                self.assertEqual(arguments.normalize_address_arguments(argv), argv)


class PlanTests(unittest.TestCase):
    """The checks that read nothing, made before the question and again
    before the send."""

    def test_a_safe_not_on_chain_yet_is_refused_with_the_next_step(self):
        cases = {
            ("new", "node-started"): "evernode wallet create -n validator01",
            ("import", "node-started"): "evernode wallet recover -n validator01",
            ("new", "wallet-created"): "evernode wallet deploy -n validator01",
            ("new", "preparing"): "evernode node status -n validator01",
        }
        for (mode, stage), command in cases.items():
            with self.subTest(mode=mode, stage=stage), self.assertRaisesRegex(
                process.OperationError, "not on chain yet; run: " + re.escape(command)
            ):
                wallets.plan_send(node(stage=stage, mode=mode), DEST, 1)

    def test_every_stage_from_a_deployed_or_recovered_safe_can_send(self):
        for stage, mode in (
            ("wallet-deployed", "new"), ("depool-prepared", "new"),
            ("depool-deployed", "new"), ("wallet-recovered", "import"),
            ("wallet-verified", "import"), ("imported", "import"),
            ("nominator-prepared", "new"), ("nominator-deployed", "new"),
            ("nominator-verified", "new"), ("ready-for-elections", "new"),
        ):
            with self.subTest(stage=stage):
                plan = wallets.plan_send(node(stage=stage, mode=mode), DEST, 1)
                self.assertEqual(plan, {"wallet_address": SAFE, "address": DEST})

    def test_the_stages_without_a_safe_are_real_stages(self):
        text = (ROOT / "docs/spec/state.md").read_text()
        table = text[text.index("### Setup stages"):]
        table = table[: table.index("\n## ")]
        stages = set(re.findall(r"^\| `([a-z-]+)` ", table, re.MULTILINE))
        self.assertIn("ready-for-elections", stages)
        self.assertLessEqual(set(wallets.STAGES_WITHOUT_SAFE), stages)

    def test_addresses_in_the_masterchain_and_workchain_0_only(self):
        for address in (DEST, MC_DEST, "0:" + "A" * 64, "-1:" + "Ab" * 32):
            with self.subTest(address=address):
                plan = wallets.plan_send(node(), address, 1)
                self.assertEqual(plan["address"], address.lower())
        for address in (
            "5:" + "9" * 64, "1:" + "9" * 64, "-2:" + "9" * 64, "00:" + "9" * 64,
            "0:" + "9" * 63, "0:" + "9" * 65, "0:" + "g" * 64, "9" * 64, "",
            " 0:" + "9" * 64, "0:" + "9" * 64 + "\n",
        ):
            with self.subTest(address=address), self.assertRaisesRegex(
                process.OperationError, "-1: or 0: followed by 64 hex digits"
            ):
                wallets.plan_send(node(), address, 1)

    def test_whole_tokens_from_1_to_the_cap(self):
        for amount in (1, 100, wallets.SEND_MAX_TOKENS):
            with self.subTest(amount=amount):
                wallets.plan_send(node(), DEST, amount)
        for amount in (0, -1, wallets.SEND_MAX_TOKENS + 1):
            with self.subTest(amount=amount), self.assertRaisesRegex(
                process.OperationError, "whole tokens, from 1 to"
            ):
                wallets.plan_send(node(), DEST, amount)

    def test_the_cap_with_the_fee_margin_fits_bash_arithmetic(self):
        self.assertLess(wallets.SEND_MAX_TOKENS * NANO + NANO, 2**63)

    def test_the_safe_address_comes_back_in_lower_case(self):
        plan = wallets.plan_send(node(wallet_address="0:" + "A" * 64), DEST, 1)
        self.assertEqual(plan["wallet_address"], SAFE)

    def test_wallet_send_checks_again_and_sends_nanotokens(self):
        sent = {"transaction_id": "7", "wallet_balance_before_nanotokens": "500",
                "wallet_balance_after_nanotokens": "390"}
        with patch("evernode.wallets.scripts.send_from_safe", return_value=sent) as send:
            result = wallets.wallet_send(
                None, node(), address="0:" + "9" * 64, amount=100, bounce=False
            )
            send.assert_called_once_with(None, node(), SAFE, DEST, 100 * NANO, False)
            self.assertEqual(result, {
                "wallet_address": SAFE, "address": DEST, "tokens": 100, "bounce": False,
                **sent,
                "note": "The Safe has sent it. An explorer shows whether the address kept "
                "it or it bounced back.",
                "next": "evernode election start -n validator01",
            })
            send.reset_mock()
            with self.assertRaises(process.OperationError):
                wallets.wallet_send(None, node(), address=DEST, amount=0, bounce=True)
            send.assert_not_called()

    def test_the_next_step_of_a_node_in_setup_is_its_setup_step(self):
        with patch("evernode.wallets.scripts.send_from_safe", return_value={}):
            result = wallets.wallet_send(
                None, node(stage="depool-prepared"), address=DEST, amount=1, bounce=True
            )
        self.assertEqual(result["next"], "evernode depool deploy -n validator01")


class OwnAccountsTests(unittest.TestCase):
    """Rule 3: the send never pays what only the node's own commands pay."""

    def accounts(self, config, **reads):
        with chain(**reads) as (special, status, state):
            return setup_actions._own_accounts(None, config), status, state

    def test_a_solo_node_never_pays_the_elector_the_config_contract_or_its_safe(self):
        accounts, status, state = self.accounts(node("solo"))
        self.assertEqual(accounts, {
            ELECTOR: "the elector",
            CONFIG: "the config contract",
            SOLO_SAFE: "this node's own Safe",
        })
        status.assert_not_called()
        state.assert_not_called()

    def test_a_nominator_node_adds_its_contract_once_it_has_an_address(self):
        accounts, *_ = self.accounts(node("nominator"))
        self.assertEqual(accounts[NOMINATOR], "this node's nominator contract")
        before = node("nominator", stage="wallet-deployed")
        before["settings"] = {}
        accounts, *_ = self.accounts(before)
        self.assertEqual(set(accounts), {ELECTOR, CONFIG, SOLO_SAFE})

    def test_a_depool_on_chain_adds_itself_and_its_proxies(self):
        accounts, _, state = self.accounts(node())
        self.assertEqual(accounts, {
            ELECTOR: "the elector",
            CONFIG: "the config contract",
            DEPOOL: "this node's DePool",
            PROXIES[0]: "a proxy of this node's DePool",
            PROXIES[1]: "a proxy of this node's DePool",
            SAFE: "this node's own Safe",
        })
        state.assert_called_once_with(None, node(), DEPOOL, SAFE)

    def test_a_depool_not_on_chain_yet_has_no_proxies_to_read(self):
        for status_value in ("Nonexist", "Uninit"):
            with self.subTest(status=status_value):
                accounts, status, state = self.accounts(
                    node(stage="depool-prepared"), depool_status=status_value
                )
                self.assertEqual(accounts, {
                    ELECTOR: "the elector",
                    CONFIG: "the config contract",
                    DEPOOL: "this node's DePool",
                    SAFE: "this node's own Safe",
                })
                status.assert_called_once_with(None, node(stage="depool-prepared"), DEPOOL)
                state.assert_not_called()

    def test_a_depool_node_before_depool_prepare_has_no_depool_yet(self):
        accounts, status, state = self.accounts(
            node(stage="wallet-deployed", depool_address=None)
        )
        self.assertEqual(set(accounts), {ELECTOR, CONFIG, SAFE})
        status.assert_not_called()
        state.assert_not_called()

    def test_the_chain_not_the_stage_decides_whether_proxies_are_read(self):
        """An imported DePool is on chain at wallet-recovered, and an
        interrupted depool deploy can leave one on chain at depool-prepared."""
        for stage, mode in (
            ("wallet-recovered", "import"), ("wallet-verified", "import"),
            ("depool-prepared", "new"),
        ):
            with self.subTest(stage=stage):
                accounts, _, state = self.accounts(node(stage=stage, mode=mode))
                self.assertIn(PROXIES[0], accounts)
                state.assert_called_once()

    def test_a_depool_that_cannot_be_read_refuses_the_send(self):
        for status_value in (None, "Frozen", "Active"):
            with self.subTest(status=status_value), self.assertRaisesRegex(
                process.OperationError, "unreadable"
            ):
                self.accounts(
                    node(), depool_status=status_value,
                    depool_state=process.OperationError("unreadable"),
                )

    def test_an_unreadable_special_contract_refuses_the_send(self):
        with patch(
            "evernode.setup_actions.nodes.fetch_special_address",
            side_effect=process.OperationError("did not report configuration parameter 1"),
        ):
            for config in (node("solo"), node(stage="wallet-deployed", depool_address=None)):
                with self.subTest(kind=config["validator_type"]), \
                        self.assertRaisesRegex(process.OperationError, "parameter 1"):
                    setup_actions._own_accounts(None, config)

    def test_addresses_are_compared_in_lower_case(self):
        accounts, *_ = self.accounts(
            node(wallet_address="0:" + "A" * 64, depool_address="0:" + "B" * 64),
            depool_status="Uninit",
        )
        self.assertIn(SAFE, accounts)
        self.assertIn(DEPOOL, accounts)


class CommandTests(unittest.TestCase):
    """The command end to end, with the chain and the program stubbed."""

    SENT = {
        "transaction_id": "7",
        "wallet_balance_before_nanotokens": str(500 * NANO),
        "wallet_balance_after_nanotokens": str(399 * NANO),
    }

    def run_send(self, *flags, config=None, schedule=False, lock=None, send=None):
        events = []
        real_confirm = ui.confirm_operation
        with (
            patch("evernode.cli.require_root_privileges"),
            patch("evernode.cli.storage.NodeStateStore") as store,
            patch(
                "evernode.setup_actions.nodes.fetch_node_statistics",
                return_value={"sync_status": "synchronization_finished",
                              "timediff": 1, "shards_timediff": 1},
            ),
            patch("evernode.setup_actions.elections.node_election_cron_path") as cron,
            chain() as (special, _status, _state),
            patch(
                "evernode.setup_actions.ui.confirm_operation",
                side_effect=lambda yes, text: events.append(("asked", text))
                or real_confirm(yes, text),
            ),
            patch(
                "evernode.setup_actions.elections.hold_election_lock",
                side_effect=lock
                or (lambda _store, name: events.append(("locked", name))
                    or contextlib.nullcontext()),
            ),
            patch(
                "evernode.wallets.scripts.send_from_safe",
                side_effect=send
                or (lambda *args: events.append(("sent", args[2:])) or dict(self.SENT)),
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            cron.return_value.exists.return_value = schedule
            store.return_value.load_node_config.return_value = config or node()
            code = cli.main(["wallet", "send", "-n", "validator01", *flags])
        self.store = store.return_value
        self.special = special
        return code, events, output.getvalue(), errors.getvalue()

    def test_a_send_is_asked_about_then_locked_then_sent(self):
        code, events, output, errors = self.run_send(
            "--address", DEST, "--amount", "100", "--yes"
        )
        self.assertEqual(code, 0, errors)
        self.assertEqual([event[0] for event in events], ["asked", "locked", "sent"])
        self.assertEqual(events[1], ("locked", "validator01"))
        self.assertEqual(events[2], ("sent", (SAFE, DEST, 100 * NANO, True)))
        result = json.loads(output)
        self.assertEqual(
            {key: result[key] for key in ("wallet_address", "address", "tokens", "bounce")},
            {"wallet_address": SAFE, "address": DEST, "tokens": 100, "bounce": True},
        )
        self.assertEqual(result["transaction_id"], "7")
        self.assertEqual(result["next"], "evernode election start -n validator01")
        self.store.save_node_config.assert_called_once_with(node())

    def test_the_question_names_the_amount_both_addresses_and_bounce(self):
        _, events, _, _ = self.run_send("--address", MC_DEST, "--amount", "100", "--yes")
        self.assertEqual(
            events[0][1],
            f"Send 100 tokens from validator01's Safe {SAFE} to {MC_DEST}? Bounce is on: "
            "an address with no contract, or one that refuses the tokens, sends them "
            "back minus fees.",
        )

    def test_no_bounce_is_sent_without_bounce_and_says_so(self):
        code, events, _, errors = self.run_send(
            "--address", DEST, "--amount", "2", "--no-bounce", "--yes"
        )
        self.assertEqual(code, 0, errors)
        self.assertTrue(events[0][1].endswith(
            "Bounce is off: the tokens stay at the address even if it has no contract."
        ))
        self.assertEqual(events[-1], ("sent", (SAFE, DEST, 2 * NANO, False)))

    def test_without_a_terminal_or_yes_nothing_is_sent(self):
        code, events, _, errors = self.run_send("--address", DEST, "--amount", "1")
        self.assertEqual(code, 1)
        self.assertIn("Cancelled", errors)
        self.assertEqual([event[0] for event in events], ["asked"])

    def test_an_installed_schedule_refuses_before_the_question(self):
        code, events, _, errors = self.run_send(
            "--address", DEST, "--amount", "1", "--yes", schedule=True
        )
        self.assertEqual(code, 1)
        self.assertIn("validator01's election schedule is installed", errors)
        self.assertIn("evernode election stop -n validator01", errors)
        self.assertIn("evernode election start -n validator01", errors)
        self.assertEqual(events, [])

    def test_the_nodes_own_accounts_are_refused_before_the_question(self):
        cases = [
            (node(), ELECTOR, "the elector"),
            (node(), CONFIG, "the config contract"),
            (node(), DEPOOL, "this node's DePool"),
            (node(), "0:" + "B" * 64, "this node's DePool"),
            (node(), PROXIES[0], "a proxy of this node's DePool"),
            (node(), PROXIES[1], "a proxy of this node's DePool"),
            (node(), SAFE, "this node's own Safe"),
            (node("solo"), SOLO_SAFE, "this node's own Safe"),
            (node("nominator"), NOMINATOR, "this node's nominator contract"),
        ]
        for config, address, whose in cases:
            with self.subTest(address=address, kind=config["validator_type"]):
                code, events, _, errors = self.run_send(
                    "--address", address, "--amount", "1", "--yes", config=config
                )
                self.assertEqual(code, 1)
                self.assertIn(f"wallet send never pays {whose} ({address.lower()})", errors)
                self.assertEqual(events, [])

    def test_a_safe_not_on_chain_is_refused_before_anything_is_read(self):
        code, events, _, errors = self.run_send(
            "--address", DEST, "--amount", "1", "--yes", config=node(stage="wallet-created")
        )
        self.assertEqual(code, 1)
        self.assertIn("not on chain yet; run: evernode wallet deploy -n validator01", errors)
        self.assertEqual(events, [])
        self.special.assert_not_called()

    def test_a_bad_address_or_amount_is_refused_before_anything_is_read(self):
        for flags in (("--address", "5:" + "9" * 64, "--amount", "1"),
                      ("--address", DEST, "--amount", "0")):
            with self.subTest(flags=flags):
                code, events, _, _ = self.run_send(*flags, "--yes")
                self.assertEqual(code, 1)
                self.assertEqual(events, [])
                self.special.assert_not_called()

    def test_a_running_election_blocks_the_send(self):
        def held(_store, _name):
            raise process.OperationError(
                "A scheduled election run holds validator01's election lock; retry when it finishes"
            )

        code, events, _, errors = self.run_send(
            "--address", DEST, "--amount", "1", "--yes", lock=held
        )
        self.assertEqual(code, 1)
        self.assertIn("election lock", errors)
        self.assertNotIn("sent", [event[0] for event in events])

    def test_a_send_that_fails_reports_and_saves_nothing(self):
        def failed(*_):
            raise process.OperationError("The send did not complete")

        code, _, _, errors = self.run_send(
            "--address", DEST, "--amount", "1", "--yes", send=failed
        )
        self.assertEqual(code, 1)
        self.assertIn("The send did not complete", errors)
        self.store.save_node_config.assert_not_called()

    def test_it_holds_the_election_lock_like_every_use_of_the_safe(self):
        self.assertIn(("wallet", "send"), setup_actions.ELECTION_LOCKED_ACTIONS)

    def test_other_actions_keep_their_own_questions(self):
        """The question for a send is a branch of its own; the others still
        get theirs."""
        args = argparse.Namespace(group="depool", action="replenish", amount=5)
        self.assertEqual(
            setup_actions._confirmation_question(args, node()),
            "Send 5 tokens from validator01's Safe to its DePool balance?",
        )
        with patch("evernode.setup_actions._review_transaction", return_value="q") as review, \
                patch("evernode.setup_actions._confirmation_question",
                      return_value="q") as question, \
                patch("evernode.setup_actions._review_send") as send, \
                patch("evernode.setup_actions._require_setup_node", return_value=node()), \
                patch("evernode.setup_actions.ui.confirm_operation",
                      side_effect=process.OperationError("stop here")):
            store = unittest.mock.MagicMock()
            for group in ("depool", "stake"):
                with self.subTest(group=group), self.assertRaises(process.OperationError):
                    setup_actions.handle_setup_action(store, argparse.Namespace(
                        name="validator01", group=group, action="confirm",
                        transaction_id="1", force=False, yes=True))
            with self.assertRaises(process.OperationError):
                setup_actions.handle_setup_action(store, argparse.Namespace(
                    name="validator01", group="depool", action="replenish",
                    amount=5, yes=True))
            self.assertEqual(review.call_count, 2)
            question.assert_called_once()
            send.assert_not_called()


# The kit's lower half, stubbed: the chain as a Safe that signs and pays.
PROGRAM_STUBS = r"""
KEYS_DIR="$TEST_ROOT"; INPL_KEYS_DIR="$TEST_ROOT"; VALIDATOR_NAME=validator
INPL_ELECTIONS_WORK_DIR="$TEST_ROOT"; ELECTIONS_WORK_DIR="$TEST_ROOT"; SIG_ID_OPTION=""
INPL_SafeC_Wallet_ABI=safe.abi; SafeC_Wallet_ABI="$TEST_ROOT/safe.abi"; : >"$SafeC_Wallet_ABI"
CALL_BC=bc; CALL_CLI=fake_cli; CALL_CONS=fake_cons; MSG_LIFETIME=1; LC_Send_MSG_Timeout=1
EFFECT_POLL_INTERVAL=1; EFFECT_WAIT_MAX=4; EFFECT_MIN_WAIT=1
echoerr() { echo "$*" >&2; }
# The node's console, read for the storage prices: TEST_P18 is its answer, "fail" none.
fake_cons() {
    echo "$*" >>"$TEST_ROOT/console"
    [[ $TEST_P18 != fail ]] || return 1
    printf '%s\n' "$TEST_P18"
}
fake_cli() {
    case "$*" in
        message\ *) [[ -z $TEST_BUILD_FAILS ]] || return 1
            printf '%s\n' "$*" >>"$TEST_ROOT/built";;
        *' getParameters '*) echo '{"expirationTime":"3600"}';;
        *' getCustodians '*) printf '%s\n' "$TEST_CUSTODIANS";;
        *) echo '{}';;
    esac
}
Get_SC_current_state() { echo "written StateInit of account"; }
Get_Account_Custodians_Info() { [[ $TEST_SIGNS != fail ]] || return 1; echo "$TEST_SIGNS"; }
# Every account read is recorded: the send reads only its own Safe. From read
# number TEST_FAIL_FROM on, the chain does not answer.
Get_Account_Info() {
    echo "$1" >>"$TEST_ROOT/read"
    if [[ $1 != "$EVERNODE_WALLET" || -n $TEST_UNREADABLE ]] || [[ -n $TEST_FAIL_FROM \
            && $(grep -c . "$TEST_ROOT/read") -ge $TEST_FAIL_FROM ]]; then
        echo "None 0 0"; return 1
    fi
    if [[ -f $TEST_ROOT/executed ]]; then echo "Active $TEST_AFTER $TEST_LAST_PAID"
    else echo "$TEST_STATUS $TEST_BEFORE $TEST_LAST_PAID"; fi
}
# The staged entries, plus this run's own once it has arrived and until it executes.
Get_MSIG_Trans_List() {
    local own='[]'
    if grep -qs submitted "$TEST_ROOT/trace" && [[ ! -f $TEST_ROOT/executed ]]; then
        own=$(jq -c --arg id "$TEST_TRANS_ID" '[. + {id: $id}]' "$TEST_ROOT/entry.json")
    fi
    jq -c --argjson own "$own" '{transactions: (. + $own)}' "$TEST_ROOT/pending.json"
}
# A message that is lost reaches nobody. With one signature the submit is the transfer.
Send_File_To_BC() {
    if [[ -n $TEST_LOST ]]; then echo lost >>"$TEST_ROOT/trace"; echo success; return; fi
    echo submitted >>"$TEST_ROOT/trace"
    [[ $TEST_SIGNS == "1 1" ]] && touch "$TEST_ROOT/executed"
    echo success
}
Send_MSIG_Trans_Confirmation() {
    echo "signed:$1:$3" >>"$TEST_ROOT/trace"
    [[ -n $TEST_CONFIRM_LOST ]] || touch "$TEST_ROOT/executed"
    echo success
}
"""
# Mainnet's masterchain storage prices (p18), in force since long ago.
MAINNET_P18 = {"p18": [
    {"utime_since": 0, "mc_bit_price_ps_dec": "1000", "mc_cell_price_ps_dec": "500000"}
]}
# Twice those: a Safe counted at 50,000 bits and 100 cells then costs 0.264 a day.
DOUBLE_P18 = {"p18": [
    {"utime_since": 0, "mc_bit_price_ps_dec": "2000", "mc_cell_price_ps_dec": "1000000"}
]}

CUSTODIAN_KEYS = ["1" * 63 + "a", "2" * 63 + "b", "3" * 63 + "c"]
EMPTY_BODY = "te6ccgEBAQEAAgAAAA=="


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("bc"),
    "Requires bash, jq and bc",
)
class ProgramTests(unittest.TestCase):
    """SAFE_SEND against the kit's real managed_ops.shinc."""

    def send(self, *, signs="3 2", value=100 * NANO, bounce=True, before=500 * NANO,
             after=None, status="Active", pending=(), lost=False, unreadable=False,
             dest=DEST, wallet=SAFE, environment=None):
        now = int(time.time())
        trans_id = str((now << 32) | 2)
        entry = {
            "creator": str(int(CUSTODIAN_KEYS[0], 16)), "dest": dest, "value": str(value),
            "payload": EMPTY_BODY, "bounce": bounce, "sendFlags": "3",
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
            (root / "functions.shinc").write_text(PROGRAM_STUBS)
            for index, key in enumerate(CUSTODIAN_KEYS, 1):
                (root / f"validator_{index}.keys.json").write_text(
                    json.dumps({"public": key, "secret": f"{index:02d}" * 32})
                )
            (root / "entry.json").write_text(json.dumps(entry))
            (root / "pending.json").write_text(json.dumps(list(pending)))
            env = dict(
                os.environ, TEST_ROOT=str(root), TEST_TRANS_ID=trans_id,
                TEST_CUSTODIANS=json.dumps(custodians), TEST_SIGNS=signs,
                TEST_BEFORE=str(before),
                TEST_AFTER=str(before - value - NANO // 50 if after is None else after),
                TEST_STATUS=status, TEST_LOST="1" if lost else "",
                TEST_UNREADABLE="1" if unreadable else "",
                TEST_LAST_PAID=str(now), TEST_P18=json.dumps(MAINNET_P18),
                EVERNODE_WALLET=wallet, SEND_DEST=dest, SEND_VALUE=str(value),
                SEND_BOUNCE="true" if bounce else "false",
            )
            env.update(environment or {})
            done = subprocess.run(
                ["bash", "-c", scripts.SAFE_SEND],
                cwd=root, capture_output=True, text=True, timeout=120, env=env,
            )
            files = {
                name: (root / name).read_text() if (root / name).exists() else ""
                for name in ("trace", "built", "read", "console")
            }
        files["trace"] = files["trace"].replace(trans_id, "<id>")
        return done, files, trans_id

    def result(self, done):
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_one_signature_the_submit_is_the_transfer(self):
        done, files, _ = self.send(signs="1 1")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(files["trace"], "submitted\n")
        self.assertEqual(self.result(done), {
            "transaction_id": "",
            "wallet_balance_before_nanotokens": str(500 * NANO),
            "wallet_balance_after_nanotokens": str(500 * NANO - 100 * NANO - NANO // 50),
        })

    def test_several_signatures_are_signed_through_to_execution(self):
        done, files, trans_id = self.send(signs="3 2")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(files["trace"], "submitted\nsigned:<id>:validator_2.keys.json\n")
        self.assertEqual(self.result(done)["transaction_id"], trans_id)

    def test_a_transfer_that_bounced_back_still_counts_as_sent(self):
        """The bounce returns the value but not the fees, so the Safe stays
        below where it was."""
        for signs in ("1 1", "3 2"):
            with self.subTest(signs=signs):
                done, _, _ = self.send(signs=signs, after=500 * NANO - NANO // 50)
                self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_safe_that_paid_nothing_is_a_send_that_failed(self):
        done, files, _ = self.send(signs="1 1", after=500 * NANO)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("did not take effect", done.stderr)
        # With one signature nothing is left waiting to warn about.
        self.assertNotIn("waiting for signatures", done.stderr)

    def test_a_lost_message_is_reported_after_its_lifetime(self):
        for signs, said in (
            ("1 1", "did not take effect"),
            ("3 2", "nothing was transferred. A transfer left waiting for signatures "
                    "expires an hour after it was made: run the send again for a new one, "
                    "and do not sign the waiting one with depool confirm or stake confirm "
                    "--force, which would send it too."),
        ):
            with self.subTest(signs=signs):
                done, files, _ = self.send(signs=signs, lost=True)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn(said, done.stderr)
                self.assertNotIn("signed", files["trace"])

    def test_the_transfer_carries_the_destination_the_value_and_the_bounce_flag(self):
        for bounce in (True, False):
            with self.subTest(bounce=bounce):
                done, files, _ = self.send(signs="1 1", bounce=bounce, dest=MC_DEST)
                self.assertEqual(done.returncode, 0, done.stderr)
                built = files["built"].strip()
                self.assertEqual(built.count("\n"), 0)
                self.assertIn(" --lifetime 1 ", built)
                self.assertIn("/validator_1.keys.json ", built)
                self.assertIn(f" --abi safe.abi {SAFE} submitTransaction ", built)
                request = json.loads(built[built.index("{"):])
                self.assertEqual(request, {
                    "dest": MC_DEST, "value": 100 * NANO, "bounce": bounce,
                    "allBalance": False, "payload": "",
                })

    def test_a_pending_transfer_is_identified_by_its_bounce_flag(self):
        """Submit_And_Identify matches the flag the transfer was built with."""
        done, files, _ = self.send(signs="3 2", bounce=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("signed:<id>", files["trace"])

    def test_only_the_safe_is_read_never_the_destination(self):
        done, files, _ = self.send(signs="3 2")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(set(files["read"].split()), {SAFE})

    def test_the_safe_keeps_back_1_token_per_signature(self):
        """Each signature is a message the Safe pays for before the transfer."""
        for signs, kept in (("1 1", 1), ("3 2", 2)):
            with self.subTest(signs=signs):
                done, files, _ = self.send(signs=signs, before=100 * NANO + kept * NANO - 1)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn(
                    f"the Safe holds {100 + kept - 1}.999 tokens and keeps back {kept}.000: "
                    f"1 token for each of the {kept} signature(s) it needs. It can send at "
                    "most 99 tokens now, so nothing was sent.",
                    done.stderr,
                )
                self.assertEqual((files["built"], files["trace"]), ("", ""))
                done, _, _ = self.send(signs=signs, before=100 * NANO + kept * NANO)
                self.assertEqual(done.returncode, 0, done.stderr)

    def owes(self, days, **case):
        """A masterchain Safe whose last transaction was DAYS ago."""
        environment = {"TEST_LAST_PAID": str(int(time.time()) - int(days * 86400))}
        environment.update(case.pop("environment", {}))
        return self.send(signs="1 1", wallet=SOLO_SAFE, environment=environment, **case)

    def test_a_masterchain_safe_keeps_back_at_least_0_2_token_a_day(self):
        """Ten days at mainnet's prices: a Safe counted as 50,000 bits and 100
        cells would cost 1.318 tokens, less than 0.2 a day, so 2 are kept."""
        done, files, _ = self.owes(10, before=100 * NANO + 3 * NANO - 1)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(
            "keeps back 3.000: 1 token for each of the 1 signature(s) it needs and "
            "2.000 for the storage it owes since its last transaction. It can send at "
            "most 99 tokens now, so nothing was sent.",
            done.stderr,
        )
        self.assertEqual(files["built"], "")
        self.assertIn("-jc getconfig 18", files["console"])
        # The seconds the program itself takes add a little storage.
        done, _, _ = self.owes(10, before=100 * NANO + 3 * NANO + NANO // 100)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_prices_above_0_2_token_a_day_are_what_counts(self):
        """At twice mainnet's prices the same ten days cost 2.636 tokens."""
        owed = (10 * 86400 * (50000 * 2000 + 100 * 1000000) + 65535) // 65536
        environment = {"TEST_P18": json.dumps(DOUBLE_P18)}
        done, _, _ = self.owes(
            10, before=100 * NANO + NANO + owed - 1, environment=environment
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(
            "keeps back 3.636: 1 token for each of the 1 signature(s) it needs and "
            "2.636 for the storage it owes since its last transaction. It can send at "
            "most 99 tokens now",
            done.stderr,
        )
        done, _, _ = self.owes(
            10, before=100 * NANO + NANO + owed + NANO // 100, environment=environment
        )
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_storage_under_a_thousandth_of_a_token_is_not_named(self):
        """A Safe that transacted a minute ago owes almost nothing: kept back,
        but not shown as 0.000."""
        done, _, _ = self.owes(1 / 1440, before=100 * NANO + NANO)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(
            "keeps back 1.000: 1 token for each of the 1 signature(s) it needs. It can "
            "send at most 99 tokens now",
            done.stderr,
        )

    def test_without_prices_the_storage_is_counted_at_0_2_token_a_day(self):
        done, _, _ = self.owes(
            10, before=100 * NANO + 3 * NANO - 1, environment={"TEST_P18": "fail"}
        )
        self.assertIn("and 2.000 for the storage it owes", done.stderr)
        self.assertIn("It can send at most 99 tokens now", done.stderr)

    def test_the_floor_holds_when_the_prices_are_lower(self):
        cheap = {"p18": [{"utime_since": 0, "mc_bit_price_ps_dec": "1",
                          "mc_cell_price_ps_dec": "500"}]}
        done, _, _ = self.owes(
            10, before=100 * NANO + 3 * NANO - 1,
            environment={"TEST_P18": json.dumps(cheap)},
        )
        self.assertIn("and 2.000 for the storage it owes", done.stderr)

    def test_a_price_dated_in_the_future_is_not_counted(self):
        p18 = {"p18": DOUBLE_P18["p18"] + [
            {"utime_since": int(time.time()) + 3600, "mc_bit_price_ps_dec": "1000000",
             "mc_cell_price_ps_dec": "500000000"},
        ]}
        owed = (10 * 86400 * (50000 * 2000 + 100 * 1000000) + 65535) // 65536
        done, _, _ = self.owes(
            10, before=100 * NANO + NANO + owed - 1, environment={"TEST_P18": json.dumps(p18)}
        )
        self.assertIn("and 2.636 for the storage it owes", done.stderr)

    def test_a_safe_that_owes_more_than_it_holds_can_send_nothing(self):
        done, files, _ = self.owes(5000, before=500 * NANO)
        self.assertIn("It cannot send a whole token now, so nothing was sent.", done.stderr)
        self.assertEqual(files["built"], "")

    def test_an_unknown_last_payment_sends_nothing(self):
        for last_paid in ("0", "null", ""):
            with self.subTest(last_paid=last_paid):
                done, files, _ = self.send(
                    signs="1 1", wallet=SOLO_SAFE, environment={"TEST_LAST_PAID": last_paid}
                )
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("could not read when the Safe last paid for its storage", done.stderr)
                self.assertEqual(files["built"], "")

    def test_a_workchain_0_safe_keeps_back_no_storage(self):
        """Its storage costs about a thousand times less: not counted."""
        done, files, _ = self.send(
            signs="1 1", before=100 * NANO + NANO, environment={"TEST_LAST_PAID": "1"}
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(files["console"], "")

    def test_a_transfer_left_waiting_is_named_with_what_not_to_do(self):
        """Signing fails: the entry stays at 1 of 2, and a rerun plus a forced
        confirmation would pay twice."""
        done, files, _ = self.send(signs="3 2", environment={"TEST_CONFIRM_LOST": "1"})
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("signed:<id>", files["trace"])
        self.assertIn(
            "do not sign the waiting one with depool confirm or stake confirm --force",
            done.stderr,
        )

    def test_a_safe_that_cannot_be_read_or_is_not_active_sends_nothing(self):
        for case, said in (
            (dict(unreadable=True), "could not read the Safe"),
            (dict(status="Frozen"), "reads as Frozen"),
            (dict(status="Uninit"), "reads as Uninit"),
        ):
            with self.subTest(case=case):
                done, files, _ = self.send(**case)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn(said, done.stderr)
                self.assertEqual((files["built"], files["trace"]), ("", ""))

    def test_a_safe_whose_signatures_cannot_be_read_sends_nothing(self):
        """Without the threshold it is unknown whether the submit is the transfer."""
        for signs, said in (
            ("fail", "could not read the wallet's custodians"),
            ("3 0", "no usable signature threshold (3 0)"),
            ("3 x", "no usable signature threshold (3 x)"),
            ("", "no usable signature threshold ()"),
        ):
            with self.subTest(signs=signs):
                done, files, _ = self.send(signs=signs)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn(said, done.stderr)
                self.assertEqual((files["built"], files["read"]), ("", ""))

    def test_a_transfer_that_cannot_be_built_sends_nothing(self):
        done, files, _ = self.send(environment={"TEST_BUILD_FAILS": "1"})
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("could not build the Safe transaction", done.stderr)
        self.assertEqual(files["trace"], "")

    def test_a_full_queue_sends_nothing(self):
        mine = str(int(CUSTODIAN_KEYS[0], 16))
        pending = [{"id": str(i), "creator": mine, "dest": "0:" + "7" * 64} for i in range(5)]
        done, files, _ = self.send(signs="3 2", pending=pending)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("nothing was sent", done.stderr)
        self.assertEqual(files["trace"], "")

    def test_one_signature_skips_the_queue(self):
        mine = str(int(CUSTODIAN_KEYS[0], 16))
        pending = [{"id": str(i), "creator": mine, "dest": "0:" + "7" * 64} for i in range(5)]
        done, _, _ = self.send(signs="1 1", pending=pending)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("queue slots", done.stdout)

    def test_inputs_it_cannot_use_send_nothing(self):
        for name, value in (
            ("SEND_DEST", "5:" + "9" * 64), ("SEND_DEST", ""), ("SEND_VALUE", "0"),
            ("SEND_VALUE", "1.5"), ("SEND_BOUNCE", "yes"), ("SEND_BOUNCE", ""),
        ):
            with self.subTest(name=name, value=value):
                done, files, _ = self.send(environment={name: value})
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("are all required", done.stderr)
                self.assertEqual(files["built"], "")

    def test_a_balance_that_cannot_be_read_afterwards_is_left_blank(self):
        """The send succeeded; only the report's last balance is unknown. The
        reads: the balance before, the one look that sees it paid, the last."""
        done, files, _ = self.send(signs="1 1", environment={"TEST_FAIL_FROM": "3"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(files["read"].split()), 3)
        self.assertEqual(self.result(done)["wallet_balance_after_nanotokens"], "")

    def test_it_reads_the_signature_threshold_first(self):
        self.assertIn(scripts.READ_SIGNATURE_THRESHOLD, scripts.SAFE_SEND)
        self.assertLess(
            scripts.SAFE_SEND.index("Get_Account_Custodians_Info"),
            scripts.SAFE_SEND.index("Submit_And_Identify"),
        )


class WrapperTests(unittest.TestCase):
    """send_from_safe: what it hands the program and what it makes of the answer."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = storage.NodeStateStore(temporary.name)
        self.config = node()

    def call(self, completed, bounce=True):
        with patch("evernode.scripts._import_verification_context",
                   return_value=Path("/scripts")), \
                patch("evernode.scripts.process.execute_command",
                      return_value=completed) as execute:
            try:
                return scripts.send_from_safe(
                    self.store, self.config, SAFE, DEST, 5 * NANO, bounce
                ), execute
            except process.OperationError as error:
                return error, execute

    def completed(self, code=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(["bash"], code, stdout, stderr)

    def test_the_program_gets_the_send_and_runs_as_sensitive(self):
        for bounce, flag in ((True, "true"), (False, "false")):
            with self.subTest(bounce=bounce):
                answer = {"transaction_id": "", "wallet_balance_before_nanotokens": "9",
                          "wallet_balance_after_nanotokens": "3"}
                result, execute = self.call(
                    self.completed(stdout="progress\n" + json.dumps(answer) + "\n"), bounce
                )
                self.assertEqual(result, answer)
                args, kwargs = execute.call_args
                self.assertEqual(args[0], ["bash", "-c", scripts.SAFE_SEND])
                self.assertEqual(kwargs["cwd"], Path("/scripts"))
                self.assertTrue(kwargs["sensitive"])
                self.assertFalse(kwargs["check"])
                env = kwargs["env"]
                self.assertEqual(
                    (env["EVERNODE_WALLET"], env["SEND_DEST"], env["SEND_VALUE"],
                     env["SEND_BOUNCE"]),
                    (SAFE, DEST, str(5 * NANO), flag),
                )
                self.assertNotIn("EVERNODE_SIGNER", env)

    def test_a_failure_is_logged_privately_and_reported_with_its_reason(self):
        error, _ = self.call(self.completed(
            code=1,
            stdout="---INFO: building\n",
            stderr="###-ERROR: the Safe holds 5 nanotokens, which does not cover 7\n",
        ))
        self.assertIsInstance(error, process.OperationError)
        text = str(error)
        self.assertIn("The send did not complete", text)
        self.assertIn("###-ERROR: the Safe holds 5 nanotokens", text)
        self.assertIn(
            "Nothing it sent can still go out; if a line above says the state is "
            "unknown, check the Safe in an explorer before sending again.",
            text,
        )
        logs = list(
            (self.store.node_directory("validator01") / "logs/validator").glob(
                "wallet-send-failed-*.log"
            )
        )
        self.assertEqual(len(logs), 1)
        self.assertIn(str(logs[0]), text)
        self.assertEqual(logs[0].stat().st_mode & 0o777, 0o600)
        self.assertIn("---INFO: building", logs[0].read_text())

    def test_a_program_stopped_from_outside_is_not_called_final(self):
        """Only the program's own exit 1 comes after the kit decided that nothing
        can still go out; a kill (137) can come with a message in flight."""
        error, _ = self.call(self.completed(code=137, stdout="---INFO: signing\n"))
        self.assertIsInstance(error, process.OperationError)
        self.assertIn(
            "It stopped before it finished: wait a minute and check the Safe in an "
            "explorer before sending again.",
            str(error),
        )
        self.assertNotIn("Nothing it sent can still go out", str(error))

    def test_an_answer_it_cannot_read_is_an_error(self):
        for stdout in ("", "not json\n"):
            with self.subTest(stdout=stdout):
                error, _ = self.call(self.completed(stdout=stdout))
                self.assertIsInstance(error, process.OperationError)
                self.assertIn("check the Safe in an explorer", str(error))


class InterruptTests(unittest.TestCase):
    """Once it has started, a send runs to its end: the first Ctrl+C only says
    so, a second one stops it. Stopped in the middle, a transfer could still go
    out unreported, and running the send again would pay twice."""

    SENT = {"transaction_id": "", "wallet_balance_before_nanotokens": "9",
            "wallet_balance_after_nanotokens": "3"}

    def setUp(self):
        # A Ctrl+C that reaches the test instead of the send fails the test
        # rather than ending the whole run.
        self.stray = []
        previous = signal.signal(signal.SIGINT, lambda *_: self.stray.append(1))
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        self.guard = signal.getsignal(signal.SIGINT)

    def send(self, presses, config=None, fails=False):
        def program(*_args):
            for _ in range(presses):
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(0.05)
            if fails:
                raise process.OperationError("The send did not complete")
            return dict(self.SENT)

        with patch("evernode.wallets.scripts.send_from_safe", side_effect=program), \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            try:
                outcome = wallets.wallet_send(
                    None, config or node(), address=DEST, amount=1, bounce=True
                )
            except (KeyboardInterrupt, process.OperationError) as raised:
                outcome = raised
        self.assertIs(signal.getsignal(signal.SIGINT), self.guard, "Ctrl+C not restored")
        self.assertEqual(self.stray, [])
        return outcome, errors.getvalue()

    def test_one_ctrl_c_says_the_send_goes_on_and_it_finishes(self):
        outcome, errors = self.send(1)
        self.assertIsInstance(outcome, dict)
        self.assertEqual(outcome["transaction_id"], "")
        self.assertEqual(
            errors.strip(),
            "The send has started and finishes within 6 minutes; press Ctrl+C again "
            "to stop it anyway.",
        )

    def test_a_safe_with_one_signature_finishes_sooner(self):
        _, errors = self.send(1, config=node("solo", custodians=1, required_signatures=1))
        self.assertIn("finishes within 3 minutes", errors)

    def test_a_second_ctrl_c_stops_it_and_says_what_to_check(self):
        outcome, errors = self.send(2)
        self.assertIsInstance(outcome, KeyboardInterrupt)
        self.assertIn(
            "Stopped. What the send already handed to the network can still go out: "
            "wait a minute and check the Safe in an explorer before sending again.",
            errors,
        )

    def test_without_a_ctrl_c_nothing_is_printed(self):
        outcome, errors = self.send(0)
        self.assertIsInstance(outcome, dict)
        self.assertEqual(errors, "")

    def test_ctrl_c_is_restored_when_the_send_fails(self):
        outcome, _ = self.send(1, fails=True)
        self.assertIsInstance(outcome, process.OperationError)

    def test_nothing_is_held_back_before_the_send_starts(self):
        """The checks run first; a refused send never touches Ctrl+C."""
        with patch("evernode.wallets.signal.signal") as handler, \
                self.assertRaises(process.OperationError):
            wallets.wallet_send(None, node(), address=DEST, amount=0, bounce=True)
        handler.assert_not_called()

    def run_program(self, program, presses_at):
        """The real runner on a real child: Ctrl+C at the given seconds."""
        with tempfile.TemporaryDirectory() as temporary:
            store = storage.NodeStateStore(temporary)
            timers = [threading.Timer(t, os.kill, (os.getpid(), signal.SIGINT))
                      for t in presses_at]
            with patch("evernode.scripts._import_verification_context",
                       return_value=Path(temporary)), \
                    patch("evernode.scripts.SAFE_SEND", program), \
                    contextlib.redirect_stderr(io.StringIO()) as errors:
                started = time.monotonic()
                for timer in timers:
                    timer.start()
                try:
                    outcome = wallets.wallet_send(
                        store, node(), address=DEST, amount=1, bounce=True
                    )
                except KeyboardInterrupt as raised:
                    outcome = raised
                finally:
                    for timer in timers:
                        timer.cancel()
                elapsed = time.monotonic() - started
        self.assertIs(signal.getsignal(signal.SIGINT), self.guard)
        return outcome, errors.getvalue(), elapsed

    def test_the_program_keeps_running_through_one_ctrl_c(self):
        answer = json.dumps(self.SENT)
        outcome, errors, elapsed = self.run_program(f"sleep 1; echo '{answer}'", [0.3])
        self.assertEqual({k: outcome[k] for k in self.SENT}, self.SENT)
        self.assertGreaterEqual(elapsed, 1)
        self.assertIn("press Ctrl+C again to stop it anyway", errors)

    def test_a_second_ctrl_c_stops_the_program(self):
        outcome, errors, elapsed = self.run_program("sleep 5; echo '{}'", [0.3, 0.6])
        self.assertIsInstance(outcome, KeyboardInterrupt)
        self.assertLess(elapsed, 4)
        self.assertIn("Stopped.", errors)


if __name__ == "__main__":
    unittest.main()
