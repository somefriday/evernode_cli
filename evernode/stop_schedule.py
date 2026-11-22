"""A node stop scheduled for a masterchain block, so that the block's state can be exported.

`node stop --after-block N` leaves a cron file and a record. Cron is only a
clock: every minute it starts a check, which reads the node's console until
the node has applied block N and every shard block N lists, lets the node run
SETTLE_SECONDS more, and then stops it the ordinary way, once. A node is never
stopped late: one found past the block that no check saw below it within
MISSED_SECONDS is left running, and the log says so.

The cron file is the schedule. Removing it ends the scheduled stop, and
whoever removes it decides: a cancel, any other stop of the node, or the check
that is about to stop it. So a cancelled stop is never carried out, and
nothing here needs a lock against the others.

This module reads no console and stops no node: cli hands a check both. That
lets nodes.stop_node end a schedule without an import cycle.
"""

import contextlib
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import time

from . import configuration, elections, process, storage

CRON_DIRECTORY = Path("/etc/cron.d")
CRON_HEADER = "# Managed by evernode\n"
RECORD_VERSION = 1
# Seconds between console readings, and how long one check reads: cron starts
# the next check a minute after this one.
POLL_SECONDS = 5
CHECK_SECONDS = 55
# How long the node runs on after it reports the block applied. It reports from
# memory and writes its block records in the background, with no way to ask how
# far that writer is; a block's state is kept for 30 minutes, so the wait costs
# nothing (NODE-6).
SETTLE_SECONDS = 30
# A node found at or past the block is stopped only if a reading showed it
# below the block within this time. Longer ago, it passed the block while no
# check ran, and it keeps a block's state for 30 minutes only (NODE-6).
MISSED_SECONDS = 600
# What one console reading shows of a node's way to the block.
BELOW, REACHED, READY = "below", "reached", "ready"


class Busy(process.OperationError):
    """An earlier check of this node's scheduled stop is still working."""


def cron_path(name):
    """The node's own cron file. A node name holds no underscore, so this is
    never an election file (evernode-NAME) or the vote file (evernode_vote)."""
    return CRON_DIRECTORY / ("evernode_stop_" + configuration.validate_node_name(name))


def record_path(store, name):
    return store.node_directory(name) / "stop-at-block.json"


def log_path(store, name):
    return store.node_directory(name) / "logs" / "validator" / "stop-at-block.log"


def shard_client_block_time(statistics):
    """Creation time of the masterchain block whose shard blocks are all applied.

    The console gives it as a lag from the node's clock, as it gives the last
    applied block's, so the clock cancels out. None while a value is missing.
    """
    values = [statistics.get(k) for k in ("masterchainblocktime", "timediff", "shards_timediff")]
    if not all(type(v) is int for v in values):
        return None
    block_time, lag, shard_lag = values
    return block_time + lag - shard_lag


class BlockWait:
    """Whether a node has applied masterchain block `seqno` and every shard block it lists.

    Fed console readings in order. The node has reached the block at the first
    reading at or past it; that block's creation time is kept. It is ready once
    the shard client's block is as new as that time: block times only grow, so
    the shard client is then at `seqno` or later. A reading without whole
    numbers never moves the wait forward.
    """

    def __init__(self, seqno):
        self.seqno = seqno
        self.block = None  # The last applied masterchain block, as last read.
        self.reached_time = None

    def state(self, statistics):
        """BELOW, REACHED or READY, or None for a reading without a block number."""
        block = statistics.get("masterchainblocknumber")
        if type(block) is not int:
            return None
        self.block = block
        if block < self.seqno:
            return BELOW
        if self.reached_time is None:
            block_time = statistics.get("masterchainblocktime")
            if type(block_time) is not int:
                return REACHED
            self.reached_time = block_time
        shard_time = shard_client_block_time(statistics)
        if shard_time is None or shard_time < self.reached_time:
            return REACHED
        return READY

    def progress(self):
        """The fields of a log line that say how far the node is."""
        if self.block is None:
            return {"error": "The node reports no masterchain block yet"}
        return {
            "masterchain_block": self.block,
            "blocks_left": max(self.seqno - self.block, 0),
        }


def is_scheduled(name):
    path = cron_path(name)
    try:
        return not path.is_symlink() and path.read_text().startswith(CRON_HEADER)
    except FileNotFoundError:
        return False


