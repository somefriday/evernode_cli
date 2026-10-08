"""Validate node settings and render the per-node shell environment."""

import ipaddress
import os
import re
import shlex
from pathlib import Path

from . import process

DEFAULT_ORCHESTRATION_REPOSITORY = "https://github.com/Custler/Ever-Validator.git"
NODE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
CONTAINER_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
DEFAULT_ENVIRONMENT = {
    "NODE_WC": 0,
    "CRONTAB_INTERVAL": 10,
    "DePool_TYPE": "EverX",
    "ValidatorAssuranceT": 50000,
    "MinStakeT": 10,
    "ParticipantRewardFraction": 65,
    "BalanceThresholdT": 20,
}


def validate_node_name(name):
    if not isinstance(name, str) or not NODE_NAME_PATTERN.fullmatch(name):
        raise process.OperationError(
            "Name must start with a lowercase letter and contain only lowercase letters, digits or hyphens (max 40)."
        )
    return name


def node_container_name(config):
    """Docker node container name; old state used the validator name directly."""
    value = config.get("container_name", config["name"])
    if not isinstance(value, str) or not CONTAINER_NAME_PATTERN.fullmatch(value):
        raise process.OperationError(
            "Invalid node container name in node configuration"
        )
    return value


def node_compose_project(config):
    value = config.get("compose_project", "evernode-" + config["name"])
    if not isinstance(value, str) or not CONTAINER_NAME_PATTERN.fullmatch(value):
        raise process.OperationError(
            "Invalid Compose project name in node configuration"
        )
    return value


def validate_saved_node_config(value, name):
    """Validate persisted inputs before they reach Docker, a path or a script."""
    if (
        not isinstance(value, dict)
        or value.get("name") != name
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
    ):
        raise process.OperationError("Invalid or unsupported node configuration")
    validate_node_name(name)
    if value.get("statsd") != "statsd-" + name:
        # Version one used statsd-<validator-name>; new installations use a
        # numeric Docker-global name. Both remain readable for safe migration.
        statsd = value.get("statsd")
        if not isinstance(statsd, str) or not CONTAINER_NAME_PATTERN.fullmatch(statsd):
            raise process.OperationError("Invalid exporter name in node configuration")
    node_container_name(value)
    node_compose_project(value)
    if value.get("network") not in ("main", "devnet"):
        raise process.OperationError("Invalid network in node configuration")
    try:
        ipaddress.IPv4Address(value.get("ip", ""))
    except (ValueError, TypeError):
        raise process.OperationError("Invalid IPv4 in node configuration") from None
    if not isinstance(value.get("memory"), str) or not re.fullmatch(
        r"[1-9][0-9]*[MG]", value["memory"]
    ):
        raise process.OperationError("Invalid memory ceiling in node configuration")
    for field in ("adnl_port", "metrics_port"):
        if type(value.get(field)) is not int or not 1024 <= value[field] <= 65535:
            raise process.OperationError(f"Invalid {field} in node configuration")
    for field in ("image", "image_id"):
        ref = value.get(field)
        if (
            not isinstance(ref, str)
            or not ref
            or ref.startswith("-")
            or any(c.isspace() or ord(c) < 32 for c in ref)
        ):
            raise process.OperationError(f"Invalid {field} in node configuration")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", value["image_id"]):
        raise process.OperationError("Saved image must be a Docker image ID")
    if (
        value.get("phase") not in ("preparing", "prepared", "console-ready", "removed")
        or type(value.get("initialized")) is not bool
    ):
        raise process.OperationError("Invalid setup state in node configuration")
    settings = value.get("settings")
    if settings is not None:
        if not isinstance(settings, dict) or set(settings) != set(DEFAULT_ENVIRONMENT):
            raise process.OperationError("Invalid saved environment settings")
        if (
            settings.get("NODE_WC") not in (-1, 0)
            or not isinstance(settings.get("CRONTAB_INTERVAL"), int)
            or not 1 <= settings["CRONTAB_INTERVAL"] <= 59
        ):
            raise process.OperationError("Invalid saved workchain or cron interval")
        if (
            settings.get("DePool_TYPE") not in ("EverX", "StEver")
            or any(
                type(settings.get(key)) is not int or settings[key] < 0
                for key in ("ValidatorAssuranceT", "MinStakeT", "BalanceThresholdT")
            )
            or type(settings.get("ParticipantRewardFraction")) is not int
            or not 0 <= settings["ParticipantRewardFraction"] <= 100
        ):
            raise process.OperationError("Invalid saved DePool settings")
    source = value.get("source_spec")
    if source is not None:
        if not isinstance(source, dict) or set(source) != {"local", "repo", "ref"}:
            raise process.OperationError("Invalid saved source specification")
        for field, ref in source.items():
            if ref is not None and (
                not isinstance(ref, str)
                or not ref
                or ref.startswith("-")
                or any(ord(c) < 32 for c in ref)
            ):
                raise process.OperationError("Invalid saved source reference")
        if source["local"] is not None and not Path(source["local"]).is_absolute():
            raise process.OperationError("Saved local source path must be absolute")
        if not source["local"] and not source["repo"]:
            raise process.OperationError("Missing saved source repository")
    return value


