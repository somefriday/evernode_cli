"""Reading the elector when no bundled interface description is guaranteed right.

Different chains run different elector builds, and the description bundled
with the reference scripts does not decode all of them. That was not a
cosmetic problem: the participation check treated an unreadable answer as a
fatal fault, so a validator that had won one election could never bid again -
in every staking mode.

These tests pin the two halves of the fix: choosing a description the live
elector actually answers to, and never letting an unreadable elector stop a
node validating or make it spend a stake twice.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KIT = REPO / "vendor/ever-validator/scripts"
CONTRACTS = REPO / "vendor/ever-validator/contracts/EverX/elector"

# A decoded `get` response, shaped as the real one is.
def response(elect_at=1793451980, members=1):
    return {
        "election_open": True,
        "cur_elect": {
            "elect_at": str(elect_at),
            "elect_close": str(elect_at - 80),
            "min_stake": "1000000000000",
            "total_stake": "74491900470000",
            "members": {"1": {"addr": "2", "stake": "74491900470000"}}
            if members else {},
            "failed": True,
            "finished": False,
        },
        "credits": {},
        "past_elections": {},
        "grams": "0",
        "active_id": str(elect_at),
        "active_hash": "0",
    }


class VendoredAbiTests(unittest.TestCase):
    def test_the_node_s_own_description_is_vendored_with_its_provenance(self):
        abi = CONTRACTS / "Elector-ever-node.abi.json"
        self.assertTrue(abi.is_file())
        self.assertEqual(json.loads(abi.read_text()).get("version"), "2.2")
        notes = (CONTRACTS / "Elector-ever-node.PROVENANCE.md").read_text()
        for recorded in ("ever-node", "sha256", "d03a21da40be24b5", "2.2"):
            with self.subTest(recorded=recorded):
                self.assertIn(recorded, notes)

    def test_both_descriptions_are_offered_as_candidates(self):
        env = (KIT / "env.sh").read_text()
        self.assertIn("Elector_ABI_CANDIDATES", env)
        self.assertIn("Elector-ever-node.abi.json", env)

    def test_adding_a_description_needs_no_code_change(self):
        """The list is data, so a new elector build is a file, not a patch."""
        resolver = (KIT / "functions.shinc").read_text()
        self.assertIn("for candidate in ${Elector_ABI_CANDIDATES", resolver)


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq"), "Requires bash and jq"
)
class ResolverTests(unittest.TestCase):
    """The resolver must prove a description works, not assume it."""

    @classmethod
    def setUpClass(cls):
        text = (KIT / "functions.shinc").read_text()
        start = text.index("function Resolve_Elector_ABI() {")
        cls.function = text[start: text.index("\n}\n", start) + len("\n}\n")]

    def resolve(self, candidates):
        """candidates: name -> (get_output, active_election_id) or None to fail."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cases = {}
            for name, value in candidates.items():
                cases[name] = value
            (directory / "cases.json").write_text(json.dumps(cases))
            program = f"""set +u
function echoerr() {{ printf "%s\\n" "$*" >&2; }}
Elector_ABI_CANDIDATES="{' '.join('/abi/' + n for n in candidates)}"
function CALL_CLI_STUB() {{
    local method="" a
    for a in "$@"; do
        case "$a" in get|active_election_id) method="$a" ;; esac
    done
    local abi="${{@: -1}}"
    python3 -c '
import json, sys
cases = json.load(open(sys.argv[1]))
name = sys.argv[2].rsplit("/", 1)[-1]
method = sys.argv[3]
case = cases.get(name)
if case is None:
    sys.exit(1)
body, active = case
if method == "get":
    if body is None:
        print("Failed to decode output messages.")
        sys.exit(0)
    print(json.dumps(body))
else:
    print(json.dumps({{"value0": str(active)}}))
' "{directory}/cases.json" "$abi" "$method"
}}
CALL_CLI=CALL_CLI_STUB
{self.function}
if out=$(Resolve_Elector_ABI /tmp/elector.boc);then
    echo "RC=0 elect_at=$(jq -r .cur_elect.elect_at <<< "$out")"
else
    echo "RC=1"
fi
"""
            (directory / "case.sh").write_text(program)
            return subprocess.run(
                ["bash", str(directory / "case.sh")],
                capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=60,
            )

    def test_the_first_description_that_decodes_is_used(self):
        result = self.resolve({
            "a.abi.json": (response(), 1793451980),
            "b.abi.json": (response(elect_at=1), 1),
        })
        self.assertIn("RC=0 elect_at=1793451980", result.stdout)
        self.assertIn("elector read with a.abi.json", result.stderr)

    def test_a_description_that_cannot_decode_is_skipped_not_fatal(self):
        """This is the case that used to stop validators bidding."""
        result = self.resolve({
            "broken.abi.json": (None, 1793451980),
            "good.abi.json": (response(), 1793451980),
        })
        self.assertIn("RC=0 elect_at=1793451980", result.stdout)
        self.assertIn("elector read with good.abi.json", result.stderr)

    def test_a_description_that_decodes_into_nonsense_is_rejected(self):
        """Parsing is not understanding: the election id has to agree."""
        result = self.resolve({
            "plausible.abi.json": (response(elect_at=999), 1793451980),
            "good.abi.json": (response(), 1793451980),
        })
        self.assertIn("RC=0 elect_at=1793451980", result.stdout)
        self.assertIn("disagrees with active_election_id", result.stderr)

    def test_the_cross_check_is_skipped_when_no_election_is_open(self):
        """With active_election_id 0 there is nothing to compare against."""
        result = self.resolve({"good.abi.json": (response(elect_at=5), 0)})
        self.assertIn("RC=0", result.stdout)
        self.assertNotIn("disagrees", result.stderr)

    def test_when_nothing_decodes_it_says_so_and_names_what_it_tried(self):
        result = self.resolve({
            "one.abi.json": (None, 0),
            "two.abi.json": (None, 0),
        })
        self.assertIn("RC=1", result.stdout)
        self.assertIn("no bundled elector interface description", result.stderr)
        self.assertIn("one.abi.json", result.stderr)
        self.assertIn("two.abi.json", result.stderr)


