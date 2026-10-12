"""Allocated disk measurement, including incomplete scans and sparse files."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evernode import disk_usage, process


class DiskUsageTests(unittest.TestCase):
    def test_one_scan_parses_top_level_sizes_and_total(self):
        directory = Path("/state with spaces/validator01")
        output = (
            f"4096\t{directory}/node_db\0"
            f"1024\t{directory}/logs\0"
            f"2048\t{directory}/.node_db-lsync-test.staging\0"
            f"8192\t{directory}\0"
        )
        with patch(
            "evernode.disk_usage.process.execute_command",
            return_value=SimpleNamespace(stdout=output),
        ) as execute:
            sizes = disk_usage.measure_workspace(directory)
        self.assertEqual(sizes[directory], 8192)
        self.assertEqual(sizes[directory / "node_db"], 4096)
        self.assertEqual(sizes[directory / "logs"], 1024)
        execute.assert_called_once_with(
            ["du", "--block-size=1", "--max-depth=1", "--null", "--", directory],
            timeout=120,
        )

    def test_incomplete_or_invalid_output_is_not_reported_as_zero(self):
        directory = Path("/state/node")
        for output in (
            "",
            "broken\0",
            f"-1\t{directory}\0",
            f"100\t{directory}/node_db\0",
            f"100\t{directory}/node_db\0" + f"50\t{directory}\0",
            f"100\t/another/node\0" + f"200\t{directory}\0",
        ):
            with self.subTest(output=output), patch(
                "evernode.disk_usage.process.execute_command",
                return_value=SimpleNamespace(stdout=output),
            ):
                with self.assertRaises(process.OperationError):
                    disk_usage.measure_workspace(directory)

    def test_failed_scan_propagates_instead_of_returning_partial_sizes(self):
        with patch(
            "evernode.disk_usage.process.execute_command",
            side_effect=process.OperationError("du: Permission denied"),
        ):
            with self.assertRaisesRegex(process.OperationError, "Permission denied"):
                disk_usage.measure_workspace(Path("/state/node"))

    def test_binary_units(self):
        self.assertEqual(disk_usage.format_bytes(0), "0.0 B")
        self.assertEqual(disk_usage.format_bytes(1024), "1.0 KiB")
        self.assertEqual(disk_usage.format_bytes(1024**3 * 1.5), "1.5 GiB")

    def test_gnu_du_measures_sparse_files_and_does_not_follow_symlinks(self):
        du = shutil.which("du")
        if (
            not du
            or "GNU"
            not in subprocess.run(
                [du, "--version"], capture_output=True, text=True
            ).stdout
        ):
            self.skipTest("GNU du is required (provided by Ubuntu/Debian coreutils)")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "node"
            directory.mkdir()
            database = directory / "node_db"
            database.mkdir()
            sparse = database / "sparse"
            with sparse.open("wb") as stream:
                stream.seek(64 * 1024**2)
                stream.write(b"x")
            before = disk_usage.measure_workspace(directory)
            self.assertLess(before[database], sparse.stat().st_size)
            os.link(sparse, database / "hardlink")
            outside = root / "outside"
            outside.mkdir()
            (outside / "large").write_bytes(b"x" * 1024**2)
            (directory / "external").symlink_to(outside, target_is_directory=True)
            after = disk_usage.measure_workspace(directory)
            # Neither the hard link nor the symlink should add another file's allocation.
            self.assertLess(after[directory] - before[directory], 128 * 1024)
