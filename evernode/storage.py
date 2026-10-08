"""Persist node state, secure workspace files and coordinate operations."""

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from . import configuration, process


def write_json_atomically(path, value):
    path = Path(path)
    descriptor, tmp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def validate_private_path(path):
    """Check managed files without following symlinks; CLI operations run as root."""
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
    ):
        raise process.OperationError(
            f"Managed path must be private and owned by the current user: {path}"
        )
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise process.OperationError(f"Unsupported managed file type: {path}")


class NodeStateStore:
    """Root-managed per-node metadata, workspace paths and operation locks."""

    def __init__(self, root):
        self.root = Path(root).expanduser().absolute()
        if any(c in str(self.root) for c in ("\0", "\n", "\r", ":", "$")):
            raise process.OperationError(
                "State directory contains unsupported Compose path characters"
            )

    def node_directory(self, name):
        return self.root / "nodes" / configuration.validate_node_name(name)

    def images_directory(self, create=False):
        """Return the private managed-image registry directory."""
        path = self.root / "images"
        if create:
            self.validate_state_root()
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            validate_private_path(self.root)
            path.mkdir(exist_ok=True, mode=0o700)
        return path

    def validate_state_root(self):
        for ancestor in (self.root, *self.root.parents):
            if ancestor.is_symlink() or (
                ancestor.exists()
                and (ancestor.stat().st_uid != 0 or ancestor.stat().st_mode & 0o022)
            ):
                raise process.OperationError(
                    f"State directory ancestor must be root-owned and not writable by others: {ancestor}"
                )

    def validate_node_directory(self, name):
        self.validate_state_root()
        for path in (self.root, self.root / "nodes", self.node_directory(name)):
            validate_private_path(path)

    def load_node_config(self, name):
        self.validate_node_directory(name)
        path = self.node_directory(name) / "node.json"
        validate_private_path(path)
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise process.OperationError(f"Cannot read node {name}: {exc}") from exc
        return configuration.validate_saved_node_config(value, name)

    def list_node_names(self):
        self.validate_state_root()
        directory = self.root / "nodes"
        if not directory.exists():
            return []
        validate_private_path(self.root)
        validate_private_path(directory)
        names = []
        for path in directory.iterdir():
            if path.is_symlink():
                raise process.OperationError(
                    f"Refusing symlinked node directory: {path}"
                )
            if path.is_dir() and (path / "node.json").exists():
                names.append(configuration.validate_node_name(path.name))
        return sorted(names)

    def save_node_config(self, config):
        configuration.validate_saved_node_config(config, config.get("name"))
        self.validate_node_directory(config["name"])
        write_json_atomically(self.node_directory(config["name"]) / "node.json", config)

    @contextlib.contextmanager
    def acquire_operation_lock(self, name=None):
        """Order: node lock, then short registry lock. Never the reverse."""
        self.validate_state_root()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        validate_private_path(self.root)
        if name is None:
            path = self.root / ".lock"
        else:
            configuration.validate_node_name(name)
            locks = self.root / "locks"
            locks.mkdir(exist_ok=True, mode=0o700)
            validate_private_path(locks)
            path = locks / (name + ".lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            validate_private_path(path)
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise process.OperationError(
                    f"Another operation holds the {name or 'registry'} lock; retry after it finishes"
                ) from None
            yield


def calculate_directory_fingerprint(directory):
    """Fingerprint file contents/paths; reject links and special files."""
    digest = hashlib.sha256()
    if directory.is_symlink() or not directory.is_dir():
        raise process.OperationError(f"Expected a real directory: {directory}")
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise process.OperationError(
                f"Refusing linked or special source file: {path}"
            )
        digest.update(str(path.relative_to(directory)).encode() + b"\0")
        digest.update(b"d" if path.is_dir() else b"f")
        if path.is_file():
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def restrict_workspace_permissions(directory):
    calculate_directory_fingerprint(
        directory
    )  # Check the whole tree before chmod follows any path.
    os.chmod(directory, 0o700)
    for path in directory.rglob("*"):
        os.chmod(
            path,
            (
                0o700
                if path.is_dir() or (path.suffix == ".sh" and path.name != "env.sh")
                else 0o600
            ),
        )
