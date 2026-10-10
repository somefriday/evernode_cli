"""Build, record and safely select immutable ever-node Docker images."""

import hashlib
import json
import re
import secrets
import shutil
import tempfile
from pathlib import Path

from . import process, storage

DEFAULT_NODE_REPOSITORY = "https://github.com/everx-labs/ever-node.git"
DEFAULT_NODE_REF = "master"
DEFAULT_CLI_REPOSITORY = "https://github.com/everx-labs/ever-cli.git"
DEFAULT_CLI_REF = "0.44.0"
DEFAULT_IMAGE_REPOSITORY = "local/ever-node"
DEFAULT_RUST_VERSION = "1.90.0"
DEFAULT_NODE_FEATURES = "statsd"
VERSION_TAG = re.compile(r"^[vV]?(\d+(?:\.\d+){1,3})(?:([-+].*))?$")


def _safe_reference(value, label):
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("-")
        or any(c.isspace() or ord(c) < 32 for c in value)
    ):
        raise process.OperationError(f"Invalid {label}")
    return value


def _version_tag_key(tag):
    match = VERSION_TAG.fullmatch(tag)
    if not match:
        return (False, (), False, tag.lower())
    parts = tuple(int(part) for part in match.group(1).split("."))
    return (True, parts + (0,) * (4 - len(parts)), not match.group(2), tag.lower())


def list_remote_node_refs(repository):
    """List source commits without cloning; annotated tags use peeled commits."""
    _safe_reference(repository, "node repository")
    result = process.execute_command(
        ["git", "ls-remote", "--heads", "--tags", repository], timeout=30
    )
    branches = {}
    tags = {}
    peeled_tags = {}
    for line in result.stdout.splitlines():
        try:
            commit, name = line.split("\t", 1)
        except ValueError:
            continue
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", commit):
            continue
        if name.startswith("refs/heads/"):
            branches[name.removeprefix("refs/heads/")] = commit
        elif name.startswith("refs/tags/"):
            tag = name.removeprefix("refs/tags/")
            if tag.endswith("^{}"):
                peeled_tags[tag[:-3]] = commit
            else:
                tags[tag] = commit
    tags.update(peeled_tags)
    ordered_tags = sorted(
        tags.items(), key=lambda pair: _version_tag_key(pair[0]), reverse=True
    )
    return {
        "branches": branches,
        "tags": ordered_tags,
    }


def _recipe_path():
    packaged = Path(__file__).resolve().parent / "assets" / "ever-node.Dockerfile"
    checkout = (
        Path(__file__).resolve().parents[1] / "docker" / "ever-node" / "Dockerfile"
    )
    for path in (packaged, checkout):
        if path.is_file():
            return path
    raise process.OperationError("Installed evernode Dockerfile is missing")


def _recipe_fingerprint():
    return hashlib.sha256(_recipe_path().read_bytes()).hexdigest()


def _git_checkout(repository, ref, destination):
    _safe_reference(repository, "repository")
    _safe_reference(ref, "revision")
    # ever-node currently keeps common build scripts in a Git submodule. A
    # non-recursive clone fails later when Cargo compiles catchain. Repositories
    # without submodules are unaffected by recursive initialization.
    process.execute_command(
        [
            "git",
            "clone",
            "--quiet",
            "--recurse-submodules",
            "--",
            repository,
            destination,
        ],
        timeout=900,
    )
    process.execute_command(
        ["git", "-C", destination, "checkout", "--quiet", "--detach", ref], timeout=180
    )
    process.execute_command(
        ["git", "-C", destination, "submodule", "sync", "--recursive"], timeout=120
    )
    process.execute_command(
        ["git", "-C", destination, "submodule", "update", "--init", "--recursive"],
        timeout=900,
    )
    return process.execute_command(
        ["git", "-C", destination, "rev-parse", "HEAD"], timeout=30
    ).stdout.strip()


