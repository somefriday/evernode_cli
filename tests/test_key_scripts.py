"""The key scripts never pass a seed phrase as a process argument, which every
user of the host can read; the vote engine derives key pairs from standard input."""

from pathlib import Path
import re
import unittest
from unittest.mock import MagicMock, patch

from evernode import process, scripts

KIT_SCRIPTS = Path(__file__).resolve().parents[1] / "evernode/vendor/ever-validator/scripts"
# Variables that hold phrases. Their values may only feed a builtin (echo, printf,
# [[ ]]), which starts no process, or an assignment, which no other user can read.
PHRASES = re.compile(r"\$\{?(SeedPhrase|json_array)\b")
SAFE = re.compile(r"^(echo|printf|\[\[|\w+=)")


class KeyScriptTests(unittest.TestCase):
    def test_a_seed_phrase_is_never_a_process_argument(self):
        for name in sorted(scripts.OFFLINE_SCRIPTS):
            lines = (KIT_SCRIPTS / name).read_text().splitlines()
            for number, line in enumerate(lines, 1):
                if line.lstrip().startswith("#"):
                    continue
                for match in PHRASES.finditer(line):
                    # The command this expansion belongs to: after the last pipe,
                    # command substitution or list separator, without "if" and "!".
                    words = re.split(r"\$\(|\||;|&&", line[: match.start()])[-1].split()
                    while words and words[0] in ("if", "!"):
                        words.pop(0)
                    with self.subTest(script=name, line=number):
                        self.assertTrue(words and SAFE.match(words[0]), line.strip())

    def test_the_key_scripts_need_the_engine_before_they_run(self):
        store = MagicMock()
        store.node_directory.return_value = Path("/nonexistent/validator01")
        missing = process.OperationError(
            "evernode-vote is not installed: run sudo evernode host setup"
        )
        with (
            patch("evernode.scripts.storage.validate_private_path"),
            patch("evernode.voting.find_engine", side_effect=missing),
            patch("evernode.scripts.process.execute_command") as execute,
        ):
            for name in sorted(scripts.OFFLINE_SCRIPTS):
                with self.subTest(script=name):
                    with self.assertRaisesRegex(process.OperationError, "host setup"):
                        scripts.execute_validator_script(store, {"name": "validator01"}, name)
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
