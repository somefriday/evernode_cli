"""Install and remove one owned reference-election schedule per node."""

import contextlib
import fcntl
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from . import configuration, process, storage

TRUE_VALUES = (True, 1, "1", "true", "True", "yes", "Yes")


def node_election_cron_path(name):
    return Path("/etc/cron.d") / ("evernode-" + configuration.validate_node_name(name))


def disable_node_election_schedule(name):
    path = node_election_cron_path(name)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.read_text().startswith(
            "# Managed by evernode\n"
        ):
            raise process.OperationError("Refusing to remove an unrecognized cron file")
        path.unlink()


def enable_node_election_schedule(store, config, executable="/usr/local/bin/evernode"):
    """Write an independent cron file using the upstream election sequence.

    Cron owns no secrets: it only invokes scripts in the already root-private
    workspace. The flock prevents an interval from overlapping a slow previous
    election attempt.
    """
    name = configuration.validate_node_name(config["name"])
    if node_election_cron_path(name).exists():
        raise process.OperationError("Election schedule is already enabled")
    workspace = store.node_directory(name)
    for directory in (
        workspace,
        workspace / "scripts",
        workspace / "logs" / "validator",
        workspace / "elections",
    ):
        storage.validate_private_path(directory)
    runner = workspace / "run-elections.sh"
    sequence = workspace / "run-election-sequence.sh"
    lock = workspace / "elections" / "evernode-election.lock"
    log = workspace / "logs" / "validator" / "election-cron.log"
    # This is deliberately the same scheduled sequence as validation_new.md.
    # part_check.sh is a read-only post-submission inspection, not a cron step.
    sequence.write_text(
        "#!/bin/bash\nset -euo pipefail\ncd "
        + shlex.quote(str(workspace / "scripts"))
        + "\n"
        "./prepare_elections.sh\nsleep 120\n./take_part_in_elections.sh\n"
    )
    os.chmod(sequence, 0o700)
    runner.write_text(
        "#!/bin/bash\nset -euo pipefail\nexec flock -n "
        + shlex.quote(str(lock))
        + " /bin/bash "
        + shlex.quote(str(sequence))
        + "\n"
    )
    os.chmod(runner, 0o700)
    interval = configuration.build_node_environment(config)["CRONTAB_INTERVAL"]
    content = (
        "# Managed by evernode\n"
        "SHELL=/bin/bash\n"
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
        f"*/{interval} * * * * root {runner} >> {log} 2>&1\n"
    )
    path = node_election_cron_path(name)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def election_schedule_status(store, config):
    path = node_election_cron_path(config["name"])
    return {
        "enabled": path.exists()
        and not path.is_symlink()
        and path.read_text().startswith("# Managed by evernode\n"),
        "cron_file": str(path),
        "log": str(
            store.node_directory(config["name"])
            / "logs"
            / "validator"
            / "election-cron.log"
        ),
    }


def cron_daemon_state():
    """Report whether a cron daemon is actually running on this host.

    An installed cron file alone does not run anything. Container and minimal
    server images frequently have no cron daemon, which silently stops every
    scheduled election.
    """
    executable = shutil.which("pgrep")
    if not executable:
        return "unknown"
    for name in ("cron", "crond"):
        try:
            found = subprocess.run(
                [executable, "-x", name], capture_output=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        if found.returncode == 0:
            return "running"
    return "not running"


def describe_election_state(store, config, statistics=None, console_error=None):
    """Report the schedule, cron health and what the chain says about this node.

    An enabled schedule proves only that the job is installed. Admission into
    a validator set and actual block validation are separate on-chain facts,
    so they are reported separately and never inferred from the schedule.
    """
    result = election_schedule_status(store, config)
    result["cron_daemon"] = cron_daemon_state()
    if result["enabled"] and result["cron_daemon"] == "not running":
        result["warning"] = (
            "The schedule is installed but no cron daemon is running, "
            "so no election will be attempted"
        )
    if console_error is not None:
        result["chain"] = {"error": console_error}
        return result
    if not isinstance(statistics, dict):
        return result
    current = statistics.get("in_current_vset_p34") in TRUE_VALUES
    following = statistics.get("in_next_vset_p36") in TRUE_VALUES
    validation = statistics.get("validation_status")
    result["chain"] = {
        "in_current_vset_p34": current,
        "in_next_vset_p36": following,
        "validation_status": validation,
        "last_validation_ago_sec": statistics.get("last_validation_ago_sec"),
        "validating": current and validation == "Active",
        "elected_for_next_round": following,
    }
    return result


@contextlib.contextmanager
def hold_election_lock(store, name):
    """Hold the same lock a scheduled election run takes.

    A scheduled sequence already in progress keeps this lock for its whole
    run, so a maintenance action waits for it instead of changing files
    underneath a signing attempt.
    """
    path = store.node_directory(name) / "elections" / "evernode-election.lock"
    # The reference runner creates this lock itself under the default umask, so
    # only its private parent directory and the absence of a symlink are checked.
    storage.validate_private_path(path.parent)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise process.OperationError(
                f"A scheduled election run holds {name}'s election lock; retry when it finishes"
            ) from None
        yield