def _record_key(inputs):
    return hashlib.sha256(
        json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _image_tag(repository, key):
    _safe_reference(repository, "image repository")
    return repository + ":" + key[:16]


def _load_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise process.OperationError(f"Invalid image record: {path}") from exc


def _validate_record(record):
    required = {"schema_version", "key", "inputs", "image", "image_id"}
    if (
        not isinstance(record, dict)
        or set(record) != required
        or record["schema_version"] != 1
    ):
        raise process.OperationError("Invalid image record")
    if not isinstance(record["key"], str) or len(record["key"]) != 64:
        raise process.OperationError("Invalid image record key")
    if _record_key(record["inputs"]) != record["key"]:
        raise process.OperationError(
            "Image record fingerprint does not match its inputs"
        )
    _safe_reference(record["image"], "recorded image")
    if not isinstance(record["image_id"], str) or not record["image_id"].startswith(
        "sha256:"
    ):
        raise process.OperationError("Invalid recorded image ID")
    return record


def image_record_path(store, key):
    if (
        not isinstance(key, str)
        or len(key) != 64
        or any(c not in "0123456789abcdef" for c in key)
    ):
        raise process.OperationError("Invalid image record key")
    return store.images_directory() / (key + ".json")


def load_image_record(store, key):
    path = image_record_path(store, key)
    storage.validate_private_path(path)
    return _validate_record(_load_json(path))


def list_managed_image_records(store):
    directory = store.images_directory(create=False)
    if not directory.exists():
        return []
    storage.validate_private_path(directory)
    records = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink():
            raise process.OperationError("Refusing symlinked image record")
        storage.validate_private_path(path)
        records.append(_validate_record(_load_json(path)))
    return records


def find_image_record(store, image):
    _safe_reference(image, "image")
    matches = [
        r
        for r in list_managed_image_records(store)
        if r["image"] == image or r["image_id"] == image or r["key"].startswith(image)
    ]
    if len(matches) != 1:
        raise process.OperationError("Image is not a unique managed evernode image")
    return matches[0]


def inspect_recorded_image(record):
    """Require the recorded tag to still name the recorded immutable image."""
    result = process.execute_command(["docker", "image", "inspect", record["image"]])
    try:
        image_id = json.loads(result.stdout)[0]["Id"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise process.OperationError("Docker returned invalid image metadata") from exc
    if image_id != record["image_id"]:
        raise process.OperationError("Image tag no longer points to its managed image")
    return record


def list_available_managed_images(store):
    """Show only managed images whose recorded tag is still present."""
    process.execute_command(
        ["docker", "info", "--format", "{{.ServerVersion}}"], timeout=10
    )
    available = []
    for record in list_managed_image_records(store):
        try:
            inspect_recorded_image(record)
        except process.OperationError:
            continue
        try:
            result = process.execute_command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--read-only",
                    "--entrypoint",
                    "/usr/local/bin/ever-node",
                    record["image_id"],
                    "--help",
                ],
                timeout=30,
                check=False,
            )
            match = re.search(
                r"EVER Node, version\s+(\S+)", result.stdout + result.stderr
            )
        except process.OperationError:
            match = None
        available.append((record, match.group(1) if match else "unknown"))
    available.sort(key=lambda item: _version_tag_key(item[1]), reverse=True)
    return available


def validate_node_image(image):
    obj = json.loads(
        process.execute_command(["docker", "image", "inspect", image]).stdout
    )[0]
    image_id = obj["Id"]
    process.execute_command(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/bash",
            image_id,
            "-ec",
            "for c in ever-node console keygen ever-cli jq yq bc flock curl getent; do command -v $c; done",
        ],
        timeout=120,
    )
    return image_id


