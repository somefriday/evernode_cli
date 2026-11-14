"""Proposals joined on this host, voted for in every round without anyone watching.

evernode keeps one store, voting/proposals.json in the state directory: every
proposal joined here, open and past, with every vote sent for it. Nothing else
reads or writes it, and no command takes it as an argument.

Cron is only a clock. It starts `evernode vote tick` every minute; the tick
runs one engine pass (`evernode-vote follow`) for each open proposal whose next
pass is due, and saves the pass's report. The engine keeps nothing: each pass
gets what an earlier one reported through the description pipe, and says when
the next pass is due, a minute later while a vote is missing, otherwise at the
next round's start. One pass runs at a time per state directory: join, leave
and the tick hold that directory's store lock.
"""

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from . import arguments, elections, process, storage, ui, voting

COMMANDS = ("join", "leave", "history", "tick")
STORE_VERSION = 1
# Node names cannot contain an underscore, so no node's election file,
# /etc/cron.d/evernode-<name>, can ever be one of these.
CRON_DIRECTORY = Path("/etc/cron.d")
CRON_HEADER = "# Managed by evernode\n"
# A pass that hangs is killed, and the next minute tries again.
PASS_TIMEOUT = 600
# While a joined node cannot be read, the next pass is at most this far away.
RETRY = 60
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class Busy(process.OperationError):
    """Another join, leave or tick holds the store's lock."""


class PassFailed(process.OperationError):
    """An engine pass ended without a report; the next minute tries again.

    `votes` are the votes it reported counted before it ended: they are kept.
    """

    def __init__(self, message, votes=()):
        super().__init__(message)
        self.votes = list(votes)