@unittest.skipUnless(shutil.which("bash"), "Requires bash")
class UnreadableElectorTests(unittest.TestCase):
    """An elector nobody can read must not stop a node, nor double-spend.

    The participation check exists only to answer "did I already bid". When
    the chain cannot answer it, the node answers from the request file it
    writes immediately before sending a bid. Erring towards "already bid"
    costs one round; erring the other way costs the stake twice, so the file
    is trusted.
    """

    @classmethod
    def setUpClass(cls):
        text = (KIT / "take_part_in_elections.sh").read_text()
        start = text.index("        if [[ $error_code -eq 6 ]];then")
        # The real branch continues into an elif; cut before it and close the
        # if, so what runs here is the branch's own text unchanged.
        cls.branch = text[start: text.index("        elif [[ $error_code", start)] + "fi\n"

    def decide(self, *, request_on_file):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            if request_on_file:
                (directory / "1793451980_query.boc").write_bytes(b"request")
            program = f"""set -uo pipefail
SelfScriptName=take_part_in_elections.sh
error_code=6
elections_id=1793451980
CURR_ELECTIONS_DIR={str(directory)!r}
Elector_Readable=true
{self.branch}
echo "REACHED_BID readable=$Elector_Readable"
"""
            (directory / "case.sh").write_text(program)
            return subprocess.run(
                ["bash", str(directory / "case.sh")],
                capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=60,
            )

    def test_a_request_already_on_file_is_not_sent_twice(self):
        result = self.decide(request_on_file=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("already bid and will not bid again", result.stdout)
        self.assertNotIn("REACHED_BID", result.stdout)

    def test_with_no_request_on_file_the_node_still_bids(self):
        """An unreadable elector must never disable validation."""
        result = self.decide(request_on_file=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("no request on file", result.stdout)
        self.assertIn("REACHED_BID readable=false", result.stdout)

    def test_the_verdict_falls_back_to_what_the_money_did(self):
        source = (KIT / "take_part_in_elections.sh").read_text()
        self.assertIn("if ! $Elector_Readable;then", source)
        self.assertIn("Effect_Account_Balance_At_Most", source)
        # A bid that moved funds correctly is never called a failure.
        self.assertIn("so the elector accepted the bid", source)

    def test_the_two_unreadable_causes_are_reported_differently(self):
        search = (KIT / "functions.shinc").read_text()
        self.assertIn("ERR_ELECTOR_UNREADABLE=6", search)
        caller = (KIT / "take_part_in_elections.sh").read_text()
        self.assertIn("cannot be read with any bundled interface", caller)
        self.assertIn("Cannot get Elector type!", caller)


@unittest.skipUnless(shutil.which("bash"), "Requires bash")
class BalanceCeilingTests(unittest.TestCase):
    """The mirror of the arriving-payment predicate, for money leaving."""

    @classmethod
    def setUpClass(cls):
        text = (KIT / "managed_ops.shinc").read_text()
        start = text.index("function Effect_Account_Balance_At_Most() {")
        cls.function = text[start: text.index("\n}\n", start) + len("\n}\n")]

    def check(self, *, balance, ceiling):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            info = "unreadable" if balance is None else f"Active {balance} 1"
            program = f"""set +u
function Get_Account_Info() {{ {'return 1' if balance is None else f'echo "{info}"'}; }}
{self.function}
Effect_Account_Balance_At_Most 0:acct {ceiling}; echo "rc=$?"
"""
            (directory / "case.sh").write_text(program)
            return subprocess.run(
                ["bash", str(directory / "case.sh")],
                capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=30,
            ).stdout.strip()

    def test_a_balance_at_or_below_the_ceiling_is_the_effect(self):
        self.assertEqual(self.check(balance=100, ceiling=100), "rc=0")
        self.assertEqual(self.check(balance=99, ceiling=100), "rc=0")

    def test_a_balance_above_the_ceiling_is_not(self):
        self.assertEqual(self.check(balance=101, ceiling=100), "rc=1")

    def test_an_unreadable_account_is_not_reported_as_the_effect(self):
        """Code 2 keeps Await_Effect waiting instead of calling it absent."""
        self.assertEqual(self.check(balance=None, ceiling=100), "rc=2")
