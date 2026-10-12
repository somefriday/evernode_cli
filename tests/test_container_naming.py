"""New naming is collision-safe and does not migrate existing identities."""

import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from evernode import arguments, configuration, creation, docker, process, provisioning
from support import make_node_configuration


class ContainerNamingTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.store.list_node_names.return_value = []
        self.store.acquire_operation_lock.side_effect = (
            lambda *args: contextlib.nullcontext()
        )

    def test_new_names_derive_from_validator_and_accept_maximum_length(self):
        with patch(
            "evernode.docker.process.execute_command",
            return_value=SimpleNamespace(stdout=""),
        ):
            for name in ("validator03", "a" * 40):
                with self.subTest(name=name):
                    node, statsd = docker.select_container_names(self.store, name)
                    self.assertEqual(
                        (node, statsd), (f"evernode-{name}", f"statsd-{name}")
                    )
                    config = make_node_configuration(
                        name, container_name=node, statsd=statsd
                    )
                    configuration.validate_saved_node_config(config, name)

    def test_each_container_name_is_checked_against_all_docker_containers(self):
        for conflict in ("evernode-validator03", "statsd-validator03"):
            with self.subTest(conflict=conflict), patch(
                "evernode.docker.process.execute_command",
                return_value=SimpleNamespace(stdout=conflict + "\n"),
            ) as execute:
                with self.assertRaisesRegex(process.OperationError, conflict):
                    docker.select_container_names(self.store, "validator03")
                execute.assert_called_once_with(
                    ["docker", "ps", "-a", "--format", "{{.Names}}"]
                )

    def test_saved_names_remain_reserved_even_without_containers(self):
        self.store.list_node_names.return_value = ["old-node"]
        for field, conflict in (
            ("container_name", "evernode-validator03"),
            ("statsd", "statsd-validator03"),
        ):
            self.store.load_node_config.return_value = make_node_configuration(
                "old-node", **{field: conflict}
            )
            with self.subTest(field=field), patch(
                "evernode.docker.process.execute_command",
                return_value=SimpleNamespace(stdout=""),
            ):
                with self.assertRaisesRegex(process.OperationError, conflict):
                    docker.select_container_names(self.store, "validator03")

    def test_existing_names_and_legacy_fallback_survive_environment_and_compose(self):
        for explicit in (None, "ever-node-07", "custom-existing-name"):
            config = make_node_configuration("validator03", statsd="statsd-07")
            if explicit is not None:
                config["container_name"] = explicit
            original = config.copy()
            with self.subTest(explicit=explicit):
                configuration.validate_saved_node_config(config, config["name"])
                env = configuration.build_node_environment(config)
                compose = docker.build_node_compose_config(
                    config, Path("/state/validator03")
                )
                self.assertEqual(
                    env["DOCKER_NODE_CONTAINER_NAME"], explicit or "validator03"
                )
                self.assertEqual(env["STATSD_DOMAIN"], "statsd-07")
                self.assertEqual(
                    compose["services"]["node"]["container_name"],
                    explicit or "validator03",
                )
                self.assertEqual(
                    compose["services"]["statsd"]["container_name"], "statsd-07"
                )
                self.assertEqual(config, original)

    def test_resume_uses_saved_configuration_without_allocating_names(self):
        config = make_node_configuration(
            "validator03", container_name="ever-node-07", statsd="statsd-07"
        )
        self.store.load_node_config.return_value = config
        args = arguments.build_argument_parser().parse_args(
            ["node", "create", "--resume", "-n", "validator03"]
        )
        with patch("evernode.creation.docker.select_container_names") as select, patch(
            "evernode.creation.provisioning.prepare_node_workspace"
        ) as prepare, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(creation.handle_node_creation(self.store, args), 0)
        select.assert_not_called()
        prepare.assert_called_once_with(self.store, config)

    def test_provisioning_rechecks_collision_before_saving_configuration(self):
        directory = Mock()
        directory.exists.return_value = False
        directory.is_symlink.return_value = False
        self.store.node_directory.return_value = directory
        config = make_node_configuration(
            "validator03",
            container_name="evernode-validator03",
            statsd="statsd-validator03",
        )
        with patch(
            "evernode.docker.process.execute_command",
            return_value=SimpleNamespace(stdout="statsd-validator03\n"),
        ):
            with self.assertRaisesRegex(process.OperationError, "statsd-validator03"):
                provisioning.provision_node_workspace(self.store, config)
        self.store.acquire_operation_lock.assert_called_once_with()
        self.store.save_node_config.assert_not_called()

    def test_invalid_name_never_reaches_docker(self):
        with patch("evernode.docker.process.execute_command") as execute:
            with self.assertRaises(process.OperationError):
                docker.select_container_names(self.store, "../invalid")
        execute.assert_not_called()
