"""Opt-in test for a disposable Ubuntu host with a compatible local image.

Creates two unfunded nodes. It does not submit elections or import wallet keys.
Run only on a test host; see documentation.md for required environment settings.
"""

import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import uuid

from evernode import cli, docker, nodes, process, storage


@unittest.skipUnless(
    os.environ.get("EVERNODE_DOCKER_TEST") == "1",
    "requires explicit disposable-host Docker opt-in",
)
class DockerIntegrationTests(unittest.TestCase):
    def test_two_nodes_keep_independent_identity_and_metrics(self):
        self.assertEqual(sys.platform, "linux", "Use a disposable Ubuntu host")
        self.assertEqual(os.geteuid(), 0, "Integration test must run as root")
        required = (
            "EVERNODE_TEST_IMAGE",
            "EVERNODE_TEST_SOURCE",
            "EVERNODE_TEST_IP",
            "EVERNODE_TEST_MEMORY",
        )
        for key in required:
            self.assertTrue(os.environ.get(key), f"Missing {key}")
        source = Path(os.environ["EVERNODE_TEST_SOURCE"]).resolve()
        self.assertTrue((source / "scripts/env.sh").is_file())
        # No ancestors writable by other users; do not place privileged state in /tmp.
        directory = Path(tempfile.mkdtemp(prefix="evernode-test-", dir="/var/lib"))
        directory.chmod(0o700)
        store = storage.NodeStateStore(directory)
        names = ["test-" + uuid.uuid4().hex[:10] for _ in range(2)]

        def command(*args):
            self.assertEqual(cli.main(["--state-dir", str(directory), *args]), 0)

        try:
            for name in names:
                command(
                    "node",
                    "create",
                    "-n",
                    name,
                    "--image",
                    os.environ["EVERNODE_TEST_IMAGE"],
                    "--project-source",
                    str(source),
                    "--ip",
                    os.environ["EVERNODE_TEST_IP"],
                    "--memory",
                    os.environ["EVERNODE_TEST_MEMORY"],
                    "--yes",
                )
                command("node", "start", "-n", name)
            configs = [store.load_node_config(n) for n in names]
            self.assertEqual(configs[0]["image_id"], configs[1]["image_id"])
            for field in ("adnl_port", "metrics_port", "statsd"):
                self.assertNotEqual(configs[0][field], configs[1][field])
            identities = [
                (store.node_directory(n) / "node_cfg/console.json").read_bytes()
                for n in names
            ]
            self.assertNotEqual(*identities)
            for c in configs:
                exporter = docker.inspect_managed_container(c["statsd"], c["name"])
                bindings = exporter["HostConfig"]["PortBindings"]
                self.assertEqual(set(bindings), {"9102/tcp"})
                self.assertEqual(bindings["9102/tcp"][0]["HostIp"], "127.0.0.1")
                process.execute_command(
                    ["curl", "-fsS", f"http://127.0.0.1:{c['metrics_port']}/metrics"]
                )
            command("node", "stop", "-n", names[0])
            self.assertEqual(nodes.get_node_status(configs[1])["container"], "running")
            command("node", "start", "-n", names[0])
            for i, name in enumerate(names):
                self.assertEqual(
                    (store.node_directory(name) / "node_cfg/console.json").read_bytes(),
                    identities[i],
                )
        finally:
            # Keep the directory if any Docker cleanup fails; never remove files
            # still mounted by a surviving test container.
            errors = []
            for name in store.list_node_names():
                try:
                    c = store.load_node_config(name)
                    docker.inspect_managed_container(name, name)
                    docker.inspect_managed_container(c["statsd"], name)
                    if (store.node_directory(name) / "compose.json").exists():
                        docker.execute_node_compose(
                            store, c, "down", "--timeout", "30", timeout=120
                        )
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
            if errors:
                raise RuntimeError(
                    f"Cleanup failed; retained {directory}: " + "; ".join(errors)
                )
            shutil.rmtree(directory)
