"""Start, stop, remove, inspect and locally seed provisioned node runtimes."""

import contextlib
import hashlib
import json
import os
import shutil
import stat
import sys
import time
import uuid
from pathlib import Path

from . import (
    configuration,
    docker,
    elections,
    process,
    provisioning,
    scripts,
    storage,
    wallets,
)


def _run_console(config, command, timeout=15):
    container = configuration.node_container_name(config)
    obj = docker.inspect_container(container)
    if not obj or obj["State"]["Status"] != "running":
        raise process.OperationError(
            "Node container is not running; inspect startup/stderr logs"
        )
    result = process.execute_command(
        [
            "docker",
            "exec",
            container,
            "console",
            "-C",
            "/ever-node/node_cfg/console.json",
            "-j",
            "-c",
            command,
        ],
        timeout=timeout,
    )
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise process.OperationError(
            "Console did not return JSON; inspect node logs"
        ) from exc
    if not isinstance(value, dict):
        raise process.OperationError("Console JSON must be an object")
    return value


# The console's lags are ever-node's u32 `now - gen_utime`: a block stamped ahead
# of this server's clock reads just under 2**32, so -11 s prints as 4294967285.
LAGS = ("timediff", "shards_timediff")
# Within this many seconds either way, the node is at the head of the chain.
SYNC_TOLERANCE = 10


def signed_lag(value):
    """A console lag as the signed number it stands for."""
    if type(value) is int and 1 << 31 <= value < 1 << 32:
        return value - (1 << 32)
    return value


def fetch_node_statistics(config):
    value = _run_console(config, "getstatsnew")
    for key in LAGS:
        if key in value:
            value[key] = signed_lag(value[key])
    return value


def fetch_chain_parameter(config, number):
    """Read one network configuration parameter through this node's console.

    Limits and timings that decide whether a stake can work at all are network
    state, not settings of this installation, so they are read from the chain
    rather than assumed. The console wraps each one in a 'pNN' key.
    """
    if not isinstance(number, int) or not 0 <= number <= 100:
        raise process.OperationError("Unsupported configuration parameter")
    value = _run_console(config, f"getconfig {number}")
    parameter = value.get(f"p{number}")
    if not isinstance(parameter, dict):
        raise process.OperationError(
            f"The chain did not report configuration parameter {number}"
        )
    return parameter


def is_node_synchronized(value):
    """At the head of the chain: both lags within the tolerance, either way.

    A negative lag is not a node behind the chain but a block stamped in this
    server's future: a few seconds of it are a late clock (clock_behind), and
    beyond the tolerance the clock is too far off to time anything by.
    """
    lags = [signed_lag(value.get(k)) for k in LAGS]
    return value.get("node_status", value.get("sync_status")) in (
        "synchronization_by_blocks",
        "synchronization_finished",
    ) and all(
        type(lag) in (int, float) and -SYNC_TOLERANCE <= lag <= SYNC_TOLERANCE
        for lag in lags
    )


def clock_behind(value):
    """Seconds this server's clock is behind the newest block the node holds; 0 when it is not."""
    lags = [signed_lag(value.get(k)) for k in LAGS]
    return max([0] + [-lag for lag in lags if type(lag) in (int, float) and lag < 0])


def late_clock(behind):
    return (
        f"this server's clock is {behind}s behind the network's newest block: "
        "check its time sync (NTP)"
    )


VALIDATOR_SET_FIELDS = (
    ("in_current_vset_p34", "current"),
    ("in_next_vset_p36", "next"),
)
# Only these answers prove the node owes nothing. Anything else - including a
# field this node version does not report - leaves its duties unproven.
OUTSIDE_VALIDATOR_SET = (False, 0, "0", "false", "False", "no", "No")


def _outside_validator_set(value):
    return value in OUTSIDE_VALIDATOR_SET


