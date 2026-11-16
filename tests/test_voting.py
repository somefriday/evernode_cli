"""evernode vote: what reaches evernode-vote, through which channel, and when."""

import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from evernode import arguments, cli, voting
from support import make_node_configuration

# Stands in for evernode-vote: logs its arguments and the description it read
# from the pipe, one JSON line per run.
FAKE_ENGINE = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print(os.environ.get("FAKE_ENGINE_VERSION", "evernode-vote 0.2.7"))
    sys.exit(0)
net = json.load(open(args[args.index("--net") + 1])) if "--net" in args else None
with open(os.environ["FAKE_ENGINE_LOG"], "a") as log:
    log.write(json.dumps({"argv": args, "net": net}) + "\\n")
sys.exit(int(os.environ.get("FAKE_ENGINE_EXIT", "0")))
"""
PROPOSAL = "ab" * 32
ADDRESS = "-1:" + "7" * 64
PHRASE = "abandon " * 11 + "about"


class VoteGroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        engine = self.tmp / "evernode-vote"
        engine.write_text(FAKE_ENGINE)
        engine.chmod(0o755)
        self.log = self.tmp / "calls.jsonl"
        for context in (
            patch("evernode.voting.shutil.which", return_value=str(engine)),
            patch.dict(os.environ, {"FAKE_ENGINE_LOG": str(self.log)}),
        ):
            context.start()
            self.addCleanup(context.stop)
        os.environ.pop("EVERNODE_ENDPOINT", None)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def vote(self, *argv, euid=1000):
        err = io.StringIO()
        with (
            patch("evernode.cli.os.geteuid", return_value=euid),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main(["vote", *argv])
        return code, err.getvalue()

    def managed_node(self, validator_type="solo", custodians=2):
        """A state directory with one node, its key ring and Safe files."""
        state = self.tmp / "state"
        node = state / "nodes" / "validator01"
        (node / "node_cfg").mkdir(parents=True)
        (node / "node_cfg" / "config.json").write_text("{}")
        (node / "keys").mkdir()
        (node / "keys" / "validator01.addr").write_text(ADDRESS + "\n")
        store = MagicMock()
        store.node_directory.side_effect = lambda name: state / "nodes" / name
        store.list_node_names.return_value = ["validator01", "validator02"]
        store.load_node_config.return_value = make_node_configuration(
            "validator01",
            validator_type=validator_type,
            wallet={
                "mode": "new",
                "custodians": custodians,
                "required_signatures": custodians,
                "wallet_address": None,
                "depool_address": None,
            },
        )
        return state, store

    def test_plain_mode_is_the_engine_with_the_description_on_a_pipe(self):
        with patch("evernode.cli.storage.NodeStateStore") as store:
            code, _ = self.vote(
                "cast", "--endpoint", "http://gql/graphql", "--keyring", "ring.json",
                "--proposal", PROPOSAL, "--watch",
            )
        self.assertEqual(code, 0)
        store.assert_not_called()
        [call] = self.calls()
        self.assertEqual(call["argv"][:2], ["cast", "--net"])
        self.assertRegex(call["argv"][2], r"^/dev/fd/\d+$")
        self.assertEqual(call["argv"][3:], ["--proposal", PROPOSAL, "--watch"])
        ring = str(Path("ring.json").absolute())
        self.assertEqual(
            call["net"],
            {"endpoint": "http://gql/graphql", "nodes": [{"name": "ring.json", "keyring": ring}]},
        )

    def test_engine_options_pass_on_in_order_and_abbreviations_stay_off(self):
        self.vote(
            "propose", "--endpoint", "http://gql/graphql", "--param", "-999", "--value-json",
            '{"p0": 1}', "--control", "console.json", "--if-hash", "none", "--dry-run",
        )
        self.vote("status", "--keys")
        propose, status = self.calls()
        self.assertEqual(
            propose["argv"][3:],
            ["--param", "-999", "--value-json", '{"p0": 1}', "--if-hash", "none", "--dry-run"],
        )
        self.assertEqual(propose["net"]["endpoint"], "http://gql/graphql")
        self.assertEqual(propose["net"]["nodes"][0]["name"], "console.json")
        # the engine's --keys is not read as evernode's --keyring
        self.assertEqual((status["argv"][3:], status["net"]["nodes"]), (["--keys"], []))

    def test_refusals_come_before_the_engine_runs(self):
        cases = [
            (["cast", "--endpoint", "u", "--keyring", "k", "--net", "n.json"], "--net:"),
            (["mint-wallet", "--name", "payer", "--secret", "00"], "never accepted"),
            (["propose", "--endpoint", "u", "--burn", "--from", "w.json", "--lock", "/tmp/x"], "names the lock itself"),
            (["cast", "--endpoint", "u", "--proposal", PROPOSAL], "signs with validator keys"),
            # a script's unset variable does not mean mainnet
            (["cast", "--endpoint", "", "--keyring", "k", "--proposal", PROPOSAL], "--endpoint is empty"),
            (["run", "--endpoint", "u", "--keyring", "k", "--burn", "--pay-from", ADDRESS], "pay only with propose"),
            (["propose", "--endpoint", "u", "--burn", "--pay-from-safe", "--from", "w.json"], "one wallet"),
            (["propose", "--endpoint", "u", "--burn", "--pay-from-safe"], "name the node with -n"),
            (["status", "-n", "validator01", "--endpoint", "u"], "sudo"),
            # D4: managed nodes vote through join
            (["cast", "-n", "validator01", "--endpoint", "u", "--proposal", PROPOSAL],
             f"sudo evernode vote join -n validator01 --proposal {PROPOSAL}"),
            (["cast", "--all", "--endpoint", "u", "--proposal", PROPOSAL, "--watch"],
             f"sudo evernode vote join --all --proposal {PROPOSAL}"),
            (["burn", "-n", "validator01", "--endpoint", "u"], "propose the change first"),
            (["run", "--all", "--endpoint", "u", "--burn"], "then sudo evernode vote join --all"),
            # D10: every payment goes through propose, which asks first
            (["run", "--endpoint", "u", "--keyring", "k", "--burn", "--from", "w.json"], "pay only with propose"),
        ]
        for argv, message in cases:
            with self.subTest(argv=argv):
                code, err = self.vote(*argv)
                self.assertEqual(code, 1)
                self.assertIn(message, err)
        self.assertEqual(self.calls(), [])

    def test_the_endpoint_is_the_flag_else_mainnets_public_one(self):
        cases = [
            # (argv, endpoint the engine gets, whether evernode names it)
            (["status"], arguments.DEFAULT_ENDPOINT, False),
            (["cast", "--keyring", "k", "--proposal", PROPOSAL], arguments.DEFAULT_ENDPOINT, True),
            (["cast", "--endpoint", "http://devnet", "--keyring", "k", "--proposal", PROPOSAL], "http://devnet", False),
        ]
        for argv, endpoint, named in cases:
            with self.subTest(argv=argv):
                code, err = self.vote(*argv)
                self.assertEqual(code, 0)
                self.assertEqual(self.calls()[-1]["net"]["endpoint"], endpoint)
                self.assertEqual(f"endpoint: {arguments.DEFAULT_ENDPOINT} (default" in err, named)

    def test_evernode_endpoint_is_refused_not_ignored(self):
        # It named the endpoint before; ignoring it would send another network's votes to mainnet.
        with patch.dict(os.environ, {"EVERNODE_ENDPOINT": "http://devnet"}):
            code, err = self.vote("cast", "--endpoint", "http://devnet", "--keyring", "k", "--proposal", PROPOSAL)
        self.assertEqual(code, 1)
        self.assertIn("EVERNODE_ENDPOINT is no longer read: unset it and pass --endpoint URL", err)
        self.assertEqual(self.calls(), [])

    def test_mainnets_endpoint_is_refused_for_a_node_on_another_network(self):
        state, store = self.managed_node()
        store.load_node_config.return_value["network"] = "devnet"
        with (
            patch("evernode.voting.storage.NodeStateStore", return_value=store),
            patch("evernode.voting.storage.validate_private_path"),
            patch("evernode.vote_schedule.print_status"),
        ):
            code, err = self.vote("status", "-n", "validator01", "--keys", euid=0)
            self.assertEqual(code, 1)
            self.assertIn(f"validator01 is on devnet, and {arguments.DEFAULT_ENDPOINT} is mainnet's", err)
            self.assertEqual(self.calls(), [])
            code, _ = self.vote("status", "-n", "validator01", "--endpoint", "http://devnet", "--keys", euid=0)
        self.assertEqual(code, 0)
        self.assertEqual(self.calls()[0]["net"]["endpoint"], "http://devnet")

    def test_offline_commands_reach_the_engine_as_typed(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            # mint-wallet's own --name is not evernode's -n
            self.assertEqual(self.vote("mint-wallet", "--name", "payer")[0], 0)
            self.assertEqual(self.vote("burn-key", "--help")[0], 0)
        self.assertNotIn("--pay-from-safe", out.getvalue())
        self.assertEqual(
            [(call["argv"], call["net"]) for call in self.calls()],
            [
                (["mint-wallet", "--name", "payer"], None),
                (["burn-key", "--help"], None),
            ],
        )

    def test_managed_nodes_sign_from_their_key_rings_in_place(self):
        state, store = self.managed_node()
        checked = []
        with (
            patch("evernode.voting.storage.NodeStateStore", return_value=store),
            patch("evernode.voting.storage.validate_private_path", side_effect=checked.append),
            patch("evernode.vote_schedule.print_status"),
        ):
            code, err = self.vote("status", "--all", "--endpoint", "u", "--keys", euid=0)
            missing, missing_err = self.vote(
                "status", "-n", "validator02", "--endpoint", "u", "--keys", euid=0
            )
        ring = state / "nodes" / "validator01" / "node_cfg" / "config.json"
        self.assertEqual(code, 0)
        self.assertEqual(self.calls()[0]["net"]["nodes"], [{"name": "validator01", "keyring": str(ring)}])
        self.assertEqual(checked, [ring.parent, ring])
        self.assertIn("validator02: no key ring yet; skipped", err)
        self.assertEqual(missing, 1)
        self.assertIn("validator02: no key ring yet; start its node first", missing_err)

    def test_the_safe_pays_in_one_engine_run_that_takes_the_election_lock_itself(self):
        """The engine locks after the operator's answer, so a question left open
        holds up no scheduled election run; evernode keeps the operation lock."""
        state, store = self.managed_node()
        events = []

        @contextlib.contextmanager
        def lock(label):
            events.append((label, len(self.calls())))
            yield
            events.append(("released", len(self.calls())))

        store.acquire_operation_lock.side_effect = lambda name: lock("operation lock")
        with (
            patch("evernode.voting.storage.NodeStateStore", return_value=store),
            patch("evernode.voting.storage.validate_private_path"),
            patch("evernode.voting.elections.hold_election_lock") as election_lock,
        ):
            code, _ = self.vote("propose", "-n", "validator01", "--pay-from-safe", "--burn", "--endpoint", "u", euid=0)
        self.assertEqual(code, 0)
        # review 1, #21: one engine run shows the price, asks and pays, so what is paid is what was shown
        self.assertEqual(events, [("operation lock", 0), ("released", 1)])
        election_lock.assert_not_called()
        [paid] = self.calls()
        self.assertNotIn("--yes", paid["argv"])
        lock = paid["argv"][paid["argv"].index("--lock") + 1]
        self.assertEqual(lock, str(state / "nodes" / "validator01" / "elections" / "evernode-election.lock"))
        keys = state / "nodes" / "validator01" / "keys"
        self.assertEqual(
            paid["net"]["payer"],
            {
                "name": "validator01's Safe",
                "type": "multisig",
                "address": ADDRESS,
                "keys": [str(keys / "validator01_1.keys.json"), str(keys / "validator01_2.keys.json")],
            },
        )

    def test_the_engine_locks_after_the_answer_and_before_it_pays(self):
        source = (Path(__file__).resolve().parents[1] / "vote/src/campaign.rs").read_text()
        propose = source[source.index("fn propose("):]
        propose = propose[: propose.index("\n}\n")]
        asked = propose.index("crate::confirm(&question)")
        locked = propose.index("hold_lock(path)?")
        paid = propose.index("payer.transfer(")
        self.assertLess(asked, locked)
        self.assertLess(locked, paid)

    def test_a_depool_safe_cannot_pay_and_a_failed_run_pays_nothing(self):
        _, store = self.managed_node(validator_type="depool")
        with (
            patch("evernode.voting.storage.NodeStateStore", return_value=store),
            patch("evernode.voting.storage.validate_private_path"),
        ):
            code, err = self.vote("propose", "-n", "validator01", "--pay-from-safe", "--burn", "--endpoint", "u", euid=0)
        self.assertEqual(code, 1)
        self.assertIn("workchain 0", err)
        with (
            patch.dict(os.environ, {"FAKE_ENGINE_EXIT": "1"}),
            patch("evernode.voting.sys.stdin") as stdin,
            patch("evernode.voting.getpass.getpass", return_value=PHRASE),
        ):
            stdin.isatty.return_value = True
            code, _ = self.vote("propose", "--pay-from", ADDRESS, "--burn", "--endpoint", "u")
        self.assertEqual(code, 1)
        self.assertEqual(len(self.calls()), 1)

    def test_a_seed_phrase_is_asked_in_a_terminal_and_only_piped(self):
        with (
            patch("evernode.voting.sys.stdin") as stdin,
            patch("evernode.voting.getpass.getpass", return_value=PHRASE) as ask,
        ):
            stdin.isatty.return_value = True
            # the address follows the option after a space, as a solo Safe's does
            code, _ = self.vote("propose", "--pay-from", ADDRESS, "--burn", "--endpoint", "u")
            stdin.isatty.return_value = False
            refused, err = self.vote("propose", "--pay-from", ADDRESS, "--burn", "--endpoint", "u")
        self.assertEqual(code, 0)
        ask.assert_called_once_with(f"Seed phrase of {ADDRESS}: ")
        # the engine shows the price and asks before it pays, in the run that holds the phrase
        [paid] = self.calls()
        self.assertNotIn("--yes", paid["argv"])
        self.assertEqual(paid["net"]["payer"], {"name": ADDRESS, "address": ADDRESS, "phrases": [PHRASE]})
        self.assertFalse(any("abandon" in " ".join(call["argv"]) for call in self.calls()))
        self.assertEqual(refused, 1)
        self.assertIn("in a terminal", err)

    def test_the_engine_answers_for_itself(self):
        with patch.dict(os.environ, {"FAKE_ENGINE_EXIT": "3"}):
            self.assertEqual(self.vote("status", "--endpoint", "u")[0], 3)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.vote("cast", "--help")[0], 0)
        self.assertIn("--pay-from-safe", out.getvalue())
        self.assertEqual(self.calls()[-1]["argv"], ["cast", "--help"])

    def test_the_engine_is_the_release_this_evernode_speaks_to(self):
        with patch.dict(os.environ, {"FAKE_ENGINE_VERSION": "evernode-vote 0.0.9"}):
            code, err = self.vote("status", "--endpoint", "u")
        self.assertEqual(code, 1)
        self.assertIn("needs evernode-vote 0.2.7: run sudo evernode host setup", err)
        with patch("evernode.voting.shutil.which", return_value=None):
            code, err = self.vote("status", "--endpoint", "u")
        self.assertIn("evernode-vote is not installed: run sudo evernode host setup", err)
        cargo = Path(__file__).resolve().parents[1] / "vote" / "Cargo.toml"
        version = re.search(r'^version = "([^"]+)"', cargo.read_text(), re.M).group(1)
        self.assertEqual(voting.ENGINE_VERSION, version)


if __name__ == "__main__":
    unittest.main()