class VoteStore:
    """voting/ in the state directory: proposals.json, its lock and the cron log."""

    def __init__(self, state_dir):
        self.state = storage.NodeStateStore(state_dir)
        self.directory = self.state.root / "voting"
        self.path = self.directory / "proposals.json"
        self.log = self.directory / "cron.log"

    def ensure_directory(self):
        self.state.validate_state_root()
        self.state.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        storage.validate_private_path(self.state.root)
        self.directory.mkdir(exist_ok=True, mode=0o700)
        storage.validate_private_path(self.directory)

    def load(self):
        """Every entry, or an empty store; readers need no lock (writes replace the file)."""
        if not self.path.exists():
            return {"version": STORE_VERSION, "proposals": []}
        storage.validate_private_path(self.directory)
        storage.validate_private_path(self.path)
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc:
            raise process.OperationError(f"Cannot read {self.path}: {exc}") from exc
        if (
            not isinstance(data, dict)
            or data.get("version") != STORE_VERSION
            or not isinstance(data.get("proposals"), list)
        ):
            raise process.OperationError(
                f"{self.path} is not a version {STORE_VERSION} vote store"
            )
        return data

    def save(self, data):
        storage.write_json_atomically(self.path, data)

    @contextlib.contextmanager
    def locked(self):
        self.ensure_directory()
        descriptor = os.open(
            self.directory / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Busy(
                    "A vote pass is running on this host; retry in a minute"
                ) from None
            yield


def handle(args, options):
    """join, leave, history and tick."""
    if os.geteuid() != 0:
        raise process.OperationError(
            f"Use sudo evernode vote {args.command}: the vote store and managed node keys are root's"
        )
    store = VoteStore(args.state_dir)
    if args.command == "tick":
        return tick(store)
    if args.command == "history":
        return history(store, options.proposal and proposal_hash(options.proposal))
    hash_ = proposal_hash(options.proposal)
    if args.command == "leave":
        return leave(store, hash_)
    return join(store, options, hash_)


def proposal_hash(value):
    value = (value or "").strip().lower()
    if not HASH_PATTERN.fullmatch(value):
        raise process.OperationError(
            "Name the proposal with --proposal HASH: its 64 hex digits, as propose and status print it"
        )
    return value


def join(store, options, proposal):
    """Show the proposal, ask, save it as open and due now, then vote at once."""
    endpoint = options.endpoint or os.environ.get("EVERNODE_ENDPOINT")
    if not endpoint:
        raise process.OperationError(
            "Name the GraphQL endpoint: set EVERNODE_ENDPOINT or pass --endpoint URL"
        )
    if not (options.name or options.all or options.keyring):
        raise process.OperationError(
            "Name the keys that vote: -n NAME, --all for every managed node, "
            "or --keyring PATH for a node evernode does not manage"
        )
    nodes = []
    if options.name or options.all:
        names = [options.name] if options.name else store.state.list_node_names()
        nodes = voting.managed_key_rings(store.state, names, skip_missing=options.all)
    names = [node["name"] for node in nodes]
    sources = [voting.key_ring_source(value) for value in options.keyring]
    for source in sources:
        voting.require_private_key_ring(source)
    engine = voting.find_engine()
    # Joined already: the preview is held to what earlier passes read, as every tick is.
    joined = open_entry(store.load(), proposal)
    refuse_another_network(store.state, joined, names, proposal)
    with external_key_rings(store, sources) as external:
        net = {"endpoint": endpoint, "nodes": nodes + external}
        if joined is not None and joined.get("config_version") is not None:
            net["known"] = known(joined)
        # The engine's dry run shows the proposal in the terminal.
        preview = run_pass(engine, net, proposal, dry_run=True)
    if preview["state"] != "open":
        raise process.OperationError(f"Nothing to join: {preview['result']}")
    ui.confirm_operation(
        options.yes,
        f"Vote for {proposal[:12]} with {', '.join(names + sources)} now and in every round until it is decided?",
    )
    now = int(time.time())
    with store.locked():
        # Scheduled first: a join that cannot schedule its passes records nothing.
        install_cron(store)
        data = store.load()
        entry = open_entry(data, proposal)
        refuse_another_network(store.state, entry, names, proposal)
        if entry is None:
            entry = {
                "proposal": proposal,
                "state": "open",
                "joined_at": now,
                "ended_at": None,
                "votes": [],
            }
            data["proposals"].append(entry)
        else:
            print(
                f"{proposal[:12]} was joined already: it now votes with {', '.join(names + sources)} "
                f"(before: {voters(entry)}), through the endpoint given now"
            )
        entry.update(nodes=names, keyrings=sources, endpoint=endpoint, next_pass_at=now)
        merge_report(entry, preview, now, record_pass=False)
        store.save(data)
        # The engine prints what the pass did; only a failure is told here.
        run_due_pass(store, engine, data, entry, log=False)
        remove_cron_if_idle(store, data)
    if entry["state"] == "open":
        print(
            f"Joined {proposal[:12]}: next pass {utc(entry['next_pass_at'])}; "
            "sudo evernode vote status shows its progress."
        )
    else:
        print(f"{proposal[:12]} is {entry['state']}.")
    return 0


def refuse_another_network(state, entry, names, proposal):
    """This host keeps one schedule per proposal, and a proposal can have the same
    hash on two networks. Joining it again for nodes on another network would take
    the schedule over and end the first network's votes, so that is refused. Only
    managed nodes name their network; a join with key rings alone cannot be told."""
    if entry is None:
        return
    before, now = _networks(state, entry.get("nodes") or []), _networks(state, names)
    if before and now and before != now:
        raise process.OperationError(
            f"{proposal[:12]} is joined already for nodes on {', '.join(sorted(before))} "
            f"({voters(entry)}); joining it for {', '.join(sorted(now))} would end those votes. "
            f"To move it, leave it first: sudo evernode vote leave --proposal {proposal}"
        )


def _networks(state, names):
    """The networks these managed nodes are on; a node removed since says nothing."""
    networks = set()
    for name in names:
        try:
            networks.add(state.load_node_config(name)["network"])
        except (process.OperationError, KeyError):
            pass
    return networks


def leave(store, proposal):
    with store.locked():
        data = store.load()
        entry = open_entry(data, proposal)
        if entry is None:
            raise process.OperationError(
                f"{proposal[:12]} is not an open proposal joined on this host; see sudo evernode vote history"
            )
        end(entry, "left", int(time.time()))
        store.save(data)
        remove_cron_if_idle(store, data)
    print(
        f"Left {proposal[:12]}: no more votes go out for it. "
        f"sudo evernode vote history --proposal {proposal} shows what was sent."
    )
    return 0


def tick(store):
    """Cron's minute: one pass for every open proposal that is due. A tick that
    finds another pass running leaves the minute to it."""
    try:
        with store.locked():
            data = store.load()
            now = int(time.time())
            due = [e for e in data["proposals"] if e["state"] == "open" and e["next_pass_at"] <= now]
            if due:
                engine = voting.find_engine()
                for entry in due:
                    run_due_pass(store, engine, data, entry)
            remove_cron_if_idle(store, data)
    except Busy:
        return 0
    return 0


def known(entry):
    """What earlier passes read, for the engine's stale-endpoint guard and the close."""
    return {
        "param": entry["param"],
        "target": entry["target"],
        "config_version": entry["config_version"],
    }


@contextlib.contextmanager
def external_key_rings(store, sources, missing=None):
    """The engine's nodes for join --keyring sources, for one engine run.

    A key ring copied out of a container lives in a root-only directory of the
    vote store until the run ends. A source that cannot be read is listed in
    `missing` when it is given, and refused when it is not.
    """
    if not sources:
        yield []
        return
    store.ensure_directory()
    with tempfile.TemporaryDirectory(dir=store.directory) as directory:
        nodes = []
        for source in sources:
            try:
                nodes.append(voting.read_key_ring(source, directory))
            except process.OperationError as exc:
                if missing is None:
                    raise
                missing.append(str(exc))
        yield nodes


def voters(entry):
    """The managed nodes and other key rings a joined proposal votes with."""
    return ", ".join(entry["nodes"] + entry.get("keyrings", []))


def run_due_pass(store, engine, data, entry, *, log=True):
    """One engine pass for `entry`; its report is saved, or only the failure.

    Each vote is saved the moment the engine reports it counted, so a vote
    survives evernode itself being killed. `log`: print the pass's result line
    (the cron log); a failure is always printed.
    """
    now = int(time.time())
    nodes, missing = [], []
    for name in entry["nodes"]:
        try:
            nodes += voting.managed_key_rings(store.state, [name], skip_missing=False)
        except process.OperationError as exc:
            missing.append(str(exc))

    def counted(vote):
        merge_votes(entry, [vote])
        store.save(data)

    with external_key_rings(store, entry.get("keyrings", []), missing) as external:
        net = {"endpoint": entry["endpoint"], "nodes": nodes + external}
        if entry.get("config_version") is not None:
            net["known"] = known(entry)
        try:
            if not net["nodes"]:
                raise PassFailed("no joined key ring could be read: " + "; ".join(missing))
            report = run_pass(engine, net, entry["proposal"], dry_run=False, on_vote=counted)
        except process.OperationError as exc:
            merge_votes(entry, getattr(exc, "votes", ()))
            entry["last_pass"] = {"at": now, "error": str(exc)}
            store.save(data)
            print(f"[{utc(now)}] {entry['proposal'][:12]}: pass failed: {exc}; next minute tries again", flush=True)
            return
    # Stamped when the report arrived: a pass that closes a proposal with its own vote ends after the vote.
    now = int(time.time())
    merge_report(entry, report, now)
    if missing:
        entry["last_pass"]["error"] = "; ".join(
            filter(None, [entry["last_pass"].get("error"), *missing])
        )
        # A node that could not be read may be back in a minute: its vote does not wait for the next round.
        if entry["state"] == "open":
            entry["next_pass_at"] = min(entry["next_pass_at"], now + RETRY)
    store.save(data)
    if log:
        following = f"; next pass {utc(entry['next_pass_at'])}" if entry["state"] == "open" else ""
        print(f"[{utc(now)}] {entry['proposal'][:12]}: {report['result']}{following}", flush=True)


def run_pass(engine, net, proposal, *, dry_run, on_vote=None):
    """Run `evernode-vote follow` and return its JSON report.

    The description goes through a pipe, as for every other engine command.
    The engine's progress (stderr) is passed on line by line, to the terminal at
    join and to the cron log at a tick, while its stdout is read: JSON lines,
    each vote the moment it is counted (handed to `on_vote` at once), then the
    report. Neither pipe can fill while the other is read. The error the engine
    ends with is not passed on: it is raised as PassFailed, with the votes it
    gave, and shown or logged from there. A pass that runs for PASS_TIMEOUT is
    killed.
    """
    sys.stdout.flush()
    read, write = os.pipe()
    argv = [engine, "follow", "--net", f"/dev/fd/{read}", "--proposal", proposal]
    if dry_run:
        argv.append("--dry-run")
    try:
        child = subprocess.Popen(
            argv,
            pass_fds=(read,),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=voting.engine_environment(),
        )
    except BaseException:
        os.close(write)
        raise
    finally:
        os.close(read)
    try:
        with os.fdopen(write, "w") as pipe:
            json.dump(net, pipe)
    except BrokenPipeError:
        pass  # the engine stopped before reading, and says why
    killed = threading.Event()

    def kill():
        killed.set()
        child.kill()

    error = []

    def pass_on_progress():
        for line in child.stderr:
            if line.startswith("Error: ") or error:
                error.append(line.strip())
            else:
                sys.stderr.write(line)
                sys.stderr.flush()

    progress = threading.Thread(target=pass_on_progress, daemon=True)
    timer = threading.Timer(PASS_TIMEOUT, kill)
    progress.start()
    timer.start()
    votes, report, bad = [], None, None
    try:
        for line in child.stdout:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError as exc:
                bad = bad or exc
                continue
            if isinstance(value, dict) and isinstance(value.get("vote"), dict):
                votes.append(value["vote"])
                if on_vote is not None:
                    on_vote(value["vote"])
            else:
                report = value
        code = child.wait()
        progress.join()
    finally:
        timer.cancel()
    if killed.is_set():
        raise PassFailed(f"the pass was killed after {PASS_TIMEOUT} s", votes)
    if code != 0:
        message = " ".join(error).removeprefix("Error: ") or f"exit code {code}"
        raise PassFailed(message, votes)
    if bad is not None:
        raise PassFailed(f"the engine wrote a line that is not JSON: {bad}", votes)
    if not isinstance(report, dict) or report.get("proposal") != proposal:
        raise PassFailed("the engine reported no report for this proposal", votes)
    return report


def open_entry(data, proposal):
    return next(
        (e for e in data["proposals"] if e["proposal"] == proposal and e["state"] == "open"),
        None,
    )


def merge_report(entry, report, now, *, record_pass=True):
    """Fold a pass's report into its entry; fields a closed proposal no longer has stay as they were.

    The change keeps the description it had when joined: a later pass describes it against the param as it
    is then, which another proposal may have changed meanwhile.
    """
    for field in ("change", "change_lines"):
        if entry.get(field) is None and report.get(field) is not None:
            entry[field] = report[field]
    for field in ("critical", "progress", "earliest_acceptance"):
        if report.get(field) is not None:
            entry[field] = report[field]
    entry.update(
        param=report["param"],
        target=report["target"],
        config_version=report["config_version"],
        round_end=report["round_end"],
        round_end_exact=report["round_end_exact"],
    )
    merge_votes(entry, report["votes"])
    if record_pass:
        entry["last_pass"] = {"at": now, "result": report["result"]}
        if report.get("errors"):
            entry["last_pass"]["error"] = "; ".join(report["errors"])
    if report["state"] == "open":
        if record_pass:
            entry["next_pass_at"] = report["next_pass_at"]
    else:
        end(entry, report["state"], now)


def merge_votes(entry, new):
    """Add votes to the entry: one per round and key, keeping the one with its transaction."""
    votes = entry.setdefault("votes", [])
    for vote in new:
        same = next(
            (v for v in votes if v["round"] == vote["round"] and v["key"] == vote["key"]),
            None,
        )
        if same is None:
            votes.append(vote)
        elif vote.get("transaction") and not same.get("transaction"):
            same.update(vote)


def end(entry, state, now):
    """The proposal ends for this host; its endpoint, which may hold a project key, goes."""
    entry.update(state=state, ended_at=now, next_pass_at=None)
    entry.pop("endpoint", None)


def cron_path(store):
    """This state directory's own cron file, so that two directories on one host never
    replace or remove each other's: evernode_vote for the default directory,
    evernode_vote_<8 hex digits of the directory's hash> for any other."""
    root = store.state.root
    if root == Path(arguments.DEFAULT_STATE_DIR):
        return CRON_DIRECTORY / "evernode_vote"
    return CRON_DIRECTORY / f"evernode_vote_{hashlib.sha256(str(root).encode()).hexdigest()[:8]}"


def install_cron(store):
    path = cron_path(store)
    executable = shutil.which("evernode")
    if executable is None:
        raise process.OperationError(
            "evernode is not on PATH, and cron needs it to vote: install it system-wide"
        )
    content = (
        CRON_HEADER
        + "SHELL=/bin/bash\n"
        + "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
        + f"* * * * * root {shlex.quote(executable)} --state-dir {shlex.quote(str(store.state.root))}"
        + f" vote tick >> {shlex.quote(str(store.log))} 2>&1\n"
    )
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.read_text().startswith(CRON_HEADER):
            raise process.OperationError(f"Refusing to replace an unrecognized cron file: {path}")
        if path.read_text() == content:
            return
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def remove_cron_if_idle(store, data):
    """The cron line exists only while a proposal is open."""
    if any(e["state"] == "open" for e in data["proposals"]):
        return
    path = cron_path(store)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.read_text().startswith(CRON_HEADER):
            raise process.OperationError(f"Refusing to remove an unrecognized cron file: {path}")
        path.unlink()


def print_status(state_dir):
    """The open proposals joined here, for `status` with sudo."""
    store = VoteStore(state_dir)
    if not store.path.exists():
        return
    entries = [e for e in store.load()["proposals"] if e["state"] == "open"]
    print()
    if not entries:
        print("joined      none open on this host (sudo evernode vote history lists past ones)")
        return
    for entry in entries:
        print(f"joined      {entry['proposal']}: {summary(entry)}")
        print(f"            nodes {voters(entry)}; {progress_line(entry)}")
        last = entry.get("last_pass") or {}
        if last:
            outcome = last.get("result") or ""
            if last.get("error"):
                outcome = (outcome + "; " if outcome else "") + "error: " + last["error"]
            print(f"            last pass {utc(last['at'])}: {outcome}")
        print(f"            next pass {utc(entry['next_pass_at'])}")
    if not cron_path(store).exists():
        print(
            f"WARNING     {cron_path(store)} is missing: the joined proposals get no passes; "
            "join one of them again to schedule them"
        )
    state = elections.cron_daemon_state()
    if state == "not running":
        print(
            "WARNING     no cron daemon is running: joined proposals get no votes until one runs"
        )


def history(store, proposal=None):
    entries = sorted(store.load()["proposals"], key=lambda e: e["joined_at"], reverse=True)
    if proposal:
        entries = [e for e in entries if e["proposal"] == proposal]
        if not entries:
            raise process.OperationError(f"{proposal[:12]} was never joined on this host")
    if not entries:
        print("No proposal was joined on this host.")
        return 0
    for entry in entries:
        ended = f", {entry['state']} {utc(entry['ended_at'])}" if entry["ended_at"] else ", open"
        print(f"{entry['proposal']}: {summary(entry)}")
        print(
            f"    joined {utc(entry['joined_at'])}{ended}; nodes {voters(entry)}; "
            f"{len(entry['votes'])} vote(s); {progress_line(entry)}"
        )
        if proposal:
            for line in entry.get("change_lines") or []:
                print(f"    {line}")
            for vote in sorted(entry["votes"], key=lambda v: (v["round"], v["node"])):
                sent = f"sent {utc(vote['sent_at'])}, " if vote.get("sent_at") else ""
                counted = (
                    f"counted {utc(vote['counted_at'])} in transaction {vote['transaction']}"
                    if vote.get("transaction")
                    else "found counted (cast elsewhere or by an earlier pass)"
                )
                print(
                    f"    round {vote['round']}: {vote['node']} as validator #{vote['validator']} "
                    f"({vote['key'][:12]}): {sent}{counted}"
                )
    return 0


def summary(entry):
    """What a proposal changes, in a line: a param change by its changed fields."""
    change = entry.get("change") or ""
    lines = entry.get("change_lines") or []
    if change.startswith("set p") and lines:
        return change.split(" =", 1)[0] + ": " + "; ".join(lines)
    return change


def progress_line(entry):
    p = entry.get("progress")
    if not p:
        return "no progress read yet"
    won = ", won" if p["won_this_round"] else ""
    text = (
        f"round {p['round']}: wins {p['wins']}/{p['wins_needed']}, "
        f"losses {p['losses']}/{p['losses_allowed']}, {p['rounds_left']} round(s) left, "
        f"{p['voters']} voter(s){won}"
    )
    # an ended proposal is no longer read: this is how it stood the last time it was open
    return text if entry["state"] == "open" else "last read open: " + text


def utc(t):
    return time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(t)) if t is not None else "never"