def remove_cron(name):
    """End the node's scheduled stop; True when this call removed its cron file.

    nodes.stop_node calls it for every stop of a node, and a check calls it
    before it stops the node. Of several callers exactly one gets True.
    """
    path = cron_path(name)
    try:
        if path.is_symlink() or not path.read_text().startswith(CRON_HEADER):
            raise process.OperationError(
                f"Refusing to remove an unrecognized cron file: {path}"
            )
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def load_record(store, name):
    """The scheduled stop's block, timeout and force, and when a reading last
    showed the node below the block."""
    path = record_path(store, name)
    try:
        storage.validate_private_path(path)
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise process.OperationError(f"Cannot read {path}: {exc}") from exc
    if (
        not isinstance(value, dict)
        or value.get("version") != RECORD_VERSION
        or type(value.get("after_block")) is not int
        or value["after_block"] < 1
        or type(value.get("timeout")) is not int
        or value["timeout"] < 1
        or type(value.get("force")) is not bool
        or type(value.get("seen_below_at")) is not int
    ):
        raise process.OperationError(
            f"{path} is not a version {RECORD_VERSION} record of a scheduled stop"
        )
    return value


def describe(store, name):
    """What is scheduled for the node, and where its log is."""
    record = load_record(store, name) if is_scheduled(name) else None
    return {
        "node": name,
        "scheduled_stop": record
        and {key: record[key] for key in ("after_block", "timeout", "force")},
        "log": str(log_path(store, name)),
    }


def schedule(store, name, seqno, statistics, *, timeout, force):
    """Install the stop of node `name` at masterchain block `seqno`.

    `statistics` is a console reading taken now. The node must still be below
    the block: from there it reaches the block only by applying it, while
    nothing shows whether a node already past it still holds the block's state.
    Returns the log's size before the first line, for watch().
    """
    if is_scheduled(name):
        raise process.OperationError(
            f"A stop is already scheduled for {name}; remove it first with "
            f"evernode node stop -n {name} --cancel"
        )
    # A check ends within seconds of its schedule's end. Until then it must not
    # find a new cron file, which it would take for its own.
    if _check_is_working(store, name):
        raise process.OperationError(
            f"A check of the stop scheduled for {name} before is still ending; "
            "retry in a few seconds"
        )
    block = statistics.get("masterchainblocknumber")
    if type(block) is not int:
        raise process.OperationError(
            "The node reports no masterchain block yet; schedule the stop once "
            "node status shows one"
        )
    if block >= seqno:
        raise process.OperationError(
            f"Masterchain block {seqno} is already applied: the node is at block "
            f"{block}. Schedule the stop before the node reaches the block. To stop "
            "the node now, run node stop without --after-block; state-export info "
            f"then shows whether block {seqno} can still be exported."
        )
    if elections.cron_daemon_state() == "not running":
        raise process.OperationError(
            "No cron daemon is running, and cron is what carries out a scheduled stop"
        )
    executable = shutil.which("evernode")
    if executable is None:
        raise process.OperationError(
            "evernode is not on PATH, and cron needs it to stop the node: install "
            "it system-wide"
        )
    log = log_path(store, name)
    # Opened before anything is installed: a stop must not be scheduled with
    # nowhere to report.
    with open(log, "a"):
        pass
    offset = log.stat().st_size
    # The record first and the cron file last: a check never finds its cron
    # file without the record.
    storage.write_json_atomically(
        record_path(store, name),
        {
            "version": RECORD_VERSION,
            "after_block": seqno,
            "timeout": timeout,
            "force": force,
            "seen_below_at": int(time.time()),
        },
    )
    path = cron_path(name)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        CRON_HEADER
        + "SHELL=/bin/bash\n"
        + "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
        + f"* * * * * root {shlex.quote(executable)} --state-dir {shlex.quote(str(store.root))}"
        + f" node stop -n {name} --check >> {shlex.quote(str(log))} 2>&1\n"
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    _log(
        store,
        name,
        result="scheduled",
        after_block=seqno,
        masterchain_block=block,
        blocks_left=seqno - block,
    )
    return offset


def cancel(store, name):
    """Remove the node's scheduled stop; False when there is none."""
    if not remove_cron(name):
        return False
    record_path(store, name).unlink(missing_ok=True)
    _log(store, name, result="cancelled")
    return True


def check(
    store, name, *, read, stop, clock=time.monotonic, sleep=time.sleep, now=time.time
):
    """Cron's minute: read the console until the block is applied, then stop the node.

    `read()` returns one console reading and `stop(timeout, force)` is the
    ordinary node stop; both raise process.OperationError. A check that finds
    an earlier one still working leaves the minute to it.
    """
    try:
        with _hold_check_lock(store, name):
            if is_scheduled(name):
                _check(store, name, read, stop, clock, sleep, now)
    except Busy:
        pass
    return 0