def validator_duty(statistics):
    """Describe this node's validator duties, or why they are unproven.

    Returns None when the node has shown it owes nothing, otherwise a phrase
    naming either the sets it belongs to or the reason membership could not be
    established. One reading for every guard, so they cannot disagree.
    """
    if _explicitly_outside_validator_set(statistics):
        return None
    member = [
        label
        for field, label in VALIDATOR_SET_FIELDS
        if statistics.get(field) is not None
        and not _outside_validator_set(statistics.get(field))
    ]
    if member:
        return "in the " + " and ".join(member) + " validator set"
    # A node still catching up is not meeting its duties either way, and
    # refusing every such operation would only teach operators to override by
    # habit. A synchronized node that does not report these fields is a version
    # this code cannot read, and treating that silence as "not a member" is how
    # a next-set member gets taken out from under its consensus session.
    if not is_node_synchronized(statistics):
        return None
    unreported = ", ".join(
        field for field, _ in VALIDATOR_SET_FIELDS if statistics.get(field) is None
    )
    return (
        "synchronized but silent about its validator-set membership ("
        + unreported
        + " missing from its console statistics), so it cannot be shown to owe "
        "no blocks"
    )


def _explicitly_outside_validator_set(value):
    """Fail closed: a stopped source must prove it has no validator duties."""
    return all(
        _outside_validator_set(value.get(field)) for field, _ in VALIDATOR_SET_FIELDS
    )


def _private_database(directory):
    database = directory / "node_db"
    storage.validate_private_path(directory)
    storage.validate_private_path(database)
    if not database.is_dir():
        raise process.OperationError(
            f"Expected a private node database directory: {database}"
        )
    return database


def _database_size(directory, *, exclude_catchains=False):
    """Validate an rsync-safe database tree and return a conservative byte count."""
    total = files = 0
    root = str(directory)
    pending = [root]
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                if exclude_catchains and current == root and entry.name == "catchains":
                    continue
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or not (
                    stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
                ):
                    raise process.OperationError(
                        f"Database contains an unsupported linked or special file: {entry.path}"
                    )
                if stat.S_ISDIR(info.st_mode):
                    pending.append(entry.path)
                else:
                    files += 1
                    total += info.st_size
    if not files:
        raise process.OperationError(f"Database is empty: {directory}")
    return total


def _active_global_config_digest(directory):
    cfg_dir = directory / "node_cfg"
    config_path = cfg_dir / "config.json"
    storage.validate_private_path(cfg_dir)
    storage.validate_private_path(config_path)
    try:
        node_config = json.loads(config_path.read_text())
        configured = node_config["ton_global_config_name"]
    except (KeyError, TypeError, ValueError) as exc:
        raise process.OperationError(
            f"Cannot determine active global configuration for {directory.name}"
        ) from exc
    configured_path = Path(configured) if isinstance(configured, str) else Path()
    if (
        configured_path.parent != Path("/ever-node/node_cfg")
        or not configured_path.name
    ):
        raise process.OperationError(
            f"Unexpected global configuration path for {directory.name}"
        )
    global_config = cfg_dir / configured_path.name
    storage.validate_private_path(global_config)
    return hashlib.sha256(global_config.read_bytes()).hexdigest()


def _has_election_schedule(name):
    path = elections.node_election_cron_path(name)
    return path.exists() or path.is_symlink()


@contextlib.contextmanager
def _local_sync_locks(store, source_name, target_name):
    """Hold both nodes' operation and election locks in one deterministic order.

    The election lock refuses while a run that began before election stop is
    still using a node, and keeps a new one from starting during the copy.
    """
    with contextlib.ExitStack() as stack:
        for name in sorted((source_name, target_name)):
            stack.enter_context(store.acquire_operation_lock(name))
            stack.enter_context(elections.hold_election_lock(store, name))
        yield


