"""evernode vote join, leave, tick and history: the store, the cron line, and what each pass is given."""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from evernode import cli, vote_schedule
from support import make_node_configuration

# Stands in for evernode-vote: logs each run with the description it read from
# the pipe, and answers `follow` with the next canned reply.
FAKE_ENGINE = """#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
if args == ["--version"]:
    print("evernode-vote 0.2.2")
    sys.exit(0)
net = json.load(open(args[args.index("--net") + 1])) if "--net" in args else None
rings = {n["name"]: open(n["keyring"]).read() for n in (net or {}).get("nodes", []) if n.get("keyring")}
with open(os.environ["FAKE_ENGINE_LOG"], "a") as log:
    log.write(json.dumps({"argv": args, "net": net, "rings": rings, "endpoint_env": os.environ.get("EVERNODE_ENDPOINT")}) + "\\n")
replies = json.load(open(os.environ["FAKE_REPLIES"]))
reply = replies.pop(0) if replies else {"exit": 0, "report": None}
json.dump(replies, open(os.environ["FAKE_REPLIES"], "w"))
sys.stderr.write(reply.get("stderr", "[progress]\\n"))
for vote in reply.get("streamed", []):
    print(json.dumps({"vote": vote}))
sys.stdout.flush()
time.sleep(reply.get("sleep", 0))
if reply.get("report") is not None:
    print(json.dumps(reply["report"]))
sys.exit(reply.get("exit", 0))
"""
PROPOSAL = "25276f466b97fe640e038b05aa3da9159a946ef7902fbecea11e8f93cf77a934"
OTHER = "cd" * 32
KEY = "9114bd592913" + "0" * 52


def report(state="open", next_pass_at=None, votes=(), result="voted: 1 vote(s) counted", **changes):
    value = {
        "proposal": PROPOSAL,
        "state": state,
        "change": "appoint dictator ff01 — burned",
        "change_lines": ["master key: ca53 → ff01 — burned"],
        "param": -999,
        "target": "ff" * 32,
        "critical": True,
        "config_version": "1000",
        "progress": {
            "round": 1, "wins": 0, "wins_needed": 4, "losses": 0, "losses_allowed": 2,
            "rounds_left": 7, "won_this_round": False, "voters": 1,
        },
        "round_end": 2_000_000_000,
        "round_end_exact": False,
        "earliest_acceptance": 2_000_000_600,
        "votes": list(votes),
        "next_pass_at": next_pass_at if state == "open" else None,
        "result": result,
        "errors": [],
    }
    value.update(changes)
    return value


