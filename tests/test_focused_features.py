import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from evernode import (
    cli,
    configuration,
    creation,
    docker,
    elections,
    host,
    images,
    process,
    scripts,
    storage,
    setup_actions,
    wallets,
)
from evernode.arguments import build_argument_parser
from support import make_node_configuration


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

    def test_container_pair_skips_docker_and_saved_names(self):
        directory = self.store.node_directory("validator")
        directory.mkdir(parents=True)
        config = make_node_configuration(
            "validator", container_name="ever-node-01", statsd="statsd-01"
        )
        self.store.save_node_config(config)
        with patch("evernode.docker.process.execute_command") as command:
            command.return_value.stdout = "ever-node-02\nstatsd-03\n"
            node, statsd = docker.select_container_names(self.store)
        self.assertEqual((node, statsd), ("ever-node-04", "statsd-04"))

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

    def test_election_schedule_uses_exact_reference_sequence(self):
        directory = self.store.node_directory("validator")
        for part in ("scripts", "logs/validator", "elections"):
            (directory / part).mkdir(parents=True, exist_ok=True)
        config = make_node_configuration("validator")
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
        }
        self.assertTrue(host.host_dependencies_available(report))
        report["commands"]["yq"] = None
        self.assertFalse(host.host_dependencies_available(report))

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
        }
        with (
            patch(
                "evernode.host._os_release",
                return_value={"ID": "debian", "VERSION_CODENAME": "trixie"},
            ),
            patch("evernode.host._find_command", return_value=None),
            patch("evernode.host.process.execute_command", side_effect=execute),
            patch("evernode.host.collect_host_dependency_report", return_value=report),
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
        }
        with (
            patch(
                "evernode.host._os_release",
                return_value={"ID": "ubuntu", "VERSION_CODENAME": "noble"},
            ),
            patch("evernode.host._find_command", return_value="/usr/bin/docker"),
            patch("evernode.host.process.execute_command", side_effect=execute),
            patch("evernode.host.collect_host_dependency_report", return_value=report),
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

    def test_import_does_not_prompt_for_new_depool_terms(self):
        parser = build_argument_parser()
        args = parser.parse_args(
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
            ]
        )
        with (
            patch(
                "evernode.cli.docker.select_container_names",
                return_value=("ever-node-01", "statsd-01"),
            ),
            patch(
                "evernode.cli.docker.select_available_node_port",
                side_effect=(58888, 9102),
            ),
            patch("evernode.cli.sys.stdin.isatty", return_value=True),
            patch(
                "evernode.creation.ui.prompt_for_value",
                side_effect=lambda value, _label, default=None: (
                    value if value is not None else default
                ),
            ),
            patch(
                "evernode.creation.ui.prompt_integer",
                side_effect=lambda value, label, default: (
                    default if value is None else value
                ),
            ) as integer,
        ):
            self.assertEqual(creation.handle_node_creation(self.store, args), 0)
        self.assertEqual(
            [call.args[1] for call in integer.call_args_list],
            ["Wallet workchain", "Election check interval in minutes"],
        )

    def test_import_prompt_labels_depool_address_as_required(self):
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
                "--import-wallet",
                "--wallet-address",
                "0:" + "a" * 64,
                "--custodians",
                "3",
                "--required-signatures",
                "2",
                "--dry-run",
            ]
        )
        with (
            patch(
                "evernode.creation.docker.select_container_names",
                return_value=("ever-node-01", "statsd-01"),
            ),
            patch(
                "evernode.creation.docker.select_available_node_port",
                side_effect=(58888, 9102),
            ),
            patch("evernode.creation.sys.stdin.isatty", return_value=True),
            patch(
                "evernode.creation.ui.prompt_for_value",
                side_effect=lambda value, _label, default=None: (
                    value if value is not None else default
                ),
            ),
            patch(
                "evernode.creation.ui.prompt_integer",
                side_effect=lambda value, _label, default: (
                    default if value is None else value
                ),
            ),
            patch("builtins.input", return_value="") as prompt,
        ):
            with self.assertRaisesRegex(process.OperationError, "requires expected"):
                creation.handle_node_creation(self.store, args)
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
                "--workchain",
                "0",
                "--cron-interval",
                "10",
                "--depool-type",
                "EverX",
                "--validator-assurance",
                "50000",
                "--min-stake",
                "10",
                "--reward-fraction",
                "65",
                "--balance-threshold",
                "20",
                "--custodians",
                "3",
                "--required-signatures",
                "2",
                "--yes",
            ]
        )
        record = {"image": "local/ever-node:abc", "image_id": "sha256:" + "a" * 64}
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
            patch("evernode.creation.process.execute_command"),
            patch(
                "evernode.creation.provisioning.provision_node_workspace",
                side_effect=process.OperationError("preparation failed"),
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
                (root / "vendor/ever-validator/scripts/env.sh").read_bytes()
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
                "evernode.setup_actions._require_synchronized_setup_node",
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
        with patch("evernode.wallets.scripts.execute_validator_script") as execute:
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
            [call.args[2] for call in execute.call_args_list],
            ["Msig_deploy.sh", "DePool_deploy.sh", "stake_to_depool.sh"],
        )
        with self.assertRaisesRegex(process.OperationError, "requires setup stage"):
            wallets.depool_deploy(self.store, deploy_wallet)

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
        with (
            patch(
                "evernode.wallets.scripts.verify_imported_safe",
                return_value={"custodians": 3, "required_signatures": 2},
            ) as safe,
            patch(
                "evernode.wallets.scripts.verify_imported_depool",
                return_value={"checked": True},
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