def _local_sync_preflight(store, source_name, target_name):
    source_name = configuration.validate_node_name(source_name)
    target_name = configuration.validate_node_name(target_name)
    if source_name == target_name:
        raise process.OperationError(
            "Local sync source and target must be different nodes"
        )
    source = store.load_node_config(source_name)
    target = store.load_node_config(target_name)
    if (
        not source["initialized"]
        or not target["initialized"]
        or source["phase"] == "removed"
        or target["phase"] == "removed"
    ):
        raise process.OperationError(
            "Local sync requires two initialized, non-removed nodes"
        )
    if not shutil.which("rsync"):
        # The copy itself is an rsync, so a missing one would otherwise fail
        # mid-operation with an errno and no indication of the remedy.
        raise process.OperationError(
            "Local sync copies the database with rsync, which is not installed; "
            "run: evernode host setup"
        )
    if _has_election_schedule(source_name) or _has_election_schedule(target_name):
        raise process.OperationError(
            "Disable managed election schedules before local sync; the command will not disable them"
        )
    source_stats = fetch_node_statistics(source)
    target_stats = fetch_node_statistics(target)
    if not is_node_synchronized(source_stats):
        raise process.OperationError("Source node is not synchronized")
    if not _explicitly_outside_validator_set(source_stats):
        raise process.OperationError(
            "Source must explicitly report no current or next validator-set membership"
        )
    if is_node_synchronized(target_stats):
        raise process.OperationError(
            "Target is already synchronized; refusing to replace a healthy database"
        )
    if (
        source["image_id"] != target["image_id"]
        or source["network"] != target["network"]
    ):
        raise process.OperationError(
            "Source and target must use the same managed image and network"
        )
    source_dir = store.node_directory(source_name)
    target_dir = store.node_directory(target_name)
    source_db = _private_database(source_dir)
    target_db = _private_database(target_dir)
    source_global = _active_global_config_digest(source_dir)
    target_global = _active_global_config_digest(target_dir)
    if source_global != target_global:
        raise process.OperationError(
            "Source and target active global-network configurations differ"
        )
    source_bytes = _database_size(source_db, exclude_catchains=True)
    required_bytes = source_bytes + max(source_bytes // 10, 64 * 1024 * 1024)
    free_bytes = shutil.disk_usage(target_dir).free
    if free_bytes < required_bytes:
        raise process.OperationError(
            f"Insufficient free space for staged database copy: need {required_bytes} bytes, have {free_bytes}"
        )
    return {
        "source": source,
        "target": target,
        "source_stats": source_stats,
        "source_database": source_db,
        "target_database": target_db,
        "source_bytes": source_bytes,
        "free_bytes": free_bytes,
        "global_config_sha256": source_global,
    }


def inspect_local_sync(store, source_name, target_name):
    """Return the checked, non-destructive plan shown before operator approval."""
    source_name = configuration.validate_node_name(source_name)
    target_name = configuration.validate_node_name(target_name)
    if source_name == target_name:
        raise process.OperationError(
            "Local sync source and target must be different nodes"
        )
    with _local_sync_locks(store, source_name, target_name):
        plan = _local_sync_preflight(store, source_name, target_name)
    return {
        "plan": "seed unsynchronized local node database",
        "source": source_name,
        "target": target_name,
        "source_masterchain_block": plan["source_stats"].get("masterchainblocknumber"),
        "image_id": plan["source"]["image_id"],
        "global_config_sha256": plan["global_config_sha256"],
        "copy_bytes": plan["source_bytes"],
        "target_free_bytes": plan["free_bytes"],
        "excluded": ["catchains/", "keys/", "node_cfg/", "elections/", "logs/"],
    }


def _remove_directory(path):
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_dir():
            raise process.OperationError(
                f"Refusing to remove unexpected local-sync path: {path}"
            )
        shutil.rmtree(path)


def _remove_leftover(path):
    """Cleanup never changes the result: a directory that stays is named."""
    try:
        _remove_directory(path)
    except (OSError, process.OperationError) as exc:
        print(f"Warning: {path} was not removed ({exc}); delete it by hand", file=sys.stderr)


def _restart_after_failed_local_sync(store, config, was_stopped, errors):
    if not was_stopped:
        return
    try:
        start_node(store, config)
    except process.OperationError as exc:
        errors.append(f"could not restart {config['name']}: {exc}")


def local_sync(store, source_name, target_name, *, timeout=30):
    """Copy a stopped local source DB through staging, leaving identities untouched."""
    source_name = configuration.validate_node_name(source_name)
    target_name = configuration.validate_node_name(target_name)
    if source_name == target_name:
        raise process.OperationError(
            "Local sync source and target must be different nodes"
        )
    with _local_sync_locks(store, source_name, target_name):
        plan = _local_sync_preflight(store, source_name, target_name)
        source, target = plan["source"], plan["target"]
        source_db, target_db = plan["source_database"], plan["target_database"]
        workspace = target_db.parent
        token = uuid.uuid4().hex
        staging = workspace / f".node_db-lsync-{token}.staging"
        backup = workspace / f".node_db-lsync-{token}.backup"
        failed = workspace / f".node_db-lsync-{token}.failed"
        source_stopped = target_stopped = backup_created = False
        recovery_errors = []
        try:
            # Marked before each stop: a stop cut short leaves the node in
            # either state, and starting a running node changes nothing.
            target_stopped = True
            stop_node(target, timeout, disable_elections=False)
            source_stopped = True
            stop_node(source, timeout, disable_elections=False)
            process.execute_command(
                [
                    "rsync",
                    "-aH",
                    "--numeric-ids",
                    "--sparse",
                    "--delete",
                    "--info=progress2",
                    "--exclude",
                    "/catchains/",
                    str(source_db) + "/",
                    str(staging) + "/",
                ],
                timeout=24 * 60 * 60,
                stream=True,
            )
            _database_size(staging)
            start_node(store, source)
            source_stopped = False
            os.replace(target_db, backup)
            backup_created = True
            os.replace(staging, target_db)
            start_node(store, target)
        except (OSError, process.OperationError, KeyboardInterrupt) as exc:
            # An interrupt takes the same way back as a failure, so the target
            # keeps its previous database and both nodes run again.
            if backup_created:
                try:
                    stop_node(target, timeout, disable_elections=False)
                    # Set aside, not deleted: the old database is back first.
                    if target_db.exists() or target_db.is_symlink():
                        os.replace(target_db, failed)
                    os.replace(backup, target_db)
                    backup_created = False
                except (OSError, process.OperationError) as restore_error:
                    recovery_errors.append(
                        f"could not restore {target_name} database: {restore_error}"
                    )
            _restart_after_failed_local_sync(
                store, target, target_stopped, recovery_errors
            )
            _restart_after_failed_local_sync(
                store, source, source_stopped, recovery_errors
            )
            message = f"Local sync failed: {str(exc) or 'interrupted'}"
            if recovery_errors:
                message += "; " + "; ".join(recovery_errors)
            raise process.OperationError(message) from None
        finally:
            # Last, once both nodes run again: each is a whole database, and
            # deleting one takes minutes.
            _remove_leftover(staging)
            _remove_leftover(failed)
        # The target runs on the copy, so the sync is done and nothing below may
        # undo it: removing the old database is cleanup, and a failure or an
        # interrupt there leaves only that directory behind.
        storage.write_json_atomically(
            workspace / "lsync.json",
            {
                "schema_version": 1,
                "source": source_name,
                "target": target_name,
                "source_masterchain_block": plan["source_stats"].get(
                    "masterchainblocknumber"
                ),
                "image_id": source["image_id"],
                "global_config_sha256": plan["global_config_sha256"],
                "copied_bytes": plan["source_bytes"],
                "completed_at": int(time.time()),
            },
        )
        _remove_leftover(backup)
        return {
            "source": source_name,
            "target": target_name,
            "copied_bytes": plan["source_bytes"],
            "target_console_ready": True,
        }


def get_node_status(config):
    container = configuration.node_container_name(config)
    obj = docker.inspect_managed_container(container, config["name"])
    result = {
        "name": config["name"],
        "phase": config["phase"],
        "setup_stage": config.get("setup_stage"),
        # Setup is complete once its schedule is installed; a node that is
        # already operating must not keep advertising the last setup step.
        "next_setup_command": (
            None
            if elections.node_election_cron_path(config["name"]).exists()
            else wallets.next_setup_command(config)
        ),
        "container": obj["State"]["Status"] if obj else "absent",
        "image": config["image"],
        "metrics": f"http://127.0.0.1:{config['metrics_port']}/metrics",
    }
    exporter = docker.inspect_managed_container(config["statsd"], config["name"])
    result["statsd"] = exporter["State"]["Status"] if exporter else "absent"
    if obj:
        result["restarts"] = obj["RestartCount"]
    if obj and result["container"] == "running":
        try:
            result["stats"] = fetch_node_statistics(config)
            result["synchronized"] = is_node_synchronized(result["stats"])
            behind = clock_behind(result["stats"])
            if behind:
                result["clock"] = late_clock(behind)
        except process.OperationError as exc:
            result["console_error"] = str(exc)
    return result


def start_node(store, config):
    if config["phase"] == "preparing":
        raise process.OperationError(
            f"Preparation did not finish; use node create --resume -n {config['name']}"
        )
    node, _statsd = [
        docker.inspect_managed_container(name, config["name"])
        for name in (configuration.node_container_name(config), config["statsd"])
    ]
    directory = store.node_directory(config["name"])
    if not node or node["State"]["Status"] != "running":
        # Starting applies this version's container definition, such as the
        # open-file limit, to a node created by an older evernode. A running
        # node keeps the one it has until it is stopped.
        storage.write_json_atomically(
            directory / "compose.json", docker.build_node_compose_config(config, directory)
        )
    docker.execute_node_compose(store, config, "up", "-d", "statsd", timeout=300)
    try:
        docker.execute_node_compose(
            store,
            config,
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "/bin/bash",
            "node",
            "-ec",
            'source /ever-node/scripts/env.sh; getent hosts "$STATSD_DOMAIN"; curl --noproxy "*" -fsS --retry 5 --retry-connrefused --retry-delay 1 --max-time 5 "http://${STATSD_DOMAIN}:9102/metrics" >/dev/null',
            timeout=90,
        )
    except process.OperationError as exc:
        logs = process.execute_command(
            ["docker", "logs", "--tail", "30", config["statsd"]], check=False
        )
        raise process.OperationError(
            f"StatsD preflight failed: {exc}\n{logs.stdout}{logs.stderr}"
        ) from exc
    if not config["initialized"]:
        if any((directory / "node_cfg").iterdir()):
            raise process.OperationError(
                "Partial node configuration exists. Refusing to regenerate keys; inspect node_cfg manually"
            )
        try:
            scripts.execute_supported_node_script(
                store, config, "init_scripts/R_gen_init_configs.sh", timeout=180
            )
        finally:
            for folder in (directory / "node_cfg", directory / "keys"):
                storage.restrict_workspace_permissions(folder)
        try:
            provisioning.validate_generated_node_configuration(directory, config)
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            process.OperationError,
        ) as exc:
            raise process.OperationError(
                "Initialization failed validation; keys are preserved. " + str(exc)
            ) from None
        config["initialized"] = True
        store.save_node_config(config)
    docker.execute_node_compose(store, config, "up", "-d", "node")
    for _ in range(12):
        try:
            fetch_node_statistics(config)
            config["phase"] = "console-ready"
            # Wallet setup begins once the node has started, whether the create
            # ran through or was resumed and started by hand.
            if config.get("setup_stage") == "preparing":
                config["setup_stage"] = "node-started"
            store.save_node_config(config)
            return
        except process.OperationError:
            obj = docker.inspect_container(configuration.node_container_name(config))
            if obj and obj["State"]["Status"] != "running":
                break
            time.sleep(5)
    raise process.OperationError(
        "Node console unavailable. Use node logs --component stderr and doctor; do not reinitialize"
    )