def vote(round_=1, transaction="tx1"):
    return {
        "round": round_, "node": "validator01", "validator": 0, "key": KEY,
        "transaction": transaction, "sent_at": 1 if transaction else None,
        "counted_at": 2 if transaction else None,
    }


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        engine = self.tmp / "evernode-vote"
        engine.write_text(FAKE_ENGINE)
        engine.chmod(0o755)
        self.log = self.tmp / "calls.jsonl"
        self.replies = self.tmp / "replies.json"
        self.replies.write_text("[]")
        self.cron = self.tmp / "cron.d" / "evernode_vote"
        self.cron.parent.mkdir()
        self.state = self.tmp / "state"
        self.nodes = MagicMock()
        self.nodes.root = self.state
        self.nodes.node_directory.side_effect = lambda name: self.state / "nodes" / name
        self.nodes.list_node_names.return_value = ["validator01"]
        self.add_node("validator01", "solo")
        which = {"evernode-vote": str(engine), "evernode": "/usr/local/bin/evernode"}
        for context in (
            patch("evernode.vote_schedule.storage.NodeStateStore", return_value=self.nodes),
            patch("evernode.vote_schedule.storage.validate_private_path"),
            patch("evernode.voting.storage.validate_private_path"),
            patch("evernode.vote_schedule.CRON_DIRECTORY", self.cron.parent),
            patch("evernode.arguments.DEFAULT_STATE_DIR", str(self.state)),
            patch("evernode.vote_schedule.shutil.which", side_effect=which.get),
            patch("evernode.voting.shutil.which", side_effect=which.get),
            patch("evernode.vote_schedule.os.geteuid", return_value=0),
            patch("evernode.voting.os.geteuid", return_value=0),
            patch("evernode.vote_schedule.elections.cron_daemon_state", return_value="running"),
            patch.dict(os.environ, {"FAKE_ENGINE_LOG": str(self.log), "FAKE_REPLIES": str(self.replies)}),
        ):
            context.start()
            self.addCleanup(context.stop)
        os.environ.pop("EVERNODE_ENDPOINT", None)

    def add_node(self, name, validator_type, workchain=-1):
        """A managed node with a key ring; a DePool node's Safe is in workchain 0."""
        node = self.state / "nodes" / name
        (node / "node_cfg").mkdir(parents=True)
        (node / "node_cfg" / "config.json").write_text("{}")
        configs = getattr(self, "configs", {})
        configs[name] = make_node_configuration(
            name,
            validator_type=validator_type,
            wallet={
                "mode": "new", "custodians": 1, "required_signatures": 1,
                "wallet_address": None, "depool_address": None,
            },
            settings={"NODE_WC": workchain},
        )
        self.configs = configs
        self.nodes.load_node_config.side_effect = lambda n: self.configs[n]

    def reply(self, *replies):
        self.replies.write_text(json.dumps(list(replies)))

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def run_vote(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--state-dir", str(self.state), "vote", *argv])
        return code, out.getvalue(), err.getvalue()

    def stored(self):
        return json.loads((self.state / "voting" / "proposals.json").read_text())["proposals"]

    def join(self, *extra):
        return self.run_vote("join", "-n", "validator01", "--endpoint", "http://gql", "--proposal", PROPOSAL, "--yes", *extra)

    def test_join_refuses_an_unregistered_hash_and_records_nothing(self):
        self.reply({"exit": 1, "stderr": f"Error: proposal {PROPOSAL} is not registered\n"})
        code, _, err = self.join()
        self.assertEqual(code, 1)
        self.assertIn("is not registered", err)
        self.assertFalse((self.state / "voting" / "proposals.json").exists())
        self.assertFalse(self.cron.exists())
        [call] = self.calls()
        self.assertEqual(call["argv"][-1], "--dry-run")

    def test_join_saves_the_proposal_schedules_cron_and_votes_at_once(self):
        now = int(time.time())
        self.reply(
            {"report": report(next_pass_at=now, result="dry run: would vote with 1 key(s) now")},
            {"report": report(next_pass_at=now + 3600, votes=[vote()])},
        )
        code, out, _ = self.join()
        self.assertEqual(code, 0, out)
        preview, first = self.calls()
        self.assertEqual(preview["argv"][:2], ["follow", "--net"])
        self.assertEqual(preview["argv"][3:], ["--proposal", PROPOSAL, "--dry-run"])
        ring = str(self.state / "nodes" / "validator01" / "node_cfg" / "config.json")
        self.assertEqual(preview["net"], {"endpoint": "http://gql", "nodes": [{"name": "validator01", "keyring": ring}]})
        # the first pass knows what the preview saw
        self.assertEqual(first["net"]["known"], {"param": -999, "target": "ff" * 32, "config_version": "1000"})
        [entry] = self.stored()
        self.assertEqual(
            (entry["state"], entry["nodes"], entry["endpoint"], entry["next_pass_at"], len(entry["votes"])),
            ("open", ["validator01"], "http://gql", now + 3600, 1),
        )
        self.assertEqual(entry["last_pass"]["result"], "voted: 1 vote(s) counted")
        line = self.cron.read_text()
        self.assertTrue(line.startswith("# Managed by evernode\n"))
        self.assertIn(
            f"* * * * * root /usr/local/bin/evernode --state-dir {self.state} vote tick >> {self.state}/voting/cron.log 2>&1\n",
            line,
        )
        self.assertEqual(os.stat(self.state / "voting" / "proposals.json").st_mode & 0o777, 0o600)
        self.assertIn("Joined 25276f466b97", out)

    def test_joining_again_replaces_nodes_and_endpoint(self):
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now + 60)})
        self.join()
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now + 60)})
        code, out, _ = self.run_vote("join", "-n", "validator01", "--endpoint", "http://other", "--proposal", PROPOSAL, "--yes")
        self.assertEqual(code, 0)
        self.assertIn("joined already: it now votes with validator01 (before: validator01)", out)
        [entry] = self.stored()
        self.assertEqual(entry["endpoint"], "http://other")

    def test_the_same_proposal_on_another_network_does_not_take_the_schedule_over(self):
        """A proposal can hash the same on two networks; one host keeps one schedule for it."""
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now + 60)})
        self.join()
        self.add_node("devnode", "solo")
        self.configs["devnode"]["network"] = "devnet"
        engine_runs = len(self.calls())
        code, _, err = self.run_vote(
            "join", "-n", "devnode", "--endpoint", "http://devnet", "--proposal", PROPOSAL, "--yes"
        )
        self.assertEqual(code, 1)
        self.assertIn("joined already for nodes on main", err)
        self.assertIn("vote leave --proposal", err)
        self.assertEqual(len(self.calls()), engine_runs)
        [entry] = self.stored()
        self.assertEqual((entry["nodes"], entry["endpoint"]), (["validator01"], "http://gql"))

    def test_a_depool_node_joins_with_its_key_ring_and_no_wallet(self):
        self.add_node("depool01", "depool", workchain=0)
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now + 60)})
        code, _, _ = self.run_vote("join", "-n", "depool01", "--endpoint", "http://gql", "--proposal", PROPOSAL, "--yes")
        self.assertEqual(code, 0)
        ring = str(self.state / "nodes" / "depool01" / "node_cfg" / "config.json")
        for call in self.calls():
            self.assertEqual(call["net"]["nodes"], [{"name": "depool01", "keyring": ring}])
            self.assertNotIn("payer", call["net"])

    def test_tick_runs_due_passes_only_and_a_failure_keeps_the_time(self):
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now - 5)})
        self.join()
        data = json.loads((self.state / "voting" / "proposals.json").read_text())
        later = dict(data["proposals"][0], proposal=OTHER, next_pass_at=now + 3600)
        data["proposals"].append(later)
        (self.state / "voting" / "proposals.json").write_text(json.dumps(data))
        self.log.unlink()
        self.reply({"exit": 1, "stderr": "Error: the endpoint's newest block is 400 s old\n"})
        code, out, _ = self.run_vote("tick")
        self.assertEqual(code, 0)
        [call] = self.calls()
        self.assertEqual(call["argv"][4], PROPOSAL)
        due, other = self.stored()
        self.assertEqual(due["next_pass_at"], now - 5, "a failed pass leaves the time: the next minute tries again")
        self.assertEqual(due["last_pass"]["error"], "the endpoint's newest block is 400 s old")
        self.assertIn("pass failed", out)
        self.assertEqual(other["next_pass_at"], now + 3600)

    def test_a_closed_report_ends_the_entry_and_removes_the_cron_line(self):
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now - 1, votes=[vote()])})
        self.join()
        self.reply({"report": report(state="applied", result="closed: it is no longer among the config's proposals; the change is in effect",
                                     change=None, progress=None, votes=[vote(transaction=None)])})
        self.run_vote("tick")
        [entry] = self.stored()
        self.assertEqual((entry["state"], entry["next_pass_at"]), ("applied", None))
        self.assertNotIn("endpoint", entry, "the endpoint may hold a project key")
        self.assertEqual(entry["change"], "appoint dictator ff01 — burned", "what a closed report lacks stays")
        self.assertEqual(len(entry["votes"]), 1, "a vote found counted is the same vote")
        self.assertFalse(self.cron.exists())

    def test_votes_a_killed_pass_reported_are_kept(self):
        # the pass that casts the deciding vote can die before its report; the proposal is then gone,
        # so no later pass could find the vote counted
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now - 1)})
        self.join()
        self.reply({"exit": -9, "stderr": "", "streamed": [vote(round_=4, transaction="tx4")]})
        self.run_vote("tick")
        [entry] = self.stored()
        self.assertEqual([(v["round"], v["transaction"]) for v in entry["votes"]], [(4, "tx4")])
        self.assertEqual(entry["last_pass"]["error"], "exit code 247")
        self.assertEqual(entry["next_pass_at"], now - 1, "the next minute tries again")
        self.reply({"report": report(state="applied", result="closed", change=None, progress=None)})
        self.run_vote("tick")
        code, out, _ = self.run_vote("history", "--proposal", PROPOSAL)
        self.assertIn("round 4: validator01 as validator #0", out)
        self.assertIn("last read open: round 1", out)

    def test_a_tick_that_finds_a_pass_running_leaves_the_minute_to_it(self):
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now - 1)})
        self.join()
        self.log.unlink()
        store = vote_schedule.VoteStore(str(self.state))
        with store.locked():
            code, _, _ = self.run_vote("tick")
        self.assertEqual(code, 0)
        self.assertEqual(self.calls(), [])

    def test_leave_ends_the_entry_and_history_keeps_it(self):
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now + 60, votes=[vote()])})
        self.join()
        code, out, _ = self.run_vote("leave", "--proposal", PROPOSAL)
        self.assertEqual(code, 0)
        [entry] = self.stored()
        self.assertEqual(entry["state"], "left")
        self.assertNotIn("endpoint", entry)
        self.assertFalse(self.cron.exists())
        code, out, _ = self.run_vote("history", "--proposal", PROPOSAL)
        self.assertEqual(code, 0)
        self.assertIn("round 1: validator01 as validator #0", out)
        self.assertIn("in transaction tx1", out)
        code, _, err = self.run_vote("leave", "--proposal", PROPOSAL)
        self.assertEqual(code, 1)
        self.assertIn("not an open proposal", err)

    def test_an_unrecognized_cron_file_is_left_alone(self):
        self.cron.write_text("* * * * * root something else\n")
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)})
        code, _, err = self.join()
        self.assertEqual(code, 1)
        self.assertIn("unrecognized cron file", err)
        self.assertEqual(self.cron.read_text(), "* * * * * root something else\n")
        # review 1, #19: a join that cannot schedule its passes records nothing
        self.assertFalse((self.state / "voting" / "proposals.json").exists())

    def test_votes_merge_by_round_and_key(self):
        entry = {"votes": [vote(transaction=None)]}
        vote_schedule.merge_report(entry, report(next_pass_at=5, votes=[vote(), vote(round_=2)]), 1)
        self.assertEqual([(v["round"], v["transaction"]) for v in entry["votes"]], [(1, "tx1"), (2, "tx1")])
        self.assertEqual(entry["next_pass_at"], 5)

    def test_the_change_keeps_its_description_from_the_join(self):
        # bd-j: another proposal changed p20 first; the last pass described this one against the new value
        entry = {"votes": []}
        joined = ["freeze_due_limit: 100000000 → 100000001"]
        vote_schedule.merge_report(entry, report(change="set p20", change_lines=joined), 1, record_pass=False)
        later = ["freeze_due_limit: 100000002 → 100000001"]
        vote_schedule.merge_report(entry, report(change="set p20", change_lines=later), 2, record_pass=False)
        self.assertEqual(entry["change_lines"], joined)


    # Review 1: the fixes on evernode's side.

    def test_a_rejoin_keeps_the_stale_endpoint_guard(self):
        # #4: a re-join's preview is held to the version earlier passes read; a staler endpoint changes nothing
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now, config_version="1000")}, {"report": report(next_pass_at=now + 600, config_version="1000")})
        self.join()
        self.log.unlink()
        self.reply({"exit": 1, "stderr": "Error: the endpoint serves an older config (version 100) than an earlier pass read (1000)\n"})
        code, _, err = self.join()
        self.assertEqual(code, 1)
        self.assertIn("older config", err)
        [preview] = self.calls()
        self.assertEqual(preview["net"]["known"]["config_version"], "1000")
        self.assertEqual(self.stored()[0]["config_version"], "1000")

    def test_each_state_directory_has_its_own_cron_file(self):
        # #5: two state directories on one host never replace or remove each other's cron line
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now + 600)})
        self.join()
        other = self.tmp / "state-b"
        (other / "nodes" / "validator01" / "node_cfg").mkdir(parents=True)
        (other / "nodes" / "validator01" / "node_cfg" / "config.json").write_text("{}")
        default = self.state
        self.state = self.nodes.root = other
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now + 600)})
        self.join()
        files = sorted(f.name for f in self.cron.parent.iterdir())
        self.assertEqual(len(files), 2, files)
        self.assertIn(f"--state-dir {default} vote tick", self.cron.read_text())
        self.run_vote("leave", "--proposal", PROPOSAL)
        self.assertEqual(sorted(f.name for f in self.cron.parent.iterdir()), ["evernode_vote"])

    def test_a_large_report_does_not_stall_the_pass(self):
        # #6: stderr and stdout are read at once, so a report larger than a pipe holds passes through
        now = int(time.time())
        lines = ["x" * 100] * 2000  # about 200 KB of stdout
        self.reply({"report": report(next_pass_at=now, change_lines=lines)}, {"report": report(next_pass_at=now + 600)})
        with patch("evernode.vote_schedule.PASS_TIMEOUT", 20):
            started = time.time()
            code, out, _ = self.join()
        self.assertEqual(code, 0, out)
        self.assertLess(time.time() - started, 10)

    def test_a_vote_is_saved_the_moment_it_is_counted(self):
        # #7: evernode killed after the engine reported a vote still has that vote in the store
        import signal
        import subprocess as sp
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"streamed": [vote()], "sleep": 5, "report": report(next_pass_at=now + 600, votes=[vote()])})

        def die(*_):
            raise SystemExit("evernode killed")

        signal.signal(signal.SIGALRM, die)
        signal.alarm(2)
        try:
            with self.assertRaises(SystemExit):
                self.join()
        finally:
            signal.alarm(0)
            sp.run(["pkill", "-f", str(self.tmp / "evernode-vote")])
        [entry] = self.stored()
        self.assertEqual([v["transaction"] for v in entry["votes"]], ["tx1"])

    def test_the_engine_never_sees_the_endpoint_in_its_environment(self):
        # #17: the endpoint, which may hold a project key, goes only through the description pipe
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now + 600)})
        with patch.dict(os.environ, {"EVERNODE_ENDPOINT": "http://gql/SECRET"}):
            code, _, _ = self.run_vote("join", "-n", "validator01", "--proposal", PROPOSAL, "--yes")
        self.assertEqual(code, 0)
        self.assertEqual([c["endpoint_env"] for c in self.calls()], [None, None])
        self.assertEqual(self.calls()[0]["net"]["endpoint"], "http://gql/SECRET")

    def test_a_node_missing_for_a_pass_is_tried_again_within_a_minute(self):
        # #24: the healthy node is counted and the engine schedules the next round, but the missing one is not
        now = int(time.time())
        self.add_node("validator02", "solo")
        self.nodes.list_node_names.return_value = ["validator01", "validator02"]
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now - 1)})
        self.run_vote("join", "--all", "--endpoint", "http://gql", "--proposal", PROPOSAL, "--yes")
        ring = self.state / "nodes" / "validator02" / "node_cfg" / "config.json"
        ring.rename(ring.with_suffix(".away"))
        self.reply({"report": report(next_pass_at=now + 3600)})
        self.run_vote("tick")
        [entry] = self.stored()
        self.assertLessEqual(entry["next_pass_at"], int(time.time()) + 60)
        # #26: the node is named once
        self.assertEqual(entry["last_pass"]["error"], "validator02: no key ring yet; start its node first")

    def test_join_says_each_thing_once(self):
        # #26: the pass's result is the engine's to print; a refused hash's error is printed once
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now + 600, result="voted: 1 vote(s) counted")})
        code, out, err = self.join()
        self.assertEqual(code, 0)
        self.assertNotIn("voted: 1 vote(s) counted", out)
        self.assertIn("Joined 25276f466b97: next pass", out)
        self.log.unlink()
        self.reply({"exit": 1, "stderr": f"Error: proposal {OTHER} is not registered\n"})
        code, out, err = self.run_vote("join", "-n", "validator01", "--endpoint", "http://gql", "--proposal", OTHER, "--yes")
        self.assertEqual(code, 1)
        self.assertEqual((out + err).count("is not registered"), 1, out + err)

    def test_status_warns_when_an_open_proposal_has_no_cron_line(self):
        # #19: status checked only that a cron daemon runs, not that this host's line exists
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now + 600)})
        self.join()
        self.cron.unlink()
        shown = io.StringIO()
        with contextlib.redirect_stdout(shown):
            vote_schedule.print_status(str(self.state))
        self.assertIn(f"WARNING     {self.cron} is missing", shown.getvalue())

    def test_join_all_with_no_key_ring_is_refused(self):
        # #29: a join with nothing to vote with records nothing, rather than an entry that fails every minute
        (self.state / "nodes" / "validator01" / "node_cfg" / "config.json").unlink()
        code, _, err = self.run_vote("join", "--all", "--endpoint", "http://gql", "--proposal", PROPOSAL, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("No managed node has a key ring yet", err)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.state / "voting" / "proposals.json").exists())
        self.assertFalse(self.cron.exists())

    def test_join_all_passes_over_removed_nodes(self):
        # E1: node remove keeps the directory and its key ring, but the node validates nothing
        self.add_node("validator02", "solo")
        self.configs["validator02"]["phase"] = "removed"
        self.nodes.list_node_names.return_value = ["validator01", "validator02"]
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now + 3600)})
        code, out, err = self.run_vote("join", "--all", "--endpoint", "http://gql", "--proposal", PROPOSAL, "--yes")
        self.assertEqual(code, 0, out + err)
        self.assertIn("validator02: removed; skipped", err)
        self.assertEqual([n["name"] for n in self.calls()[0]["net"]["nodes"]], ["validator01"])
        [entry] = self.stored()
        self.assertEqual(entry["nodes"], ["validator01"])

    def test_join_all_with_only_removed_nodes_is_refused(self):
        self.configs["validator01"]["phase"] = "removed"
        code, _, err = self.run_vote("join", "--all", "--endpoint", "http://gql", "--proposal", PROPOSAL, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("validator01: removed; skipped", err)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.state / "voting" / "proposals.json").exists())

    def test_a_removed_node_named_with_n_still_votes(self):
        # Named on purpose: a node removed with --force may still hold a key of the current set.
        self.configs["validator01"]["phase"] = "removed"
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now + 3600)})
        code, out, err = self.join()
        self.assertEqual(code, 0, out + err)
        self.assertEqual([n["name"] for n in self.calls()[0]["net"]["nodes"]], ["validator01"])

    # --keyring: a node evernode does not manage, its config.json on this host or in a container.

    def ring(self, key="a2V5"):
        return json.dumps({"validator_key_ring": {key: {"pvt_key": "secret-" + key}}})

    def keyring_file(self, mode=0o600, text=None):
        path = self.tmp / "config.json"
        path.write_text(text if text is not None else self.ring())
        path.chmod(mode)
        return path

    def docker(self, *answers, mode=(0, "600\n", "")):
        """docker exec cat answers (exit code, stdout, stderr) in turn, and stat
        answers `mode`; other commands run for real."""
        real, queue, seen = vote_schedule.process.execute_command, list(answers), []

        def execute(argv, **options):
            if argv[0] != "docker":
                return real(argv, **options)
            seen.append(argv)
            if argv[3] == "stat":
                return vote_schedule.subprocess.CompletedProcess(argv, *mode)
            code, out, err = queue.pop(0) if len(queue) > 1 else queue[0]
            return vote_schedule.subprocess.CompletedProcess(argv, code, out, err)

        context = patch("evernode.voting.process.execute_command", side_effect=execute)
        context.start()
        self.addCleanup(context.stop)
        return seen

    def join_keyring(self, *sources):
        flags = [item for source in sources for item in ("--keyring", str(source))]
        return self.run_vote("join", *flags, "--endpoint", "http://gql", "--proposal", PROPOSAL, "--yes")

    def leftovers(self):
        return [p.name for p in (self.state / "voting").iterdir() if p.is_dir()]

    def test_join_needs_a_key_to_vote_with(self):
        code, _, err = self.run_vote("join", "--endpoint", "http://gql", "--proposal", PROPOSAL, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("-n NAME, --all for every managed node, or --keyring PATH", err)
        self.assertEqual(self.calls(), [])

    def test_a_key_ring_file_is_read_in_place_at_every_pass(self):
        path = self.keyring_file()
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now - 1)})
        code, out, _ = self.join_keyring(path)
        self.assertEqual(code, 0, out)
        preview, _ = self.calls()
        self.assertEqual(preview["net"]["nodes"], [{"name": str(path), "keyring": str(path)}])
        [entry] = self.stored()
        self.assertEqual((entry["nodes"], entry["keyrings"]), ([], [str(path)]))
        # The next election's key: ever-node rewrote the file, and the next pass reads it.
        path.write_text(self.ring("bmV4dA=="))
        self.reply({"report": report(next_pass_at=now + 3600)})
        self.run_vote("tick")
        self.assertIn("secret-bmV4dA==", self.calls()[-1]["rings"][str(path)])

    def test_a_key_ring_file_other_users_can_read_is_refused_at_join(self):
        path = self.keyring_file(mode=0o644)
        code, _, err = self.join_keyring(path)
        self.assertEqual(code, 1)
        self.assertIn(f"chmod 600 {path}", err)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.state / "voting" / "proposals.json").exists())

    def test_a_file_without_a_key_ring_is_refused(self):
        path = self.keyring_file(text='{"control_server": {}}')
        code, _, err = self.join_keyring(path)
        self.assertEqual(code, 1)
        self.assertIn("has no validator_key_ring: name the node's config.json", err)

    def test_a_container_key_ring_is_copied_for_each_pass_and_removed(self):
        seen = self.docker((0, self.ring(), ""), (0, self.ring(), ""), (0, self.ring("bmV4dA=="), ""))
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now - 1)})
        code, out, _ = self.join_keyring("ever-node:/ever-node/node_cfg/config.json")
        self.assertEqual(code, 0, out)
        # E2: join reads the mode first; nothing here ever changes it.
        self.assertEqual(seen[0], ["docker", "exec", "ever-node", "stat", "-L", "-c", "%a", "/ever-node/node_cfg/config.json"])
        self.assertEqual(seen[1], ["docker", "exec", "ever-node", "cat", "/ever-node/node_cfg/config.json"])
        preview, first = self.calls()
        source = "ever-node:/ever-node/node_cfg/config.json"
        self.assertEqual(preview["rings"][source], self.ring())
        self.assertTrue(preview["net"]["nodes"][0]["keyring"].startswith(str(self.state / "voting")))
        self.reply({"report": report(next_pass_at=now + 3600)})
        self.run_vote("tick")
        self.assertEqual(self.calls()[-1]["rings"][source], self.ring("bmV4dA=="))
        # A pass only reads: the mode is checked at join.
        self.assertEqual([argv[3] for argv in seen], ["stat", "cat", "cat", "cat"])
        self.assertFalse([argv for argv in seen if "chmod" in argv])
        self.assertEqual(self.leftovers(), [])

    def test_a_container_key_ring_other_users_can_read_is_refused_at_join(self):
        # E2: the same refusal as a file on this host, and the key ring is never read
        seen = self.docker((0, self.ring(), ""), mode=(0, "644\n", ""))
        code, _, err = self.join_keyring("ever-node:/node/config.json")
        self.assertEqual(code, 1)
        self.assertIn(
            "ever-node:/node/config.json holds a secret key and other users can read it: "
            "docker exec ever-node chmod 600 /node/config.json",
            err,
        )
        self.assertEqual([argv[3] for argv in seen], ["stat"])
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.state / "voting" / "proposals.json").exists())

    def test_a_container_key_ring_whose_mode_cannot_be_read_is_refused(self):
        for mode, shown in (
            ((1, "", "stat: cannot statx '/node/config.json': No such file or directory\n"),
             "ever-node:/node/config.json: stat: cannot statx '/node/config.json': No such file or directory"),
            ((0, "?\n", ""), "ever-node:/node/config.json: cannot tell who can read it"),
        ):
            with self.subTest(shown=shown):
                seen = self.docker((0, self.ring(), ""), mode=mode)
                code, _, err = self.join_keyring("ever-node:/node/config.json")
                self.assertEqual(code, 1)
                self.assertIn(shown, err)
                self.assertEqual([argv[3] for argv in seen], ["stat"])
                self.assertEqual(self.calls(), [])

    def test_a_container_error_names_the_source_and_never_its_output(self):
        self.docker((1, "secret-output", "Error response from daemon: No such container: nope\n"))
        code, _, err = self.join_keyring("nope:/node/config.json")
        self.assertEqual(code, 1)
        self.assertIn("nope:/node/config.json: Error response from daemon: No such container: nope", err)
        self.assertNotIn("secret-output", err)
        self.assertEqual(self.leftovers(), [])

    def test_a_pass_votes_with_the_key_rings_it_can_read(self):
        self.docker((0, self.ring(), ""), (0, self.ring(), ""), (1, "", "container is not running"))
        now = int(time.time())
        self.reply({"report": report(next_pass_at=now)}, {"report": report(next_pass_at=now - 1)})
        code, out, _ = self.run_vote(
            "join", "-n", "validator01", "--keyring", "ever-node:/node/config.json",
            "--endpoint", "http://gql", "--proposal", PROPOSAL, "--yes",
        )
        self.assertEqual(code, 0, out)
        self.reply({"report": report(next_pass_at=now + 3600)})
        self.run_vote("tick")
        self.assertEqual([n["name"] for n in self.calls()[-1]["net"]["nodes"]], ["validator01"])
        [entry] = self.stored()
        self.assertIn("ever-node:/node/config.json: container is not running", entry["last_pass"]["error"])
        self.assertLessEqual(entry["next_pass_at"], int(time.time()) + vote_schedule.RETRY)
        shown = io.StringIO()
        with contextlib.redirect_stdout(shown):
            vote_schedule.print_status(str(self.state))
        self.assertIn("nodes validator01, ever-node:/node/config.json;", shown.getvalue())


