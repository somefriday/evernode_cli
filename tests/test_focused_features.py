import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from evernode import (
    configuration,
    creation,
    docker,
    elections,
    host,
    images,
    nominator,
    process,
    role,
    scripts,
    storage,
    setup_actions,
    wallets,
)
from evernode.arguments import build_argument_parser
from support import PROJECT_SOURCE, make_node_configuration


class ProjectSourceTests(unittest.TestCase):
    """Several commands run scripts only evernode's own kit has, so a node
    created without a source flag gets the kit installed with evernode, and
    never a clone of the upstream one."""

    def select(self, *flags):
        args = build_argument_parser().parse_args(["node", "create", *flags])
        return creation.select_project_source(args)

    def test_the_package_holds_the_kit_and_the_contract(self):
        self.assertTrue((configuration.BUNDLED_KIT / "scripts/managed_ops.shinc").is_file())
        self.assertTrue((configuration.BUNDLED_CONTRACT_SOURCE / "PINNED.json").is_file())
        manifest = (Path(__file__).resolve().parents[1] / "MANIFEST.in").read_text()
        self.assertIn("graft evernode/vendor/ever-validator", manifest)
        self.assertIn("graft evernode/vendor/single-nominator", manifest)

    def test_without_a_source_flag_the_node_gets_the_installed_kit(self):
        self.assertEqual(self.select(), (None, None, None))

    def test_a_source_flag_is_used_as_given(self):
        self.assertEqual(
            self.select("--project-source", str(PROJECT_SOURCE)),
            (PROJECT_SOURCE, None, None),
        )
        self.assertEqual(
            self.select("--project-repo", "https://example.test/kit.git"),
            (None, "https://example.test/kit.git", None),
        )
        self.assertEqual(
            self.select("--project-ref", "v1"),
            (None, configuration.DEFAULT_ORCHESTRATION_REPOSITORY, "v1"),
        )

    def test_a_named_kit_that_is_not_there_is_refused(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaisesRegex(
                process.OperationError, "No election kit at .*leave out --project-source"
            ):
                self.select("--project-source", empty)

    def test_a_missing_installed_kit_is_refused_before_any_question(self):
        args = build_argument_parser().parse_args(["node", "create"])
        with (
            tempfile.TemporaryDirectory() as empty,
            patch.object(configuration, "BUNDLED_KIT", Path(empty) / "ever-validator"),
            patch("evernode.creation.ui.prompt_for_value",
                  side_effect=AssertionError("asked before refusing")),
        ):
            with self.assertRaisesRegex(
                process.OperationError, "kit is missing from .*reinstall evernode"
            ):
                creation.handle_node_creation(
                    SimpleNamespace(list_node_names=lambda: []), args
                )

    def test_a_nominator_on_the_installed_kit_takes_the_installed_contract(self):
        found = nominator.resolve_contract_source({
            "source_spec": dict(configuration.BUNDLED_SOURCE_SPEC),
            "project_source": "bundled",
        })
        self.assertEqual(found, configuration.BUNDLED_CONTRACT_SOURCE)

    def test_a_nominator_on_a_named_kit_takes_the_contract_beside_it(self):
        with tempfile.TemporaryDirectory() as checkout:
            beside = Path(checkout) / "single-nominator"
            beside.mkdir()
            (beside / "PINNED.json").write_text("{}")
            found = nominator.resolve_contract_source({"source_spec": {
                "local": str(Path(checkout) / "ever-validator"), "repo": None, "ref": None,
            }})
        self.assertEqual(found, beside)

    def test_a_recorded_contract_source_that_is_gone_gives_way_to_the_installed_one(self):
        """The checkout layout before the kit moved into the package recorded
        <repo>/vendor/single-nominator; the same pinned contract is installed now."""
        found = nominator.resolve_contract_source({
            "nominator_source": "/root/evernode_cli/vendor/single-nominator",
            "source_spec": {
                "local": "/root/evernode_cli/vendor/ever-validator", "repo": None, "ref": None,
            },
        })
        self.assertEqual(found, configuration.BUNDLED_CONTRACT_SOURCE)

    def test_a_named_contract_source_must_be_one(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaisesRegex(process.OperationError, "has no PINNED.json"):
                nominator.resolve_contract_source({}, requested=empty)


class FocusedFeatureTests(unittest.TestCase):
    def setUp(self):
        self.mask = os.umask(0o077)
        self.addCleanup(os.umask, self.mask)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = storage.NodeStateStore(self.tmp.name)
        guard = patch.object(storage.NodeStateStore, "validate_state_root")
        guard.start()
        self.addCleanup(guard.stop)

    def test_named_container_pair_coexists_with_older_numeric_names(self):
        directory = self.store.node_directory("validator")
        directory.mkdir(parents=True)
        config = make_node_configuration(
            "validator", container_name="ever-node-01", statsd="statsd-01"
        )
        self.store.save_node_config(config)
        with patch("evernode.docker.process.execute_command") as command:
            command.return_value.stdout = "ever-node-02\nstatsd-03\n"
            node, statsd = docker.select_container_names(self.store, "validator03")
        self.assertEqual((node, statsd), ("evernode-validator03", "statsd-validator03"))

    def test_managed_image_record_is_found_by_tag_and_short_key(self):
        inputs = {
            "node_repo": "repo",
            "node_commit": "a",
            "cli_repo": "cli",
            "cli_commit": "b",
            "rust_version": "1.90.0",
            "node_features": "statsd",
            "recipe_sha256": "c",
        }
        key = images._record_key(inputs)
        self.store.images_directory(create=True)
        record = {
            "schema_version": 1,
            "key": key,
            "inputs": inputs,
            "image": "local/ever-node:" + key[:16],
            "image_id": "sha256:" + "b" * 64,
        }
        storage.write_json_atomically(images.image_record_path(self.store, key), record)
        self.assertEqual(
            images.find_image_record(self.store, key[:12])["image_id"],
            record["image_id"],
        )
        self.assertEqual(
            images.find_image_record(self.store, record["image"])["key"], key
        )

    def test_source_checkout_initializes_recursive_submodules(self):
        commands = []

        def execute(command, **kwargs):
            commands.append((command, kwargs))
            return type("Result", (), {"stdout": "a" * 40 + "\n"})()

        destination = Path(self.tmp.name) / "source"
        with patch("evernode.images.process.execute_command", side_effect=execute):
            self.assertEqual(
                images._git_checkout(
                    "https://example.test/node.git", "revision", destination
                ),
                "a" * 40,
            )
        self.assertEqual(
            commands[0][0],
            [
                "git",
                "clone",
                "--quiet",
                "--recurse-submodules",
                "--",
                "https://example.test/node.git",
                destination,
            ],
        )
        self.assertEqual(
            commands[2][0],
            ["git", "-C", destination, "submodule", "sync", "--recursive"],
        )
        self.assertEqual(
            commands[3][0],
            ["git", "-C", destination, "submodule", "update", "--init", "--recursive"],
        )

    def test_image_build_streams_plain_docker_output(self):
        commits = iter(["n" * 40, "c" * 40])

        def checkout(_repository, _ref, destination):
            destination.mkdir(parents=True)
            (destination / "placeholder").write_text("source")
            return next(commits)

        with (
            patch("evernode.images._git_checkout", side_effect=checkout),
            patch("evernode.images.process.execute_command") as command,
            patch(
                "evernode.images.validate_node_image", return_value="sha256:" + "a" * 64
            ),
        ):
            images.build_managed_image(
                self.store,
                node_repo="node",
                node_ref="ref",
                cli_repo="cli",
                cli_ref="ref",
            )
        build = next(
            call
            for call in command.call_args_list
            if call.args[0][:2] == ["docker", "build"]
        )
        self.assertIn("--progress=plain", build.args[0])
        self.assertTrue(build.kwargs["stream"])

    @staticmethod
    def checkout(_repository, _ref, destination):
        destination.mkdir(parents=True)
        (destination / "placeholder").write_text("source")
        return ("n" if destination.name == "ever-node" else "c") * 40

    def build(self, **arguments):
        return images.build_managed_image(
            self.store, node_repo="node", node_ref="ref", cli_repo="cli", cli_ref="ref",
            **arguments,
        )

    def test_a_second_build_of_the_same_sources_is_refused_while_the_first_runs(self):
        refused = []

        def command(argv, **_options):
            if argv[:2] == ["docker", "build"] and not refused:
                # Another terminal starts the same build while this one runs.
                try:
                    self.build()
                except process.OperationError as exc:
                    refused.append(str(exc))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with (
            patch("evernode.images._git_checkout", side_effect=self.checkout),
            patch("evernode.images.process.execute_command", side_effect=command),
            patch("evernode.images.validate_node_image", return_value="sha256:" + "a" * 64),
        ):
            record, built = self.build()
            self.assertTrue(built)
            self.assertEqual(len(refused), 1)
            self.assertIn("Another build of this image is running", refused[0])
            # Once registered, the same sources are reused, not built again.
            self.assertEqual(self.build(), (record, False))

    def test_another_repository_is_another_image_and_the_default_keeps_its_key(self):
        ids = iter(["sha256:" + "a" * 64, "sha256:" + "b" * 64])
        with (
            patch("evernode.images._git_checkout", side_effect=self.checkout),
            patch("evernode.images.process.execute_command"),
            patch("evernode.images.validate_node_image", side_effect=lambda _tag: next(ids)),
        ):
            default, default_built = self.build()
            custom, custom_built = self.build(image_repo="custom/ever-node")
        self.assertTrue(default_built and custom_built)
        self.assertTrue(default["image"].startswith("local/ever-node:"))
        self.assertTrue(custom["image"].startswith("custom/ever-node:"))
        self.assertNotIn("image_repo", default["inputs"])
        self.assertEqual(custom["inputs"]["image_repo"], "custom/ever-node")

    def test_import_seeds_are_private_and_not_part_of_metadata(self):
        directory = self.store.node_directory("validator")
        (directory / "keys").mkdir(parents=True)
        config = make_node_configuration(
            "validator",
            wallet={
                "mode": "import",
                "custodians": 2,
                "required_signatures": 2,
                "wallet_address": "0:" + "a" * 64,
                "depool_address": "0:" + "b" * 64,
            },
        )
        wallets.store_import_seeds(self.store, config, ["one " * 12, "two " * 12])
        seed = directory / "keys/MSKeys_validator/validator_seed_1.txt"
        self.assertEqual(seed.stat().st_mode & 0o777, 0o600)
        self.assertEqual(seed.read_text().strip().split()[0], "one")

    def test_a_refused_phrase_leaves_every_stored_phrase_as_it_was(self):
        """A typo in the second phrase used to leave the first one replaced."""
        directory = self.store.node_directory("validator")
        (directory / "keys").mkdir(parents=True)
        config = make_node_configuration(
            "validator",
            wallet={
                "mode": "import",
                "custodians": 2,
                "required_signatures": 2,
                "wallet_address": "0:" + "a" * 64,
                "depool_address": "0:" + "b" * 64,
            },
        )
        wallets.store_import_seeds(self.store, config, ["one " * 12, "two " * 12])
        with self.assertRaisesRegex(process.OperationError, "single mnemonic phrase"):
            wallets.store_import_seeds(
                self.store, config, ["three " * 12, "too short"], replace=True
            )
        seeds = directory / "keys/MSKeys_validator"
        self.assertEqual((seeds / "validator_seed_1.txt").read_text().split()[0], "one")
        self.assertEqual((seeds / "validator_seed_2.txt").read_text().split()[0], "two")

    def test_election_schedule_uses_exact_reference_sequence(self):
        directory = self.store.node_directory("validator")
        for part in ("scripts", "logs/validator", "elections"):
            (directory / part).mkdir(parents=True, exist_ok=True)
        config = make_node_configuration("validator", validator_type="depool")
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch(
                "evernode.elections.node_election_cron_path",
                return_value=Path(temporary) / "cron",
            ),
        ):
            elections.enable_node_election_schedule(self.store, config)
            sequence = (directory / "run-election-sequence.sh").read_text()
            self.assertIn("./prepare_elections.sh", sequence)
            self.assertIn("./take_part_in_elections.sh", sequence)
            self.assertNotIn("./part_check.sh", sequence)

    def test_new_container_names_are_rendered_without_changing_validator_identity(self):
        config = make_node_configuration(
            "validator",
            container_name="ever-node-07",
            statsd="statsd-07",
            compose_project="evernode-validator",
        )
        values = configuration.build_node_environment(config)
        self.assertEqual(values["VALIDATOR_NAME"], "validator")
        self.assertEqual(values["DOCKER_NODE_CONTAINER_NAME"], "ever-node-07")
        self.assertEqual(values["DOCKER_STATSD_CONTAINER_NAME"], "statsd-07")

    def test_packaged_build_recipe_matches_repository_recipe(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            (root / "docker/ever-node/Dockerfile").read_bytes(),
            (root / "evernode/assets/ever-node.Dockerfile").read_bytes(),
        )
        self.assertEqual(
            (root / "docker/ever-node/.dockerignore").read_bytes(),
            (root / "evernode/assets/ever-node.dockerignore").read_bytes(),
        )
        recipe = (root / "docker/ever-node/Dockerfile").read_text()
        self.assertNotIn("sed -i", recipe)
        self.assertIn("CARGO_PROFILE_RELEASE_LTO=fat", recipe)

    def test_host_check_requires_the_reference_script_runtime(self):
        report = {
            "platform": "linux",
            "commands": {name: "/usr/bin/" + name for name in host.REQUIRED_COMMANDS},
            "docker": "26.0",
            "compose": "v2",
            "services": {name: "active" for name in host.REQUIRED_SERVICES},
            "vote_engine": "/usr/local/bin/evernode-vote",
        }
        self.assertTrue(host.host_dependencies_available(report))
        report["commands"]["yq"] = None
        self.assertFalse(host.host_dependencies_available(report))
        report["commands"]["yq"] = "/usr/local/bin/yq"
        report["vote_engine"] = {"error": "evernode-vote is not installed: run sudo evernode host setup"}
        self.assertFalse(host.host_dependencies_available(report))

    def test_host_check_reports_the_vote_engine_or_why_it_cannot_be_used(self):
        with patch("evernode.host.voting.find_engine", return_value="/usr/local/bin/evernode-vote"):
            self.assertEqual(host.vote_engine(), "/usr/local/bin/evernode-vote")
        missing = process.OperationError("evernode-vote is not installed: run sudo evernode host setup")
        with patch("evernode.host.voting.find_engine", side_effect=missing):
            self.assertEqual(host.vote_engine(), {"error": str(missing)})

    def test_the_vote_engine_this_evernode_needs_is_not_built_again(self):
        with (
            patch("evernode.host.voting.find_engine", return_value="/usr/local/bin/evernode-vote"),
            patch("evernode.host.process.execute_command") as execute,
        ):
            host.install_vote_engine()
        execute.assert_not_called()

    def test_a_missing_or_other_vote_engine_is_built_from_the_installed_sources(self):
        other = process.OperationError(
            "/usr/local/bin/evernode-vote is evernode-vote 0.2.0; this evernode needs "
            "evernode-vote 0.2.4: run sudo evernode host setup"
        )
        with tempfile.TemporaryDirectory() as temporary:
            sources = Path(temporary)
            (sources / "Cargo.toml").write_text("")
            with (
                patch("evernode.host.ENGINE_SOURCES", sources),
                patch(
                    "evernode.host.voting.find_engine",
                    side_effect=[other, "/usr/local/bin/evernode-vote"],
                ) as find,
                patch("evernode.host.process.execute_command") as execute,
                contextlib.redirect_stderr(io.StringIO()) as err,
            ):
                host.install_vote_engine()
        execute.assert_called_once_with(
            ["docker", "build", "--output", "/usr/local/bin", sources], timeout=3600, stream=True
        )
        self.assertEqual(find.call_count, 2)
        self.assertIn("Building evernode-vote 0.2.4 with Docker", err.getvalue())

    def test_a_build_that_leaves_another_engine_fails(self):
        other = process.OperationError(
            "/usr/bin/evernode-vote is evernode-vote 0.2.0; this evernode needs "
            "evernode-vote 0.2.4: run sudo evernode host setup"
        )
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / "Cargo.toml").write_text("")
            with (
                patch("evernode.host.ENGINE_SOURCES", Path(temporary)),
                patch("evernode.host.voting.find_engine", side_effect=other),
                patch("evernode.host.process.execute_command"),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(process.OperationError) as caught,
            ):
                host.install_vote_engine()
        self.assertIn("/usr/bin/evernode-vote is evernode-vote 0.2.0", str(caught.exception))

    def test_an_install_without_the_engine_sources_says_so(self):
        missing = process.OperationError("evernode-vote is not installed: run sudo evernode host setup")
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("evernode.host.ENGINE_SOURCES", Path(temporary) / "vote"),
                patch("evernode.host.voting.find_engine", side_effect=missing),
                patch("evernode.host.process.execute_command") as execute,
                self.assertRaises(process.OperationError) as caught,
            ):
                host.install_vote_engine()
        self.assertIn("installed without the vote engine's sources", str(caught.exception))
        execute.assert_not_called()

    def test_a_rebuild_holds_only_what_the_checkout_holds(self):
        """pip builds inside the checkout and reuses build/: a file a newer
        version deleted must not be built, installed and synced to nodes again."""
        try:
            import setuptools  # noqa: F401
        except ImportError:
            self.skipTest("setuptools is not installed")
        root = Path(__file__).resolve().parents[1]
        ignore = shutil.ignore_patterns("__pycache__", "node_modules", "target", "runs", ".DS_Store")
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary) / "checkout"
            checkout.mkdir()
            for name in ("setup.py", "MANIFEST.in", "pyproject.toml"):
                shutil.copy2(root / name, checkout / name)
            for name in ("evernode", "vote"):
                shutil.copytree(root / name, checkout / name, ignore=ignore)
            built = checkout / "build" / "lib" / "evernode"
            stale = [built / "retired.py", built / "vendor/ever-validator/scripts/retired.sh"]
            for path in stale:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("left by an earlier build\n")
            result = subprocess.run(
                [sys.executable, "setup.py", "-q", "build_py"],
                cwd=checkout, capture_output=True, text=True, timeout=300,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            for path in stale:
                self.assertFalse(path.exists(), f"{path.name} survived the rebuild")
            for kept in ("cli.py", "vendor/ever-validator/scripts/functions.shinc", "vendor/vote/Cargo.toml"):
                self.assertTrue((built / kept).is_file(), f"{kept} is missing from the build")

    def test_a_build_into_the_checkout_itself_clears_nothing(self):
        try:
            from distutils.core import run_setup
        except ImportError:
            self.skipTest("setuptools is not installed")
        root = Path(__file__).resolve().parents[1]
        # A checkout of its own, so the build step runs for real and touches nothing else.
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            shutil.copy2(root / "setup.py", checkout / "setup.py")
            shutil.copytree(
                root / "vote", checkout / "vote", ignore=shutil.ignore_patterns("target", "runs")
            )
            source = checkout / "evernode" / "cli.py"
            source.parent.mkdir()
            source.write_text("# the checkout's own module\n")
            stale = checkout / "build" / "evernode" / "retired.py"
            stale.parent.mkdir(parents=True)
            stale.write_text("left by an earlier build\n")
            distribution = run_setup(str(checkout / "setup.py"), stop_after="init")
            for build_lib in (checkout, checkout / "build"):
                command = distribution.cmdclass["build_py"](distribution)
                command.build_lib = str(build_lib)
                with patch("setuptools.command.build_py.build_py.run"):
                    command.run()
            self.assertTrue(source.is_file(), "the checkout's own package was cleared")
            self.assertFalse(stale.exists())

    def test_the_package_build_carries_what_the_engine_build_reads(self):
        """setup.py copies vote/'s build inputs into the package; host setup builds from them."""
        import ast
        import re

        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "setup.py").read_text())
        inputs = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and getattr(node.targets[0], "id", None) == "ENGINE_INPUTS"
        )
        # Every file the engine compiles in is under one of the inputs.
        for path in (root / "vote" / "src").rglob("*.rs"):
            for name in re.findall(r'include_(?:str|bytes)!\("([^"]+)"\)', path.read_text()):
                included = (path.parent / name).resolve().relative_to((root / "vote").resolve())
                self.assertIn(included.parts[0], inputs, f"{path.name} includes {name}")
        # The sdist carries them too.
        manifest = (root / "MANIFEST.in").read_text()
        for name in inputs:
            self.assertIn(f"vote/{name}", manifest)
        # The build step copies exactly them, without the checkout's other files.
        try:
            from distutils.core import run_setup
        except ImportError:
            self.skipTest("setuptools is not installed")
        with tempfile.TemporaryDirectory() as temporary:
            distribution = run_setup(str(root / "setup.py"), stop_after="init")
            command = distribution.cmdclass["build_py"](distribution)
            command.build_lib = temporary
            with patch("setuptools.command.build_py.build_py.run"):
                command.run()
            copied = Path(temporary) / "evernode" / "vendor" / "vote"
            self.assertEqual(sorted(p.name for p in copied.iterdir()), sorted(inputs))
            for name in inputs:
                source = root / "vote" / name
                files = [p for p in source.rglob("*") if p.is_file() and not p.name.startswith(".")] if source.is_dir() else [source]
                for item in files:
                    self.assertEqual(
                        (copied / item.relative_to(root / "vote")).read_bytes(), item.read_bytes()
                    )

    def test_host_setup_plan_identifies_debian_and_ubuntu(self):
        with patch(
            "evernode.host._os_release",
            return_value={"ID": "debian", "VERSION_CODENAME": "trixie"},
        ):
            self.assertIn("Docker's official Debian repository", host.get_setup_plan())
        with patch(
            "evernode.host._os_release",
            return_value={"ID": "ubuntu", "VERSION_CODENAME": "noble"},
        ):
            self.assertIn("Docker's official Ubuntu repository", host.get_setup_plan())
            self.assertIn(
                "build the vote engine evernode-vote 0.2.4 into /usr/local/bin with Docker",
                host.get_setup_plan(),
            )

    def test_host_setup_builds_the_vote_engine_once_docker_runs(self):
        order = []

        def execute(command, **_kwargs):
            order.append(" ".join(str(part) for part in command[:3]))
            architecture = "amd64\n" if command[:2] == ["dpkg", "--print-architecture"] else ""
            return SimpleNamespace(stdout=architecture, stderr="", returncode=0)

        report = {
            "platform": "linux",
            "commands": {name: "/usr/bin/" + name for name in host.REQUIRED_COMMANDS},
            "docker": "27.0",
            "compose": "v2",
            "services": {name: "active" for name in host.REQUIRED_SERVICES},
            "vote_engine": "/usr/local/bin/evernode-vote",
        }
        with (
            patch(
                "evernode.host._os_release",
                return_value={"ID": "ubuntu", "VERSION_CODENAME": "noble"},
            ),
            patch("evernode.host._find_command", return_value="/usr/bin/docker"),
            patch("evernode.host.process.execute_command", side_effect=execute),
            patch("evernode.host.install_vote_engine", side_effect=lambda: order.append("engine")),
            patch(
                "evernode.host.collect_host_dependency_report",
                side_effect=lambda: order.append("check") or report,
            ),
            patch("os.chmod"),
        ):
            self.assertEqual(host.install_host_dependencies(), report)
        self.assertEqual(order[-3:], ["systemctl enable --now", "engine", "check"])

    def test_host_setup_configures_debian_docker_repository(self):
        commands = []

        def execute(command, **_kwargs):
            commands.append(command)
            if command[:2] == ["dpkg", "--print-architecture"]:
                return type(
                    "Result", (), {"stdout": "amd64\n", "stderr": "", "returncode": 0}
                )()
            return type("Result", (), {"stdout": "", "stderr": "", "returncode": 0})()

        report = {
            "platform": "linux",
            "commands": {name: "/usr/bin/" + name for name in host.REQUIRED_COMMANDS},
            "docker": "27.0",
            "compose": "v2",
            "services": {name: "active" for name in host.REQUIRED_SERVICES},
            "vote_engine": "/usr/local/bin/evernode-vote",
        }
        with (
            patch(
                "evernode.host._os_release",
                return_value={"ID": "debian", "VERSION_CODENAME": "trixie"},
            ),
            patch("evernode.host._find_command", return_value=None),
            patch("evernode.host.process.execute_command", side_effect=execute),
            patch("evernode.host.collect_host_dependency_report", return_value=report),
            patch("evernode.host.install_vote_engine"),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text") as write_source,
            patch("os.chmod"),
        ):
            self.assertEqual(host.install_host_dependencies(), report)
        self.assertIn("https://download.docker.com/linux/debian/gpg", str(commands))
        self.assertIn("docker-ce", str(commands))
        self.assertIn(
            "https://download.docker.com/linux/debian trixie stable",
            write_source.call_args.args[0],
        )

    def test_host_setup_adds_missing_plugins_without_reinstalling_engine(self):
        commands = []

        def execute(command, **_kwargs):
            commands.append(command)
            if command[:3] in (
                ["docker", "compose", "version"],
                ["docker", "buildx", "version"],
            ):
                return type(
                    "Result", (), {"stdout": "", "stderr": "", "returncode": 1}
                )()
            if command[:2] == ["dpkg", "--print-architecture"]:
                return type(
                    "Result", (), {"stdout": "amd64\n", "stderr": "", "returncode": 0}
                )()
            return type("Result", (), {"stdout": "", "stderr": "", "returncode": 0})()

        report = {
            "platform": "linux",
            "commands": {name: "/usr/bin/" + name for name in host.REQUIRED_COMMANDS},
            "docker": "27.0",
            "compose": "v2",
            "services": {name: "active" for name in host.REQUIRED_SERVICES},
            "vote_engine": "/usr/local/bin/evernode-vote",
        }
        with (
            patch(
                "evernode.host._os_release",
                return_value={"ID": "ubuntu", "VERSION_CODENAME": "noble"},
            ),
            patch("evernode.host._find_command", return_value="/usr/bin/docker"),
            patch("evernode.host.process.execute_command", side_effect=execute),
            patch("evernode.host.collect_host_dependency_report", return_value=report),
            patch("evernode.host.install_vote_engine"),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("os.chmod"),
        ):
            self.assertEqual(host.install_host_dependencies(), report)
        installs = [
            command
            for command in commands
            if command[:3] == ["apt-get", "install", "-y"]
        ]
        self.assertTrue(
            any(
                "docker-compose-plugin" in command and "docker-buildx-plugin" in command
                for command in installs
            )
        )
        self.assertFalse(any("docker-ce" in command for command in installs))

    def created_node(self):
        """What node create leaves: a running node with no validator role yet."""
        self.store.node_directory("validator").mkdir(parents=True)
        self.store.save_node_config(
            make_node_configuration(
                "validator", phase="console-ready", setup_stage="node-started"
            )
        )

    def setup_arguments(self, *flags):
        return build_argument_parser().parse_args(
            ["node", "setup", "-n", "validator", *flags]
        )

    def test_import_does_not_prompt_for_new_depool_terms(self):
        self.created_node()
        args = self.setup_arguments(
            "--import-wallet",
            "--wallet-address",
            "0:" + "a" * 64,
            "--depool-address",
            "0:" + "b" * 64,
            "--custodians",
            "3",
            "--required-signatures",
            "2",
            "--dry-run",
        )
        with (
            patch("evernode.role.sys.stdin.isatty", return_value=True),
            patch(
                "evernode.role.ui.prompt_for_value",
                side_effect=lambda value, _label, default=None: (
                    value if value is not None else default
                ),
            ),
            patch(
                "evernode.role.ui.prompt_integer",
                side_effect=lambda value, label, default: (
                    default if value is None else value
                ),
            ) as integer,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(role.handle_node_setup(self.store, "validator", args), 0)
        # The workchain is a fixed choice and is selected, not typed, so the
        # only value still entered as an integer here is the cron interval.
        self.assertEqual(
            [call.args[1] for call in integer.call_args_list],
            ["Election check interval in minutes"],
        )

    def test_import_prompt_labels_depool_address_as_required(self):
        self.created_node()
        args = self.setup_arguments(
            "--import-wallet",
            "--wallet-address",
            "0:" + "a" * 64,
            "--custodians",
            "3",
            "--required-signatures",
            "2",
            "--dry-run",
        )
        with (
            patch("evernode.role.sys.stdin.isatty", return_value=True),
            patch(
                "evernode.role.ui.prompt_for_value",
                side_effect=lambda value, _label, default=None: (
                    value if value is not None else default
                ),
            ),
            patch(
                "evernode.role.ui.prompt_integer",
                side_effect=lambda value, _label, default: (
                    default if value is None else value
                ),
            ),
            patch("builtins.input", return_value="") as prompt,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(process.OperationError, "requires expected"):
                role.handle_node_setup(self.store, "validator", args)
        prompt.assert_called_once_with("Existing DePool address: ")

    def test_failed_node_create_discards_its_partial_workspace(self):
        args = build_argument_parser().parse_args(
            [
                "node",
                "create",
                "-n",
                "validator",
                "--image",
                "local/ever-node:abc",
                "--ip",
                "203.0.113.5",
                "--memory",
                "40G",
                "--network",
                "main",
                "--yes",
            ]
        )
        record = {"image": "local/ever-node:abc", "image_id": "sha256:" + "a" * 64}

        def fail_after_reserving(store, config, *arguments):
            config["phase"] = "preparing"
            raise process.OperationError("preparation failed")

        with (
            patch(
                "evernode.creation.docker.select_container_names",
                return_value=("ever-node-01", "statsd-01"),
            ),
            patch(
                "evernode.creation.docker.select_available_node_port",
                side_effect=(58888, 9102),
            ),
            patch("evernode.creation.images.find_image_record", return_value=record),
            patch(
                "evernode.creation.images.inspect_recorded_image", return_value=record
            ),
            patch(
                "evernode.creation.provisioning.provision_node_workspace",
                side_effect=fail_after_reserving,
            ),
            patch("evernode.creation.nodes.discard_failed_creation") as discard,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with self.assertRaisesRegex(process.OperationError, "preparation failed"):
                creation.handle_node_creation(self.store, args)
        discard.assert_called_once()

    def test_current_vendored_env_accepts_all_rendered_values(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "env.sh"
            target.write_bytes(
                (root / "evernode/vendor/ever-validator/scripts/env.sh").read_bytes()
            )
            config = make_node_configuration(
                "validator",
                container_name="ever-node-01",
                statsd="statsd-01",
                compose_project="evernode-validator",
            )
            configuration.write_node_environment(
                target, configuration.build_node_environment(config)
            )
            rendered = target.read_text()
            self.assertIn("export DOCKER_NODE_CONTAINER_NAME=ever-node-01", rendered)
            self.assertIn("export DOCKER_STATSD_CONTAINER_NAME=statsd-01", rendered)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_wallet_create_keeps_phrases_out_of_result_and_advances_one_stage(self):
        config = make_node_configuration(
            "validator",
            wallet={
                "mode": "new",
                "custodians": 2,
                "required_signatures": 2,
                "wallet_address": None,
                "depool_address": None,
            },
            setup_stage="node-started",
        )
        details = {
            "address": "0:" + "a" * 64,
            "public_keys": ["public-1"],
            "key_directory": "/private",
        }
        with (
            patch("evernode.wallets.prepare_wallet", return_value=details),
            patch(
                "evernode.wallets._new_wallet_phrases",
                return_value=["one " * 12, "two " * 12],
            ),
        ):
            updated, result, phrases = wallets.wallet_create(self.store, config)
        self.assertEqual(updated["setup_stage"], "wallet-created")
        self.assertEqual(updated["wallet"]["wallet_address"], details["address"])
        self.assertNotIn("one one", str(result))
        self.assertEqual(len(phrases), 2)
        self.assertIn("evernode wallet deploy", result["next"])

    def test_wallet_create_displays_generated_phrases_on_the_terminal(self):
        phrase = "one " * 12
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            setup_actions._print_new_secrets("Safe wallet", "0:" + "a" * 64, [phrase])
        self.assertIn("Safe wallet seed phrase 1: " + phrase, output.getvalue())
        self.assertIn("Safe wallet address: 0:" + "a" * 64, output.getvalue())

        config = make_node_configuration(
            "validator",
            wallet={
                "mode": "new",
                "custodians": 1,
                "required_signatures": 1,
                "wallet_address": None,
                "depool_address": None,
            },
            setup_stage="node-started",
        )
        self.store.node_directory("validator").mkdir(parents=True)
        self.store.save_node_config(config)
        args = SimpleNamespace(
            name="validator", group="wallet", action="create", yes=True
        )
        result = {
            "stage": "wallet-created",
            "wallet": {"address": "0:" + "a" * 64},
            "next": "evernode wallet deploy -n validator",
        }
        with (
            patch("evernode.setup_actions.sys.stdin") as stdin,
            patch("evernode.setup_actions.sys.stdout") as stdout,
            patch(
                "evernode.setup_actions._require_setup_node",
                return_value=config,
            ),
            patch("evernode.setup_actions.ui.confirm_operation"),
            patch(
                "evernode.wallets.wallet_create",
                return_value=(config, result, [phrase]),
            ),
            patch("evernode.setup_actions._print_new_secrets") as show_secrets,
            patch("evernode.setup_actions._acknowledge_secret_backup"),
        ):
            stdin.isatty.return_value = True
            stdout.isatty.return_value = True
            self.assertEqual(setup_actions.handle_setup_action(self.store, args), 0)
        show_secrets.assert_called_once_with(
            "Safe wallet", result["wallet"]["address"], [phrase]
        )

    def test_each_fresh_action_runs_one_reference_script_and_requires_its_stage(self):
        profile = {
            "mode": "new",
            "custodians": 3,
            "required_signatures": 2,
            "wallet_address": "0:" + "a" * 64,
            "depool_address": "0:" + "b" * 64,
        }
        deploy_wallet = make_node_configuration(
            "validator", wallet=dict(profile), setup_stage="wallet-created"
        )
        deploy_depool = make_node_configuration(
            "validator", wallet=dict(profile), setup_stage="depool-prepared"
        )
        stake = make_node_configuration(
            "validator",
            wallet=dict(profile),
            setup_stage="depool-deployed",
            settings={"ValidatorAssuranceT": 50000},
        )
        with patch("evernode.wallets.scripts.execute_validator_script") as execute, patch(
            "evernode.wallets.scripts.read_account_status", return_value="Uninit"
        ), patch(
            # Checking a deployed Safe's custodians is covered separately.
            "evernode.wallets.require_own_safe"
        ), patch(
            # Checking the DePool's proxies is covered separately (ELECT-8).
            "evernode.wallets.require_live_proxies"
        ), patch(
            # Waiting for a deployed contract to appear is covered separately.
            "evernode.wallets.scripts.wait_for_active_account"
        ) as awaited, patch(
            "evernode.wallets.scripts.verify_imported_depool",
            return_value={"validator_wallet": profile["wallet_address"]},
        ):
            self.assertEqual(
                wallets.wallet_deploy(self.store, deploy_wallet)[0]["setup_stage"],
                "wallet-deployed",
            )
            self.assertEqual(
                wallets.depool_deploy(self.store, deploy_depool)[0]["setup_stage"],
                "depool-deployed",
            )
            self.assertEqual(
                wallets.depool_stake_initial(self.store, stake)[0]["setup_stage"],
                "ready-for-elections",
            )
        self.assertEqual(
            [call.args[2] for call in awaited.call_args_list],
            [profile["wallet_address"], profile["depool_address"]],
        )
        self.assertEqual(
            [call.args[2] for call in execute.call_args_list],
            ["Msig_deploy.sh", "DePool_deploy.sh", "stake_to_depool.sh"],
        )
        with self.assertRaisesRegex(process.OperationError, "requires setup stage"):
            wallets.depool_deploy(self.store, deploy_wallet)

    def test_a_depool_whose_constructor_failed_is_not_marked_deployed(self):
        # review 1, #27 (bd-j vdp3): the deploy activated the account, but the constructor failed (exit 507)
        profile = {
            "mode": "new",
            "custodians": 1,
            "required_signatures": 1,
            "wallet_address": "0:" + "a" * 64,
            "depool_address": "0:" + "b" * 64,
        }
        config = make_node_configuration("validator", wallet=dict(profile), setup_stage="depool-prepared")
        with patch("evernode.wallets.scripts.execute_validator_script"), patch(
            "evernode.wallets.scripts.read_account_status", return_value="Uninit"
        ), patch(
            "evernode.wallets.scripts.wait_for_active_account"
        ), patch(
            "evernode.wallets.scripts.verify_imported_depool",
            return_value={"validator_wallet": "0:" + "0" * 64},
        ):
            with self.assertRaisesRegex(process.OperationError, "constructor did not complete") as raised:
                wallets.depool_deploy(self.store, config)
        self.assertIn("cannot be deployed again", str(raised.exception))
        self.assertEqual(config["setup_stage"], "depool-prepared")

    # A deploy whose wait ran out, or whose session dropped, may have landed:
    # running it again checks the contract instead of failing in the kit script.

    DEPLOY_PROFILE = {
        "mode": "new",
        "custodians": 2,
        "required_signatures": 2,
        "wallet_address": "0:" + "a" * 64,
        "depool_address": "0:" + "b" * 64,
    }

    def deploy_keys(self, *publics):
        keys = self.store.node_directory("validator") / "keys"
        keys.mkdir(parents=True, exist_ok=True, mode=0o700)
        for index, public in enumerate(publics, 1):
            path = keys / f"validator_{index}.keys.json"
            path.write_text(json.dumps({"public": public}))
            path.chmod(0o600)

    def deploy_wallet(self, statuses, safe, script=None):
        config = make_node_configuration(
            "validator", wallet=dict(self.DEPLOY_PROFILE), setup_stage="wallet-created"
        )
        with patch(
            "evernode.wallets.scripts.read_account_status", side_effect=list(statuses)
        ), patch(
            "evernode.wallets.scripts.execute_validator_script", side_effect=script
        ) as execute, patch("evernode.wallets.scripts.wait_for_active_account"), patch(
            "evernode.wallets.scripts.verify_imported_safe", **safe
        ):
            try:
                wallets.wallet_deploy(self.store, config)
            except process.OperationError as exc:
                return config, execute, str(exc)
        return config, execute, None

    def test_an_active_safe_with_this_nodes_keys_finishes_the_stage(self):
        self.deploy_keys("0x01", "0x02")
        observed = {"custodians": 2, "required_signatures": 2, "public_keys": ["01", "02"]}
        config, execute, error = self.deploy_wallet(["Active"], {"return_value": observed})
        self.assertIsNone(error)
        execute.assert_not_called()
        self.assertEqual(config["setup_stage"], "wallet-deployed")

    def test_an_active_safe_set_up_otherwise_is_refused(self):
        self.deploy_keys("0x01", "0x02")
        for observed, reason in (
            ({"custodians": 2, "required_signatures": 1, "public_keys": ["01", "02"]},
             "needs 1 signatures, not the 2 and 2"),
            ({"custodians": 2, "required_signatures": 2, "public_keys": ["01", "03"]},
             "custodian keys are not this node's keys"),
        ):
            with self.subTest(reason=reason):
                config, _, error = self.deploy_wallet(["Active"], {"return_value": observed})
                self.assertIn(reason, error)
                self.assertIn("this node cannot use that Safe", error)
                self.assertNotIn("Imported", error)
                self.assertEqual(config["setup_stage"], "wallet-created")

    def test_a_safe_that_cannot_be_read_is_checked_again_by_the_next_run(self):
        config, _, error = self.deploy_wallet(
            ["Active"], {"side_effect": process.OperationError("console timed out")}
        )
        self.assertIn("could not be read (console timed out)", error)
        self.assertIn("run evernode wallet deploy -n validator again", error)
        self.assertEqual(config["setup_stage"], "wallet-created")

    def test_a_message_that_lands_while_the_script_starts_is_not_a_failure(self):
        """The earlier run's deploy executes between the first read and the script's own."""
        self.deploy_keys("0x01", "0x02")
        observed = {"custodians": 2, "required_signatures": 2, "public_keys": ["01", "02"]}
        config, execute, error = self.deploy_wallet(
            ["Uninit", "Active"], {"return_value": observed},
            script=process.OperationError("Msig_deploy.sh failed (exit 1)"),
        )
        self.assertIsNone(error)
        execute.assert_called_once()
        self.assertEqual(config["setup_stage"], "wallet-deployed")

    def test_a_failed_deploy_of_an_inactive_account_still_fails(self):
        config, _, error = self.deploy_wallet(
            ["Uninit", "Uninit"], {"return_value": {}},
            script=process.OperationError("Msig_deploy.sh failed (exit 1)"),
        )
        self.assertEqual(error, "Msig_deploy.sh failed (exit 1)")
        self.assertEqual(config["setup_stage"], "wallet-created")

    def test_a_depool_that_does_not_answer_is_not_declared_dead(self):
        config = make_node_configuration(
            "validator", wallet=dict(self.DEPLOY_PROFILE), setup_stage="depool-prepared"
        )
        with patch("evernode.wallets.scripts.read_account_status", return_value="Active"), patch(
            "evernode.wallets.scripts.execute_validator_script"
        ) as execute, patch("evernode.wallets.scripts.wait_for_active_account"), patch(
            "evernode.wallets.scripts.verify_imported_depool",
            side_effect=process.OperationError("getter failed"),
        ):
            with self.assertRaises(process.OperationError) as raised:
                wallets.depool_deploy(self.store, config)
        message = str(raised.exception)
        execute.assert_not_called()
        self.assertIn("did not answer (getter failed)", message)
        self.assertIn("run evernode depool deploy -n validator again", message)
        self.assertIn("may not have been constructed", message)
        self.assertNotIn("constructor did not complete", message)
        self.assertEqual(config["setup_stage"], "depool-prepared")

    def test_port_checks_read_every_binding_docker_records(self):
        """docker run -p 8080 records an empty host port; -p 18000-18002:80 a range."""
        container = {
            "HostConfig": {"PortBindings": {
                "8080/tcp": [{"HostIp": "", "HostPort": ""}],
                "80/tcp": [{"HostIp": "", "HostPort": "18000-18002"}],
                "9000/tcp": [{"HostIp": "", "HostPort": "9102"}],
                "30303/udp": [{"HostIp": "", "HostPort": "58888"}],
            }},
            "NetworkSettings": {"Ports": {
                "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "51040"},
                             {"HostIp": "::", "HostPort": "51040"}],
                "443/tcp": None,
            }},
        }
        self.assertEqual(
            docker._bound_host_ports(container, "tcp"), {9102, 18000, 18001, 18002, 51040}
        )
        self.assertEqual(docker._bound_host_ports(container, "udp"), {58888})
        self.assertEqual(docker._bound_host_ports({"HostConfig": None}, "tcp"), set())

    def test_a_random_port_container_does_not_stop_node_create(self):
        container = {"HostConfig": {"PortBindings": {"8080/tcp": [{"HostIp": "", "HostPort": ""}]}}}
        answers = [SimpleNamespace(stdout="abc\n"), SimpleNamespace(stdout=json.dumps([container]))]
        with patch("evernode.docker.process.execute_command", side_effect=answers):
            self.assertTrue(docker.is_host_port_available(9102, "tcp"))

    def test_import_verification_is_split_between_safe_and_depool(self):
        profile = {
            "mode": "import",
            "custodians": 3,
            "required_signatures": 2,
            "wallet_address": "0:" + "a" * 64,
            "depool_address": "0:" + "b" * 64,
        }
        recovered = make_node_configuration(
            "validator", wallet=dict(profile), setup_stage="wallet-recovered"
        )
        verified = make_node_configuration(
            "validator", wallet=dict(profile), setup_stage="wallet-verified"
        )
        keys = self.store.node_directory("validator") / "keys"
        keys.mkdir(parents=True)
        for index, public in enumerate(("1" * 64, "2" * 64, "3" * 64), 1):
            (keys / f"validator_{index}.keys.json").write_text(
                json.dumps({"public": public, "secret": "private"})
            )
        with (
            patch(
                "evernode.wallets.scripts.verify_imported_safe",
                return_value={
                    "custodians": 3,
                    "required_signatures": 2,
                    "public_keys": ["1" * 64, "2" * 64, "3" * 64],
                },
            ) as safe,
            patch(
                "evernode.wallets.scripts.verify_imported_depool",
                return_value={"validator_wallet": profile["wallet_address"]},
            ) as depool,
        ):
            self.assertEqual(
                wallets.wallet_verify(self.store, recovered)[0]["setup_stage"],
                "wallet-verified",
            )
            self.assertEqual(
                wallets.depool_verify(self.store, verified)[0]["setup_stage"],
                "imported",
            )
        safe.assert_called_once()
        depool.assert_called_once()
        self.assertEqual(
            (keys / "depool.addr").read_text().strip(), profile["depool_address"]
        )

    def test_imported_wallet_uses_verified_custodians_not_deployment_key(self):
        profile = {
            "mode": "import",
            "custodians": 3,
            "required_signatures": 2,
            "wallet_address": "0:" + "a" * 64,
            "depool_address": "0:" + "b" * 64,
        }
        config = make_node_configuration(
            "validator", wallet=profile, setup_stage="node-started"
        )
        keys = self.store.node_directory("validator") / "keys"
        source = keys / "MSKeys_validator"
        source.mkdir(parents=True)
        for index, public in enumerate(("1" * 64, "2" * 64, "3" * 64), 1):
            (source / f"validator_{index}.keys.json").write_text(
                json.dumps({"public": public, "secret": "private"})
            )
        (keys / "validator.addr").write_text("0:" + "c" * 64 + "\n")
        details = {
            "address": profile["wallet_address"],
            "public_keys": ["1" * 64, "2" * 64, "3" * 64],
            "key_directory": str(source),
        }
        observed = {
            "custodians": 3,
            "required_signatures": 2,
            "public_keys": ["3" * 64, "1" * 64, "2" * 64],
        }
        with (
            patch("evernode.wallets.prepare_wallet", return_value=details),
            patch(
                "evernode.wallets.scripts.verify_imported_safe", return_value=observed
            ),
        ):
            updated, result = wallets.wallet_recover(self.store, config)
        self.assertEqual(updated["setup_stage"], "wallet-recovered")
        self.assertEqual(result["wallet"]["address"], profile["wallet_address"])
        self.assertEqual(
            (keys / "validator.addr").read_text().strip(), profile["wallet_address"]
        )
        self.assertEqual(
            json.loads((keys / "validator_1.keys.json").read_text())["public"], "1" * 64
        )

    def test_wallet_verifier_parses_decimal_on_chain_custodian_keys(self):
        config = make_node_configuration("validator")
        output = json.dumps(
            {
                "custodians": [
                    {
                        "index": "2",
                        "pubkey": "15093806633510367186719388192793368257920671216233184067238754526642486242420",
                    },
                    {
                        "index": "1",
                        "pubkey": "19086442550313926316359400052600843615723821204819734974451688910555339614790",
                    },
                    {
                        "index": "0",
                        "pubkey": "49076393504541418186784131125560371494484552085723361103046486474514097973445",
                    },
                ],
                "required_signatures": "2",
            }
        )
        with (
            patch(
                "evernode.scripts._import_verification_context",
                return_value=self.store.root,
            ),
            patch(
                "evernode.scripts.process.execute_command",
                return_value=SimpleNamespace(stdout=output),
            ),
        ):
            observed = scripts.verify_imported_safe(self.store, config, "0:" + "a" * 64)
        self.assertEqual(observed["required_signatures"], 2)
        self.assertEqual(
            observed["public_keys"],
            [
                "215ecab405e57627be949d3ebf50a9cc27838f3f67b8f474ea222ae195cdec74",
                "2a328b2d060eeda22426d9488d67be96c6b00930aa2e3a536426bb6fc4f4ea46",
                "6c80411e67f06e5a966ca501ef8879e3c7d3d4efb3cbe0d500fc9195646f28c5",
            ],
        )

    def test_recovery_refuses_missing_seed_before_reference_script(self):
        profile = {
            "mode": "import",
            "custodians": 3,
            "required_signatures": 2,
            "wallet_address": "0:" + "a" * 64,
            "depool_address": "0:" + "b" * 64,
        }
        config = make_node_configuration(
            "validator", wallet=profile, setup_stage="node-started"
        )
        with (
            patch("evernode.wallets.scripts.execute_validator_script") as execute,
            self.assertRaisesRegex(
                process.OperationError, "Missing imported seed file 1"
            ),
        ):
            wallets.wallet_recover(self.store, config)
        execute.assert_not_called()

    def test_imported_wallet_preparation_accepts_another_deployment_key(self):
        profile = {
            "mode": "import",
            "custodians": 3,
            "required_signatures": 2,
            "wallet_address": "0:" + "a" * 64,
            "depool_address": "0:" + "b" * 64,
        }
        config = make_node_configuration("validator", wallet=profile)
        keys = self.store.node_directory("validator") / "keys"
        seed_dir = keys / "MSKeys_validator"
        seed_dir.mkdir(parents=True)
        (keys / "validator.addr").write_text("0:" + "c" * 64 + "\n")
        for index in range(1, 4):
            (seed_dir / f"validator_seed_{index}.txt").write_text("one " * 12)
            (seed_dir / f"validator_{index}.keys.json").write_text(
                json.dumps({"public": str(index) * 64, "secret": "private"})
            )
        with patch("evernode.wallets.scripts.execute_validator_script"):
            details = wallets.prepare_wallet(self.store, config, imported=True)
        self.assertEqual(details["address"], profile["wallet_address"])
        self.assertEqual(details["public_keys"], ["1" * 64, "2" * 64, "3" * 64])

    def test_new_wallet_rejects_stale_root_address_or_keys_before_funding(self):
        profile = {
            "mode": "new",
            "custodians": 2,
            "required_signatures": 2,
            "wallet_address": None,
            "depool_address": None,
        }
        config = make_node_configuration("validator", wallet=profile)
        keys = self.store.node_directory("validator") / "keys"
        generated = keys / "MSKeys_validator"
        generated.mkdir(parents=True)
        address = "0:" + "a" * 64
        (generated / "validator.addr").write_text(address + "\n")
        (keys / "validator.addr").write_text(address + "\n")
        for index in (1, 2):
            key = json.dumps({"public": str(index) * 64, "secret": "private"})
            (generated / f"validator_{index}.keys.json").write_text(key)
            (keys / f"validator_{index}.keys.json").write_text(key)
        with patch("evernode.wallets.scripts.execute_validator_script"):
            self.assertEqual(
                wallets.prepare_wallet(self.store, config)["address"], address
            )
            (keys / "validator.addr").write_text("0:" + "b" * 64)
            with self.assertRaisesRegex(process.OperationError, "address differs"):
                wallets.prepare_wallet(self.store, config)
            (keys / "validator.addr").write_text(address)
            (keys / "validator_2.keys.json").write_text(
                json.dumps({"public": "c" * 64, "secret": "private"})
            )
            with self.assertRaisesRegex(process.OperationError, "key 2 differs"):
                wallets.prepare_wallet(self.store, config)

    def test_import_rejects_unrelated_custodian_and_depool(self):
        profile = {
            "mode": "import",
            "custodians": 3,
            "required_signatures": 2,
            "wallet_address": "0:" + "a" * 64,
            "depool_address": "0:" + "b" * 64,
        }
        config = make_node_configuration(
            "validator", wallet=profile, setup_stage="node-started"
        )
        details = {"address": profile["wallet_address"], "public_keys": ["1" * 64] * 3}
        observed = {
            "custodians": 3,
            "required_signatures": 2,
            "public_keys": ["2" * 64, "3" * 64, "4" * 64],
        }
        with (
            patch("evernode.wallets.prepare_wallet", return_value=details),
            patch(
                "evernode.wallets.scripts.verify_imported_safe", return_value=observed
            ),
            self.assertRaisesRegex(
                process.OperationError,
                "The imported Safe does not match: its custodian keys are not this node's keys",
            ),
        ):
            wallets.wallet_recover(self.store, config)
        self.assertEqual(config["setup_stage"], "node-started")

        config["setup_stage"] = "wallet-verified"
        with (
            patch(
                "evernode.wallets.scripts.verify_imported_depool",
                return_value={"validator_wallet": "0:" + "c" * 64},
            ),
            self.assertRaisesRegex(
                process.OperationError, "different validator wallet"
            ),
        ):
            wallets.depool_verify(self.store, config)

    def test_setup_status_reports_one_next_command(self):
        config = make_node_configuration(
            "validator",
            wallet={
                "mode": "import",
                "custodians": 3,
                "required_signatures": 2,
                "wallet_address": "0:" + "a" * 64,
                "depool_address": "0:" + "b" * 64,
            },
            setup_stage="wallet-recovered",
        )
        self.assertEqual(
            wallets.next_setup_command(config), "evernode wallet verify -n validator"
        )


if __name__ == "__main__":
    unittest.main()