def stop_node(config, timeout=30, disable_elections=True):
    containers = [
        (name, docker.inspect_managed_container(name, config["name"]))
        for name in (configuration.node_container_name(config), config["statsd"])
    ]
    if disable_elections:
        elections.disable_node_election_schedule(config["name"])
    for name, obj in containers:
        if obj and obj["State"]["Status"] in ("running", "restarting", "paused"):
            process.execute_command(
                ["docker", "stop", "--time", str(timeout), name], timeout=timeout + 30
            )


def remove_node(store, config, purge=False, timeout=30):
    stop_node(config, timeout)
    for name in (configuration.node_container_name(config), config["statsd"]):
        if docker.inspect_managed_container(name, config["name"]):
            process.execute_command(["docker", "rm", name])
    docker.remove_node_network(config)
    if purge:
        for folder in ("node_db", "logs"):
            path = store.node_directory(config["name"]) / folder
            if path.is_symlink():
                raise process.OperationError("Refusing symlinked data directory")
            shutil.rmtree(path)
            path.mkdir(mode=0o700)
        for folder in ("logs/node", "logs/validator", "logs/archives"):
            (store.node_directory(config["name"]) / folder).mkdir(
                parents=True, exist_ok=True, mode=0o700
            )
    config["phase"] = "removed"
    store.save_node_config(config)


