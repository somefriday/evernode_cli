from pathlib import Path
import sys
import tempfile
import time
import unittest

from evernode.process import execute_command, OperationError


class ProcessTests(unittest.TestCase):
    def test_real_shell_arguments_and_explicit_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "script with spaces.sh"
            script.write_text('printf "%s|%s|%s" "$PWD" "$TEST_SETTING" "$1"\n')
            marker = "$(touch do-not-create)"
            result = execute_command(
                ["bash", script, marker],
                cwd=tmp,
                env={"PATH": "/usr/bin:/bin", "TEST_SETTING": "value"},
            )
            self.assertEqual(result.stdout, f"{Path(tmp).resolve()}|value|{marker}")
            self.assertFalse((Path(tmp) / "do-not-create").exists())

    def test_script_failure_output_is_suppressed_when_sensitive(self):
        with self.assertRaises(OperationError) as error:
            execute_command(
                ["bash", "-c", "echo secret-material >&2; exit 7"], sensitive=True
            )
        self.assertIn("exit 7", str(error.exception))
        self.assertNotIn("secret-material", str(error.exception))

    def test_timeout_kills_shell_children_before_they_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "unexpected"
            program = "import time, pathlib, sys; time.sleep(0.7); pathlib.Path(sys.argv[1]).touch()"
            script = Path(tmp) / "spawn.sh"
            script.write_text('"$1" -c "$2" "$3" &\nwait\n')
            with self.assertRaisesRegex(OperationError, "timed out"):
                execute_command(
                    ["bash", script, sys.executable, program, marker], timeout=0.1
                )
            time.sleep(0.8)
            self.assertFalse(marker.exists())

    def test_missing_program_fails_without_echoing_arguments(self):
        with self.assertRaises(OperationError) as error:
            execute_command(["/no/such/executable", "private-argument"])
        self.assertNotIn("private-argument", str(error.exception))

    def test_script_input_is_noninteractive(self):
        result = execute_command(
            ["bash", "-c", "read -r answer || exit 12"], check=False
        )
        self.assertEqual(result.returncode, 12)

    def test_streaming_command_returns_a_normal_result(self):
        result = execute_command(["bash", "-c", "exit 0"], stream=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