def build_managed_image(
    store,
    *,
    node_repo=DEFAULT_NODE_REPOSITORY,
    node_ref=DEFAULT_NODE_REF,
    cli_repo=DEFAULT_CLI_REPOSITORY,
    cli_ref=DEFAULT_CLI_REF,
    image_repo=DEFAULT_IMAGE_REPOSITORY,
    rust_version=DEFAULT_RUST_VERSION,
    node_features=DEFAULT_NODE_FEATURES,
    rebuild=False,
):
    """Resolve sources, reuse an exact verified record, or build and register it."""
    for value, label in (
        (node_repo, "node repository"),
        (node_ref, "node revision"),
        (cli_repo, "CLI repository"),
        (cli_ref, "CLI revision"),
        (rust_version, "Rust version"),
        (node_features, "node features"),
    ):
        _safe_reference(value, label)
    with tempfile.TemporaryDirectory(prefix="evernode-build-") as temporary:
        temporary = Path(temporary)
        sources = temporary / "src"
        sources.mkdir(mode=0o700)
        node_commit = _git_checkout(node_repo, node_ref, sources / "ever-node")
        cli_commit = _git_checkout(cli_repo, cli_ref, sources / "ever-cli")
        inputs = {
            "node_repo": node_repo,
            "node_commit": node_commit,
            "cli_repo": cli_repo,
            "cli_commit": cli_commit,
            "rust_version": rust_version,
            "node_features": node_features,
            "recipe_sha256": _recipe_fingerprint(),
        }
        if rebuild:
            # A forced rebuild is deliberately a new immutable artifact even if
            # source references resolve to the same commits.
            inputs["rebuild_nonce"] = secrets.token_hex(8)
        key = _record_key(inputs)
        tag = _image_tag(image_repo, key)
        with store.acquire_operation_lock():
            path = image_record_path(store, key)
            if path.exists() and not rebuild:
                record = load_image_record(store, key)
                process.execute_command(
                    ["docker", "image", "inspect", record["image_id"]]
                )
                return record, False
        context = temporary / "context"
        context.mkdir(mode=0o700)
        shutil.copy2(_recipe_path(), context / "Dockerfile")
        ignore = _recipe_path().with_name("ever-node.dockerignore")
        if not ignore.exists():
            ignore = _recipe_path().parent / ".dockerignore"
        if ignore.exists():
            shutil.copy2(ignore, context / ".dockerignore")
        shutil.copytree(sources, context / "src")
        process.execute_command(
            [
                "docker",
                "build",
                "--progress=plain",
                "--pull",
                "--no-cache",
                "--tag",
                tag,
                "--build-arg",
                "RUST_VERSION=" + rust_version,
                "--build-arg",
                "NODE_BUILD_FEATURES=" + node_features,
                str(context),
            ],
            timeout=7200,
            stream=True,
        )
        image_id = validate_node_image(tag)
        record = {
            "schema_version": 1,
            "key": key,
            "inputs": inputs,
            "image": tag,
            "image_id": image_id,
        }
        with store.acquire_operation_lock():
            destination = image_record_path(store, key)
            if destination.exists() and not rebuild:
                return load_image_record(store, key), False
            store.images_directory(create=True)
            storage.write_json_atomically(destination, record)
        return record, True


def remove_managed_image(store, image_reference, *, dry_run=False):
    """Recheck references under the registry lock before removing a pinned image."""
    if image_reference.startswith("-"):
        raise process.OperationError("Invalid image reference")
    with store.acquire_operation_lock():
        record = find_image_record(store, image_reference)
        nodes = [store.load_node_config(n) for n in store.list_node_names()]
        users = [
            c["name"]
            for c in nodes
            if c["image_id"] == record["image_id"] and c["phase"] != "removed"
        ]
        ids = process.execute_command(["docker", "ps", "-aq"]).stdout.split()
        if ids:
            users += [
                o["Name"]
                for o in json.loads(
                    process.execute_command(["docker", "inspect", *ids]).stdout
                )
                if o["Image"] == record["image_id"]
            ]
        if users:
            raise process.OperationError("Image is referenced by: " + ", ".join(users))
        if not dry_run:
            process.execute_command(["docker", "image", "rm", record["image_id"]])
            image_record_path(store, record["key"]).unlink()


def list_node_images(store):
    return {
        "managed_images": list_managed_image_records(store),
        "nodes": [
            {
                "name": name,
                "image": store.load_node_config(name).get("image"),
                "image_id": store.load_node_config(name).get("image_id"),
            }
            for name in store.list_node_names()
        ],
    }