def discard_failed_creation(store, config):
    """Remove every artifact created by one failed ``node create`` call.

    The caller has already established that the name was unused before the
    create operation began. Container ownership labels are still verified
    before anything is stopped or removed.
    """
    name = configuration.validate_node_name(config["name"])
    directory = store.node_directory(name)
    if not directory.exists():
        return
    with store.acquire_operation_lock(name):
        storage.validate_private_path(directory)
        errors = []
        try:
            elections.disable_node_election_schedule(name)
        except process.OperationError as exc:
            errors.append(str(exc))
        for container in (configuration.node_container_name(config), config["statsd"]):
            try:
                item = docker.inspect_managed_container(container, name)
                if item and item["State"]["Status"] in (
                    "running",
                    "restarting",
                    "paused",
                ):
                    process.execute_command(
                        ["docker", "stop", "--time", "30", container], timeout=60
                    )
                if item:
                    process.execute_command(["docker", "rm", container])
            except process.OperationError as exc:
                errors.append(str(exc))
        docker.remove_node_network(config)
        try:
            shutil.rmtree(directory)
        except OSError as exc:
            errors.append(f"cannot remove {directory}: {exc}")
        if errors:
            raise process.OperationError("; ".join(errors))


def _refuse_while_validating(config, action):
    """Stop an operator taking a node out of a set it is a member of.

    A node in the current set misses the blocks it owes. A node in the next set
    is worse: its successor session cannot reach consensus without it, which on
    a small network halts the chain outright. Fencing a replaced validator is a
    legitimate reason to do it anyway, so this is overridable, not forbidden.
    """
    try:
        statistics = fetch_node_statistics(config)
    except process.OperationError:
        return  # An unreachable console cannot prove membership either way.
    duty = validator_duty(statistics)
    if duty is None:
        return
    raise process.OperationError(
        f"This node is {duty}; {action} would make it miss the blocks it owes, "
        "and a next-set member is required for its successor session to reach "
        f"consensus. Wait for the set to expire, or pass --force to {action} it "
        "anyway (for example to fence a replaced node)."
    )


