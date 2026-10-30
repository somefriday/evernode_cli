"""The one wait every operation converges through.

An operation is "build a signed message, deliver it, decide whether it worked",
and every failure traced so far came from the deciding: a single read that lost
a race with inclusion, a transport acknowledgement mistaken for execution, a
counter that unrelated activity also moves. These cases pin the replacement
down, including the parts that are easy to get subtly wrong -- a deadline that
has already passed, a chain that cannot be read at all, and a predicate that
must never be allowed to send anything.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "vendor/ever-validator/scripts"
MANAGED_OPS = SCRIPTS / "managed_ops.shinc"


@unittest.skipUnless(shutil.which("bash"), "Requires bash")
class AwaitEffectTests(unittest.TestCase):
    def run_case(self, body, **environment):
        with tempfile.TemporaryDirectory() as temporary:
            program = (
                "set +u\n"
                'function echoerr() { printf "%s\\n" "$*" >&2; }\n'
                f'source "{MANAGED_OPS}"\n'
                "function sleep() { :; }\n" + body
            )
            path = Path(temporary) / "case.sh"
            path.write_text(program)
            return subprocess.run(
                ["bash", str(path)],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
                env=dict(os.environ, TEST_ROOT=temporary, **environment),
                cwd=temporary,
            )

    # ---- Trans_Created_At: the id carries its own creation time -----------

    def test_a_transaction_id_yields_its_creation_time(self):
        """The real election bid: submitted 2026-10-29T02:29:03Z, and its id
        says so without anything having recorded the time separately."""
        result = self.run_case('Trans_Created_At 7701911206319200129\n')
        self.assertEqual(result.stdout.strip(), "1793240943")

    def test_a_non_numeric_id_is_refused_rather_than_guessed(self):
        result = self.run_case('Trans_Created_At "0x1234"; echo "rc=$?"\n')
        self.assertIn("rc=1", result.stdout)

    def test_an_empty_id_is_refused(self):
        result = self.run_case('Trans_Created_At ""; echo "rc=$?"\n')
        self.assertIn("rc=1", result.stdout)

    # ---- Min_Deadline: a deadline is a minimum, never a maximum -----------

    def test_the_earliest_deadline_wins(self):
        result = self.run_case("Min_Deadline 2000 1500 1800\n")
        self.assertEqual(result.stdout.strip(), "1500")

    def test_an_unknown_window_does_not_extend_the_contract_deadline(self):
        """A caller that could not determine its window passes nothing."""
        result = self.run_case('Min_Deadline 1500 "" 0 "unknown"\n')
        self.assertEqual(result.stdout.strip(), "1500")

    def test_no_usable_deadline_is_an_error_not_an_unbounded_wait(self):
        result = self.run_case('Min_Deadline "" 0 "x"; echo "rc=$?"\n')
        self.assertIn("rc=1", result.stdout)

    # ---- Await_Effect: the wait itself -----------------------------------

    def test_an_effect_already_on_chain_returns_at_once(self):
        result = self.run_case(
            'function present() { return 0; }\n'
            'Await_Effect "the stake" $(( $(date +%s) + 60 )) 2 present\n'
            'echo "rc=$?"\n'
        )
        self.assertIn("rc=0", result.stdout)
        self.assertIn("confirmed on chain after 0s", result.stdout)

    def test_an_effect_that_appears_later_is_waited_for(self):
        result = self.run_case(
            'function appears() {\n'
            '    local count="$TEST_ROOT/count"\n'
            '    echo x >> "$count"\n'
            '    [[ $(wc -l < "$count") -ge 3 ]]\n'
            '}\n'
            'Await_Effect "the stake" $(( $(date +%s) + 600 )) 1 appears\n'
            'echo "rc=$?"\n'
        )
        self.assertIn("rc=0", result.stdout)
        self.assertIn("after 2s", result.stdout)

    def test_a_deadline_that_has_already_passed_still_gets_one_poll(self):
        """A late start must not fail an operation that already succeeded.

        This is the race that reported a successful election bid as a failure:
        the single read happened a second before inclusion.
        """
        result = self.run_case(
            'function present() { return 0; }\n'
            'Await_Effect "the bid" $(( $(date +%s) - 3600 )) 2 present\n'
            'echo "rc=$?"\n'
        )
        self.assertIn("rc=0", result.stdout)

    def test_an_absent_effect_fails_at_the_deadline(self):
        result = self.run_case(
            'function absent() { return 1; }\n'
            'Await_Effect "the stake" $(( $(date +%s) - 1 )) 2 absent\n'
            'echo "rc=$?"\n'
        )
        self.assertIn("rc=1", result.stdout)
        self.assertIn("did not take effect", result.stderr)

    def test_the_failure_tells_the_operator_what_to_look_at(self):
        result = self.run_case(
            'function absent() { return 1; }\n'
            'Await_Effect "the stake" $(( $(date +%s) - 1 )) 2 absent\n'
        )
        self.assertIn("depool info", result.stderr)
        self.assertIn("needs its remaining signatures", result.stderr)
        self.assertIn("never reached the wallet", result.stderr)

    def test_an_unreadable_chain_is_not_reported_as_an_absent_effect(self):
        """"The node never answered" and "the stake is not there" are
        different operator problems and must not share a message."""
        result = self.run_case(
            'function unreadable() { return 2; }\n'
            'Await_Effect "the stake" $(( $(date +%s) - 1 )) 2 unreadable\n'
            'echo "rc=$?"\n'
        )
        self.assertIn("rc=1", result.stdout)
        self.assertIn("did not answer", result.stderr)
        self.assertIn("state is unknown", result.stderr)
        self.assertNotIn("did not take effect", result.stderr)

    def test_a_chain_that_recovers_mid_wait_still_succeeds(self):
        result = self.run_case(
            'function flaky() {\n'
            '    local count="$TEST_ROOT/count"\n'
            '    echo x >> "$count"\n'
            '    [[ $(wc -l < "$count") -ge 3 ]] && return 0\n'
            '    return 2\n'
            '}\n'
            'Await_Effect "the stake" $(( $(date +%s) + 600 )) 1 flaky\n'
            'echo "rc=$?"\n'
        )
        self.assertIn("rc=0", result.stdout)

    def test_a_partly_unreadable_chain_reports_the_absent_effect(self):
        """One failed read among several does not make the state unknown."""
        result = self.run_case(
            'function mixed() {\n'
            '    local count="$TEST_ROOT/count"\n'
            '    echo x >> "$count"\n'
            '    [[ $(wc -l < "$count") -ge 2 ]] && return 1\n'
            '    return 2\n'
            '}\n'
            'Await_Effect "the stake" $(( $(date +%s) + 1 )) 1 mixed\n'
        )
        self.assertIn("did not take effect", result.stderr)

    def test_the_predicate_receives_its_arguments(self):
        result = self.run_case(
            'function needs_args() { [[ $1 == 0:depool && $2 == 0:wallet ]]; }\n'
            'Await_Effect "the stake" $(( $(date +%s) + 60 )) 2'
            ' needs_args 0:depool 0:wallet\n'
            'echo "rc=$?"\n'
        )
        self.assertIn("rc=0", result.stdout)

    def test_a_zero_interval_cannot_spin(self):
        result = self.run_case(
            'function absent() { return 1; }\n'
            'function sleep() { echo "slept:$1"; }\n'
            'Await_Effect "the stake" $(( $(date +%s) + 1 )) 0 absent\n'
        )
        self.assertIn("slept:1", result.stdout)

    def test_the_wait_sends_nothing(self):
        """The non-regression argument rests on this: the poll is read-only, so
        the only writes an operation makes remain its own idempotent
        confirmations."""
        text = MANAGED_OPS.read_text()
        start = text.index("function Await_Effect()")
        body = text[start : text.index("\n}\n", start)]
        for sender in ("Send_Message", "Send_MSIG_Trans_Confirmation", "sendmessage",
                       "sendfile", "CALL_CLI", "CALL_CONS"):
            self.assertNotIn(sender, body, sender)


if __name__ == "__main__":
    unittest.main()
