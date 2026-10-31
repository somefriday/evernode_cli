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

# Seconds a submitted recovery is given to reach the wallet before the bid
# reads its balance. Production election windows last hours, so this is only
# ever a small part of one; the generated script reads it from the environment
# so a short-window test network can shorten it without editing the script.
RECOVERY_SETTLE_SECONDS = 120

DEPOOL_SEQUENCE_FILE = "run-election-sequence.sh"
SOLO_SEQUENCE_FILE = "run-solo-election-sequence.sh"


def node_election_cron_path(name):
    return Path("/etc/cron.d") / ("evernode-" + configuration.validate_node_name(name))


def election_sequence_path(store, config):
    """The sequence cron runs for this node, which differs by validator type.

    A nominator validator shares the solo sequence: in both modes recovery and
    bidding are independent, because the stake goes to the elector rather than
    through a DePool whose rounds the preparation step has to advance first.
    """
    direct = configuration.validator_type(config) in ("solo", "nominator")
    return store.node_directory(config["name"]) / (
        SOLO_SEQUENCE_FILE if direct else DEPOOL_SEQUENCE_FILE
    )


def _depool_sequence_program(scripts_dir):
    """Prepare, then bid, aborting if preparation fails.

    This is deliberately the same scheduled sequence as validation_new.md.
    part_check.sh is a read-only post-submission inspection, not a cron step.
    Preparation here also ticks the DePool that the bid is placed into, so a
    preparation that did not finish must stop the cycle rather than bid into a
    DePool whose rounds were never advanced.
    """
    return (
        "#!/bin/bash\nset -euo pipefail\ncd "
        + shlex.quote(str(scripts_dir))
        + "\n"
        "./prepare_elections.sh\nsleep 120\n./take_part_in_elections.sh\n"
    )


def _solo_sequence_program(scripts_dir, preparation_log):
    """Recover finished rounds, then bid, because the two are independent.

    A solo validator stakes straight to the elector and a nominator validator
    stakes through its own contract, so in neither case does the bid need
    anything preparation produces: preparation only asks the elector for
    rounds that have finished. A recovery that cannot run must therefore not
    cancel the bid for the election that is open now, which is the one thing
    the node cannot do later. Each outcome is named in the cron log instead.

    The wait exists only to let a recovery that was actually submitted arrive
    before the bid reads the staking account's balance, so a cycle with
    nothing to recover does not spend any of the election window waiting.
    """
    return f"""#!/bin/bash
# Direct-to-elector election cycle, written by evernode election start.
set -uo pipefail
cd {shlex.quote(str(scripts_dir))}
log={shlex.quote(str(preparation_log))}
./prepare_elections.sh 2>&1 | tee "$log"
status=${{PIPESTATUS[0]}}
case $status in
    0) ;;
    3)
        # An interrupted attempt leaves a half-signed request to the elector.
        # Preparation refuses to add a second one, and the bid step finishes
        # the one already in the wallet instead of queueing another.
        echo "+++-INFO: preparation stopped at a pending request to the elector;"
        echo "+++-INFO: the bid step will finish that one rather than add another."
        ;;
    *)
        echo "+++-WARNING: preparation exited ${{status}}; bidding anyway, because a"
        echo "+++-WARNING: solo bid goes to the elector and does not depend on it."
        ;;
esac
if grep -qE 'recover_amount = [1-9][0-9]* nanotokens' "$log";then
    settle=${{ELECTION_SETTLE_SECONDS:-{RECOVERY_SETTLE_SECONDS}}}
    echo "---INFO: waiting ${{settle}}s for the recovered stake to reach the wallet"
    sleep "$settle"
fi
./take_part_in_elections.sh
"""


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
    sequence = election_sequence_path(store, config)
    lock = workspace / "elections" / "evernode-election.lock"
    log = workspace / "logs" / "validator" / "election-cron.log"
    # A node enabled before the sequences were separated has the other type's
    # file sitting beside this one. Nothing references it, but leaving it there
    # makes it unclear which script cron runs.
    for other in (DEPOOL_SEQUENCE_FILE, SOLO_SEQUENCE_FILE):
        stale = workspace / other
        if stale != sequence and stale.is_file() and not stale.is_symlink():
            stale.unlink()
    if configuration.validator_type(config) in ("solo", "nominator"):
        sequence.write_text(
            _solo_sequence_program(
                workspace / "scripts",
                workspace / "elections" / "last-preparation.log",
            )
        )
    else:
        sequence.write_text(_depool_sequence_program(workspace / "scripts"))
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
        "sequence": str(election_sequence_path(store, config)),
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