def write_node_environment(path, values):
    text = path.read_text()
    for key, value in values.items():
        pattern = re.compile(r"^(?:export )?" + re.escape(key) + r"=.*$", re.M)
        assignment = f"export {key}={shlex.quote(str(value))}"
        if not pattern.search(text):
            # These are CLI-only derived names. Current and older upstream
            # env.sh versions need not know them, but they are useful to every
            # rendered node context and do not alter upstream defaults.
            if key not in {
                "COMPOSE_PROJECT_NAME",
                "CRON_MARKER",
                "DOCKER_STATSD_CONTAINER_NAME",
                "METRICS_HOST_PORT",
            }:
                raise process.OperationError(f"Unsupported project: env.sh lacks {key}")
            text += "\n# Added by evernode-manager\n" + assignment + "\n"
        else:
            text = pattern.sub(lambda _: assignment, text)
    path.write_text(text)
    os.chmod(path, 0o600)


def build_node_environment(config):
    container = node_container_name(config)
    settings = dict(DEFAULT_ENVIRONMENT, **config.get("settings", {}))
    return {
        "NETWORK_TYPE": config["network"],
        "RUN_MODE": "docker",
        "NODE_ROLE": "validator",
        "NODE_WC": settings["NODE_WC"],
        "STAKE_MODE": "depool",
        "FORCE_USE_DAPP": "false",
        "DOCKER_USE_CUSTOM_IMAGE": "true",
        "DOCKER_IMAGE_REPO": config["image"].rsplit(":", 1)[0],
        "RUST_VERSION": "1.90.0",
        "CLI_GIT_COMMIT": "0.44.0",
        "VALIDATOR_NAME": config["name"],
        "COMPOSE_PROJECT_NAME": node_compose_project(config),
        "CRON_MARKER": "evernode-" + config["name"],
        "DOCKER_NODE_CONTAINER_NAME": container,
        "DOCKER_STATSD_CONTAINER_NAME": config["statsd"],
        "NODE_IP_ADDR": config["ip"],
        "ADNL_PORT": config["adnl_port"],
        "RCONSOLE_PORT": 5888,
        "STATSD_DOMAIN": config["statsd"],
        "STATSD_UDP_PORT": 9125,
        "METRICS_HOST_PORT": config["metrics_port"],
        "CRONTAB_INTERVAL": settings["CRONTAB_INTERVAL"],
        "Enable_Node_Autoupdate": "false",
        "Enable_Scripts_Autoupdate": "false",
        "DePool_TYPE": settings["DePool_TYPE"],
        "ValidatorAssuranceT": settings["ValidatorAssuranceT"],
        "MinStakeT": settings["MinStakeT"],
        "ParticipantRewardFraction": settings["ParticipantRewardFraction"],
        "BalanceThresholdT": settings["BalanceThresholdT"],
        "IS_ENVIRONMENT_CONFIGURED": "true",
    }
