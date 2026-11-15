"""A bid that does not register is sent again by the next scheduled run, a few
times per election at most, and the wait for it starts after signing."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "evernode/vendor/ever-validator/scripts"
TAKE_PART = (SCRIPTS / "take_part_in_elections.sh").read_text()
ELECTION = "1793451980"


def bash(program, directory, **environment):
    return subprocess.run(
        ["bash", "-c", program], capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "CURR_ELECTIONS_DIR": str(directory),
             "MANAGED_OPS": str(SCRIPTS / "managed_ops.shinc"), **environment},
    )


def section(start, end=None):
    """The script's own text from START up to END (or to its end)."""
    first = TAKE_PART.index(start)
    return TAKE_PART[first: TAKE_PART.index(end, first) if end else len(TAKE_PART)]


@unittest.skipUnless(shutil.which("bash"), "Requires bash")
class BidAttemptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def attempts(self, count):
        (self.directory / f"{ELECTION}_bid-attempts.txt").write_text("1\n" * count)

    def stopped(self):
        """Why bidding stopped in this election, or "" while it goes on."""
        path = self.directory / f"{ELECTION}_bid-stopped.txt"
        return path.read_text().strip() if path.exists() else ""

    def test_the_helpers_count_attempts_and_remember_a_stop(self):
        result = bash(f"""source "$MANAGED_OPS"
Bidding_Stopped {ELECTION} || echo "not stopped"
Record_Bid_Attempt {ELECTION}; Record_Bid_Attempt {ELECTION}
echo "attempts $(Bid_Attempts {ELECTION})"
Stop_Bidding {ELECTION} "the reason"
Bidding_Stopped {ELECTION} && echo "stopped"
""", self.directory)
        self.assertEqual(result.stdout.split("\n")[:4], ["not stopped", "attempts 2", "the reason", "stopped"])

    def test_a_stopped_election_is_not_bid_again(self):
        block = section("# Bidding in this election may have stopped", "# Check that you have unsigned")
        program = f'source "$MANAGED_OPS"\nelections_id={ELECTION}\n{block}\necho REACHED_BID\n'
        self.assertIn("REACHED_BID", bash(program, self.directory).stdout)
        (self.directory / f"{ELECTION}_bid-stopped.txt").write_text("3 bids were sent and none registered.\n")
        result = bash(program, self.directory)
        self.assertEqual(result.returncode, 0)
        self.assertIn("No further bid in elections", result.stdout)
        self.assertNotIn("REACHED_BID", result.stdout)

    def unregistered(self, *, earlier, mode="msig", round_stake=""):
        """Run the script's own failure report for a bid that did not register,
        after EARLIER bids in this election that did not either."""
        (self.directory / f"{ELECTION}_bid-stopped.txt").unlink(missing_ok=True)
        self.attempts(earlier)
        tail = section("# Not registered, and it cannot register later")
        program = f"""source "$MANAGED_OPS"
SelfScriptName=take_part_in_elections.sh
elections_id={ELECTION} STAKE_MODE={mode} Stake_DST_Addr=0:dst NOMINATOR_ADDR=0:pool
Get_DP_Round_Stake_For_Election() {{ echo "{round_stake}"; }}
Get_Account_Info() {{ echo "Active 0 0"; }}
Send_msg_toTelBot() {{ :; }}
{tail}"""
        return bash(program, self.directory)

    def test_a_bid_that_did_not_register_is_sent_again_by_the_next_run(self):
        result = self.unregistered(earlier=0)
        self.assertEqual(result.returncode, 1)
        self.assertIn("attempt 1 of 3; the next scheduled run sends it again in this election", result.stdout)
        self.assertNotIn("elections have closed", result.stdout)
        self.assertEqual(self.stopped(), "")

    def test_after_the_last_attempt_no_further_bid_is_sent(self):
        for mode in ("msig", "nominator"):
            with self.subTest(mode=mode):
                result = self.unregistered(earlier=2, mode=mode)
                self.assertIn("attempt 3 of 3, so no further bid is sent", result.stdout)
                self.assertEqual(self.stopped(), "3 bids were sent and none registered.")

    def test_a_depool_round_with_no_stake_stops_at_once(self):
        """Stakes made during an election go to the collecting round, never to
        the one being elected, so a retry cannot change it."""
        result = self.unregistered(earlier=0, mode="depool", round_stake="0")
        self.assertIn("No further bid is sent in this election", result.stdout)
        self.assertIn("no stake in the round being elected", self.stopped())

    def test_a_stopped_election_is_left_before_a_request_is_made(self):
        self.assertLess(TAKE_PART.index("Bidding_Stopped "), TAKE_PART.index("election-bid "))

    def test_only_a_bid_that_did_not_register_is_counted(self):
        """A run that stops before sending (a wallet it cannot read, a full
        queue) used to count, so the first real failure became attempt 4 of 3."""
        self.assertEqual(TAKE_PART.count("Record_Bid_Attempt "), 1)
        self.assertGreater(TAKE_PART.index("Record_Bid_Attempt "),
                           TAKE_PART.index("# Not registered, and it cannot register later"))


if __name__ == "__main__":
    unittest.main()
