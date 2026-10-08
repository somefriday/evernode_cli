"""Install and remove one owned reference-election schedule per node."""

import os
import shlex
from pathlib import Path

from . import configuration, process, storage


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