class PaymentTests(unittest.TestCase):
    """D10: --from FILE shows the price and asks before paying, like the other two."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        engine = self.tmp / "evernode-vote"
        engine.write_text(FAKE_ENGINE)
        engine.chmod(0o755)
        self.log = self.tmp / "calls.jsonl"
        replies = self.tmp / "replies.json"
        replies.write_text("[]")
        for context in (
            patch("evernode.voting.shutil.which", return_value=str(engine)),
            patch.dict(os.environ, {"FAKE_ENGINE_LOG": str(self.log), "FAKE_REPLIES": str(replies)}),
        ):
            context.start()
            self.addCleanup(context.stop)

    def propose(self, *extra):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["vote", "propose", "--endpoint", "u", "--burn", "--from", "w.json", *extra])
        return code, err.getvalue()

    def argvs(self):
        if not self.log.exists():
            return []
        return [json.loads(line)["argv"][3:] for line in self.log.read_text().splitlines()]

    def test_one_engine_process_shows_the_price_asks_and_pays(self):
        # review 1, #21: the price confirmed is the price paid, so the engine asks in the process that pays
        code, _ = self.propose()
        self.assertEqual(code, 0)
        self.assertEqual(self.argvs(), [["--burn", "--from", "w.json"]])
        self.log.unlink()
        code, _ = self.propose("--yes")
        self.assertEqual(code, 0)
        self.assertEqual(self.argvs(), [["--burn", "--from", "w.json", "--yes"]])

    def test_a_wallet_file_others_can_read_is_refused_before_anything_runs(self):
        # bd-j: a 644 payer file paid without a word, though the guide says it must be readable only by its owner
        wallet, key = self.tmp / "payer.json", self.tmp / "c1.keys.json"
        wallet.write_text(json.dumps({"name": "payer", "type": "multisig", "address": "-1:" + "0" * 64, "keys": [key.name]}))
        key.write_text("{}")
        for readable, private in ((wallet, key), (key, wallet)):
            readable.chmod(0o644)
            private.chmod(0o600)
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = cli.main(["vote", "propose", "--endpoint", "u", "--burn", "--from", str(wallet), "--yes"])
            self.assertEqual(code, 1)
            self.assertIn(f"chmod 600 {readable}", err.getvalue())
            self.assertEqual(self.argvs(), [], "the engine never ran")
        key.chmod(0o600)
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["vote", "propose", "--endpoint", "u", "--burn", "--from", str(wallet), "--yes"]), 0)


class KeyFileTests(unittest.TestCase):
    """A key ring holds the validator's secret keys and a console file the
    console's; a relay is a wallet file. Each must be readable by its owner only."""

    setUp = PaymentTests.setUp

    def cast(self, *extra):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["vote", "cast", "--endpoint", "u", "--proposal", PROPOSAL, *extra])
        return code, err.getvalue()

    def private(self, name, text="{}"):
        path = self.tmp / name
        path.write_text(text)
        path.chmod(0o600)
        return path

    def test_a_key_file_others_can_read_is_refused_before_anything_runs(self):
        # The voting guide's own keyring command made a 644 file under root's umask.
        for option in ("--keyring", "--control"):
            with self.subTest(option=option):
                key = self.private(option[2:] + ".json")
                key.chmod(0o644)
                code, err = self.cast(option, str(key))
                self.assertEqual(code, 1)
                self.assertIn(f"chmod 600 {key}", err)
                self.assertFalse(self.log.exists(), "the engine never ran")
                key.chmod(0o600)
                self.assertEqual(self.cast(option, str(key))[0], 0)
                self.log.unlink()

    def test_a_relay_wallet_others_can_read_is_refused(self):
        keyring = self.private("keyring.json")
        relay = self.private("relay.json", json.dumps({"name": "relay", "keys": []}))
        relay.chmod(0o644)
        code, err = self.cast("--keyring", str(keyring), "--relay", str(relay))
        self.assertEqual(code, 1)
        self.assertIn(f"chmod 600 {relay}", err)
        self.assertFalse(self.log.exists(), "the engine never ran")


if __name__ == "__main__":
    unittest.main()