def pending_election(store, config, now=None):
    """When the round this node bid for starts, while its election is undecided.

    The kit gives the node a validator key for an election before it bids, and
    ever-node lists that key in node_cfg/config.json under the election id,
    which is the time the elected set starts. Until the elector publishes that
    set (config param 36) the node is in neither set, yet it can be elected
    into the next one; once p36 is out, set membership answers instead. On
    mainnet an election stays open for hours.
    """
    path = store.node_directory(config["name"]) / "node_cfg" / "config.json"
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None  # No key file yet: the node has never bid.
    keys = value.get("validator_keys") if isinstance(value, dict) else None
    now = time.time() if now is None else now
    upcoming = [
        key["election_id"]
        for key in (keys if isinstance(keys, list) else [])
        if isinstance(key, dict)
        and type(key.get("election_id")) is int
        and key["election_id"] > now
    ]
    if not upcoming or _run_console(config, "getconfig 36").get("p36"):
        return None
    return max(upcoming)


def _refuse_with_pending_bid(store, config, action):
    """Stop an operator taking a node out while the election it bid in is open."""
    try:
        election = pending_election(store, config)
    except process.OperationError:
        return  # An unreachable console cannot tell whether the election is decided.
    if election is None:
        return
    starts = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(election))
    raise process.OperationError(
        f"This node bid for the round that starts {starts}, and the election "
        f"result is not out yet: elected while stopped, it misses that round. "
        "Wait until node status shows it in the next set or the election closes "
        f"without it, or pass --force to {action} it anyway."
    )


def apply_node_lifecycle_operation(
    store, name, action, *, timeout=30, purge_data=False, force=False
):
    """Coordinate one lifecycle operation under its node and reservation locks."""
    if action not in ("start", "stop", "restart", "remove"):
        raise process.OperationError("Unsupported node lifecycle operation")
    if timeout < 1:
        raise process.OperationError("Timeout must be positive")
    with store.acquire_operation_lock(name):
        config = store.load_node_config(name)
        if action in ("stop", "restart", "remove") and not force:
            _refuse_while_validating(config, action)
            # A restart is back within a minute, long before that round starts.
            if action != "restart":
                _refuse_with_pending_bid(store, config, action)
        if action == "start":
            # A removed node can reserve its image again before startup.
            with store.acquire_operation_lock():
                process.execute_command(
                    ["docker", "image", "inspect", config["image_id"]]
                )
                if config["phase"] == "removed":
                    config["phase"] = (
                        "prepared"
                        if config.get("prepare_stage") == "complete"
                        or config["initialized"]
                        else "preparing"
                    )
                    store.save_node_config(config)
            start_node(store, config)
        elif action == "stop":
            stop_node(config, timeout)
        elif action == "restart":
            if elections.node_election_cron_path(name).exists():
                raise process.OperationError(
                    "Disable the election schedule explicitly before restart"
                )
            if config["phase"] in ("preparing", "removed"):
                raise process.OperationError(
                    "Use node create --resume and node start before restarting this node"
                )
            with elections.hold_election_lock(store, name):
                stop_node(config, timeout)
                start_node(store, config)
        elif action == "remove":
            remove_node(store, config, purge_data, timeout)