def _check(store, name, read, stop, clock, sleep, now):
    try:
        record = load_record(store, name)
    except process.OperationError as exc:
        _give_up(store, name, str(exc), "Schedule the stop again")
        return
    seqno = record["after_block"]
    wait = _wait_for_block(store, name, record, read, clock, sleep, now)
    if wait is None:
        return
    _log(store, name, **wait.progress(), stop_in_seconds=SETTLE_SECONDS)
    for _ in range(SETTLE_SECONDS // POLL_SECONDS):
        if not is_scheduled(name):
            return
        sleep(POLL_SECONDS)
    # Removing the cron file claims the stop: a cancel that came first has
    # removed it already, and one that comes later finds nothing to cancel.
    if not remove_cron(name):
        return
    try:
        stop(record["timeout"], record["force"])
    except (process.OperationError, OSError, ValueError) as exc:
        _log(
            store,
            name,
            result="failed",
            error=str(exc),
            hint="The stop is not tried again and nothing is scheduled any more. "
            f"To export block {seqno}, stop the node within 30 minutes of the "
            f"block: evernode node stop -n {name}",
        )
    else:
        _log(
            store,
            name,
            result="stopped",
            after_block=seqno,
            masterchain_block=wait.block,
        )
    finally:
        record_path(store, name).unlink(missing_ok=True)


def _wait_for_block(store, name, record, read, clock, sleep, now):
    """Read the console for one check's time. The wait once the node is ready;
    None when it is not yet, or when the schedule has ended."""
    seqno = record["after_block"]
    wait = BlockWait(seqno)
    seen_below_at = record["seen_below_at"]
    deadline = clock() + CHECK_SECONDS
    while True:
        state = error = None
        try:
            state = wait.state(read())
        except (process.OperationError, OSError, ValueError) as exc:
            error = str(exc)
        if not is_scheduled(name):
            return None  # Cancelled, or another command stopped the node.
        if state == BELOW:
            seen_below_at = int(now())
        elif state and now() - seen_below_at > MISSED_SECONDS:
            _give_up(
                store,
                name,
                f"The node is at block {wait.block} and was last seen below block "
                f"{seqno} at {_stamp(seen_below_at)}, more than "
                f"{MISSED_SECONDS // 60} minutes ago: it passed the block while no "
                "check ran, and may no longer hold the block's state. The node was "
                "not stopped.",
                f"To stop it now: evernode node stop -n {name}; state-export info "
                f"then shows whether block {seqno} can still be exported",
            )
            return None
        if state == READY:
            return wait
        if clock() >= deadline:
            # The next check may find the node past the block: it then knows
            # how long ago the node was still below it.
            if seen_below_at != record["seen_below_at"]:
                storage.write_json_atomically(
                    record_path(store, name), {**record, "seen_below_at": seen_below_at}
                )
            _log(store, name, **({"error": error} if error else wait.progress()))
            return None
        sleep(POLL_SECONDS)


def _give_up(store, name, error, hint):
    """End the schedule without stopping the node, unless it has ended already."""
    if remove_cron(name):
        record_path(store, name).unlink(missing_ok=True)
        _log(
            store,
            name,
            result="failed",
            error=error,
            hint="Nothing is scheduled any more. " + hint,
        )


def watch(store, name, *, offset=None, sleep=time.sleep, out=print):
    """Print the scheduled stop's log lines as they come, until it has ended.

    It has ended when the cron file is gone and no check is working: a check
    removes the file before it stops the node, and writes the result after.
    Without `offset` the log's latest line comes first. Returns the log's last
    result, or None.
    """
    log = log_path(store, name)
    result = None
    if offset is None:
        lines, offset = _read_lines(log, 0)
        for line in lines:
            result = _result(line) or result
        if lines:
            out(lines[-1])
    while True:
        ended = not is_scheduled(name) and not _check_is_working(store, name)
        lines, offset = _read_lines(log, offset)
        for line in lines:
            out(line)
            result = _result(line) or result
        if ended:
            return result
        sleep(1)


@contextlib.contextmanager
def _hold_check_lock(store, name):
    """Held by a check for as long as it works, so that checks never overlap."""
    path = store.node_directory(name) / "stop-at-block.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy(f"A check of {name}'s scheduled stop is working") from None
        yield


def _check_is_working(store, name):
    try:
        with _hold_check_lock(store, name):
            return False
    except Busy:
        return True


def _stamp(moment=None):
    return time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(moment))


def _log(store, name, **entry):
    line = json.dumps({"time": _stamp(), "node": name, **entry})
    with open(log_path(store, name), "a") as stream:
        stream.write(line + "\n")


def _read_lines(path, offset):
    """The log's complete lines from `offset` on, and where the next one begins."""
    try:
        with open(path, "rb") as stream:
            stream.seek(offset)
            data = stream.read()
    except FileNotFoundError:
        return [], offset
    end = data.rfind(b"\n") + 1
    return data[:end].decode(errors="replace").splitlines(), offset + end


def _result(line):
    """The result a log line reports, or None: cron also appends a check's stderr."""
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value.get("result") if isinstance(value, dict) else None