def image_maintenance_blockers(store, name):
    """Report what would stop image maintenance, without changing anything.

    A dry run that lists a plan the real run then refuses tells the operator
    nothing, so both read the same conditions.
    """
    reasons = []
    if elections.node_election_cron_path(name).exists():
        reasons.append(
            "This node's election schedule is installed; disable it with "
            "evernode election stop before image maintenance"
        )
    try:
        status = get_node_status(store.load_node_config(name))
    except process.OperationError as exc:
        return reasons + [str(exc)]
    duty = (
        validator_duty(status.get("stats", {}))
        if status.get("container") == "running"
        else None
    )
    if duty:
        reasons.append(
            "This node is " + duty + "; do not replace its image during validation"
        )
    return reasons


def update_node_image(store, name, record=None, *, rollback=False, timeout=30):
    """Switch exactly one node to a verified image while retaining its identity."""
    with store.acquire_operation_lock(name), elections.hold_election_lock(store, name):
        config = store.load_node_config(name)
        blockers = image_maintenance_blockers(store, name)
        if blockers:
            raise process.OperationError("; ".join(blockers))
        if rollback:
            previous = config.get("image_rollback")
            if not isinstance(previous, dict) or set(previous) != {"image", "image_id"}:
                raise process.OperationError("No saved image rollback is available")
            target = previous
        else:
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("image"), str)
                or not isinstance(record.get("image_id"), str)
            ):
                raise process.OperationError("Invalid managed replacement image")
            target = {"image": record["image"], "image_id": record["image_id"]}
            if target["image_id"] == config["image_id"]:
                raise process.OperationError("Node already uses this image")
            config["image_rollback"] = {
                "image": config["image"],
                "image_id": config["image_id"],
            }
        # Recorded before the node stops, under the lock image remove decides
        # with: the replacement is either gone already, and this stops here, or
        # the node record names it and image remove refuses.
        with store.acquire_operation_lock():
            process.execute_command(["docker", "image", "inspect", target["image_id"]])
            config.update(target)
            store.save_node_config(config)
        stop_node(config, timeout)
        storage.write_json_atomically(
            store.node_directory(name) / "compose.json",
            docker.build_node_compose_config(config, store.node_directory(name)),
        )
        try:
            start_node(store, config)
            synchronized_once = False
            for _ in range(12):
                stats = fetch_node_statistics(config)
                if is_node_synchronized(stats):
                    if synchronized_once:
                        break
                    synchronized_once = True
                else:
                    synchronized_once = False
                time.sleep(5)
            else:
                behind = clock_behind(stats)
                raise process.OperationError(
                    "Replacement image is console-ready but "
                    + (late_clock(behind) if behind > SYNC_TOLERANCE else "not synchronized")
                    + "; use rollback after inspection"
                )
        except BaseException:
            # Preserve the replacement and rollback records for explicit repair.
            raise
        if rollback:
            config.pop("image_rollback", None)
        store.save_node_config(config)


def build_node_log_command(store, config, component="node", *, tail=100, follow=False):
    if component in ("startup", "statsd"):
        container = (
            configuration.node_container_name(config)
            if component == "startup"
            else config["statsd"]
        )
        if not docker.inspect_managed_container(container, config["name"]):
            raise process.OperationError("Container does not exist")
        command = (
            ["docker", "logs", "--tail", str(tail)]
            + (["--follow"] if follow else [])
            + [container]
        )
    else:
        relative = {
            "node": "logs/node/node.log",
            "stderr": "logs/node/stderr.log",
            "stdout": "logs/node/stdout.log",
            # prepare_elections.sh and take_part_in_elections.sh write this.
            # validator.log is written only by the reference kit's own updater
            # cron, which this CLI does not install, so it stays empty forever.
            "elections": "logs/validator/transactions.log",
            "election-cron": "logs/validator/election-cron.log",
        }[component]
        path = store.node_directory(config["name"]) / relative
        # tail -F deliberately waits for a file that does not exist yet, which
        # is useful when following. A plain read should say so instead of
        # leaving the operator with tail's own message.
        if not follow and not path.exists():
            raise process.OperationError(
                f"No {component} log yet at {path}; nothing has written it"
            )
        command = (
            ["tail", "-n", str(tail)] + (["-F"] if follow else []) + [str(path)]
        )
    return command
