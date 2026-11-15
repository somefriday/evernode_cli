"""Interactive preparation and startup of one managed validator node."""

import ipaddress
import re
import sys
import urllib.request

from . import configuration, docker, images, nodes, process, provisioning, ui

# The election scripts keep keys/depool.addr, keys/proxy0.addr and
# keys/proxy1.addr beside a node's own keys/NAME.addr: a node with one of these
# names would have its Safe's address file read as, or overwritten by, theirs.
RESERVED_NAMES = ("depool", "proxy0", "proxy1")


def choose_node_image(store, args):
    """Return (managed record, source ref, selected in menu)."""
    if args.image:
        return images.find_image_record(store, args.image), None, False
    if args.build_new:
        return None, args.node_ref or images.DEFAULT_NODE_REF, False
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise process.OperationError(
            "Choose an image with --image IMAGE or --build-new in noninteractive mode"
        )
    available = images.list_available_managed_images(store)
    repository = args.node_repo or images.DEFAULT_NODE_REPOSITORY
    if any(char.isspace() for char in repository):
        raise process.OperationError("Invalid node repository")
    try:
        refs = images.list_remote_node_refs(repository)
    except process.OperationError:
        refs = {"branches": {}, "tags": []}
        print(
            "Could not list source refs; local images remain available.",
            file=sys.stderr,
        )

    primary = []
    default_branch = next(
        (name for name in ("master", "main") if name in refs["branches"]), None
    )
    if default_branch is None and refs["branches"]:
        default_branch = sorted(refs["branches"])[0]
    if default_branch:
        commit = refs["branches"][default_branch]
        primary.append((f"{default_branch} (latest)  {commit[:7]}", (None, commit)))
    elif not refs["branches"]:
        primary.append(("master (fetch latest source)", (None, "master")))
    primary.extend(
        (f"tag {tag}  {commit[:7]}", (None, commit)) for tag, commit in refs["tags"]
    )
    primary.extend(
        (
            f"local {version}  {record['image']}  "
            f"({record['inputs'].get('node_commit', 'unknown')[:7]})",
            (record, None),
        )
        for record, version in available
    )
    branches = [
        (f"branch {name}  {commit[:7]}", (None, commit))
        for name, commit in sorted(refs["branches"].items())
        if name != default_branch
    ]
    alternate = (
        ("Other branches (Tab: versions and images)", [label for label, _ in branches])
        if branches
        else None
    )
    page, index = ui.select_option(
        f"{repository} — source versions and local images",
        [label for label, _ in primary],
        alternate=alternate,
    )
    record, node_ref = (primary if page == 0 else branches)[index][1]
    return record, node_ref, True


def select_project_source(args):
    """Return the kit a new node takes its scripts from, as (path, repo, ref).

    Without a source flag it is the kit installed with evernode: (None, None,
    None). --project-source names a local kit, --project-repo/--project-ref a
    clone. A kit that is not there is refused here, before any question.
    """
    if args.project_source and (args.project_repo or args.project_ref):
        raise process.OperationError(
            "Use --project-source or --project-repo/--project-ref"
        )
    if args.project_source:
        source = args.project_source.expanduser()
        if not (source / "scripts").is_dir():
            raise process.OperationError(
                f"No election kit at {source}: name a kit directory, or leave out "
                "--project-source for the kit installed with evernode"
            )
        return source, None, None
    if args.project_repo or args.project_ref:
        return (
            None,
            args.project_repo or configuration.DEFAULT_ORCHESTRATION_REPOSITORY,
            args.project_ref,
        )
    configuration.bundled_kit()
    return None, None, None


def handle_node_creation(store, args):
    if args.resume:
        if not args.name:
            raise process.OperationError("Resume requires -n NAME")
        if (
            any(
                getattr(args, field) is not None
                for field in (
                    "image",
                    "ip",
                    "network",
                    "memory",
                    "adnl_port",
                    "metrics_port",
                    "project_source",
                    "project_repo",
                    "project_ref",
                    "node_repo",
                    "node_ref",
                    "cli_repo",
                    "cli_ref",
                    "image_repo",
                )
            )
            or args.build_new
        ):
            raise process.OperationError(
                "Resume uses saved inputs; do not supply configuration/source flags"
            )
        name = configuration.validate_node_name(args.name)
        config = store.load_node_config(name)
        if config["phase"] == "removed":
            raise process.OperationError(
                "Node was removed; use node start to restore its runtime before resuming preparation"
            )
        ui.print_json_result(
            {
                "plan": "resume preparation",
                "node": name,
                "stage": config.get("prepare_stage", config["phase"]),
            }
        )
        if args.dry_run:
            return 0
        with store.acquire_operation_lock(name):
            provisioning.prepare_node_workspace(store, store.load_node_config(name))
        print(f"Prepared {name}. Next: evernode node start -n {name}")
        return 0
    # Before any question: a missing kit is known now, not after an image build.
    kit = select_project_source(args)
    existing = set(store.list_node_names())
    name = configuration.validate_node_name(
        ui.prompt_for_value(args.name, "Validator name")
    )
    if name in RESERVED_NAMES:
        raise process.OperationError(
            f"{name} is a name the election scripts give their own files in the "
            "node's keys/, where its Safe's address file would collide with them; "
            "choose another name"
        )
    if name in existing:
        raise process.OperationError(
            "Node already exists; use --resume for unfinished preparation"
        )
    memory = ui.prompt_for_value(
        args.memory, "Memory ceiling (e.g. 40G; choose from measured requirements)"
    )
    if not re.fullmatch(r"[1-9][0-9]*[MG]", memory):
        raise process.OperationError(
            "Memory must be a positive integer with M or G suffix"
        )
    ip = args.ip
    if ip is None:
        if not sys.stdin.isatty():
            raise process.OperationError("Pass --ip in noninteractive mode")
        try:
            with urllib.request.urlopen("https://api.ipify.org", timeout=5) as response:
                default = str(ipaddress.IPv4Address(response.read(64).decode().strip()))
        except Exception:
            default = None
        ip = ui.prompt_for_value(
            None, "Public IPv4 (confirm NAT/forwarding independently)", default
        )
    try:
        ip = str(ipaddress.IPv4Address(ip))
    except ValueError as exc:
        raise process.OperationError("Invalid IPv4 address") from exc
    for reference in (
        args.image,
        args.project_repo,
        args.project_ref,
        args.node_repo,
        args.node_ref,
        args.cli_repo,
        args.cli_ref,
        args.image_repo,
    ):
        if reference and (
            reference.startswith("-") or any(ord(c) < 32 for c in reference)
        ):
            raise process.OperationError("Invalid repository, ref or image argument")
    if args.node_ref and not args.build_new:
        raise process.OperationError("--node-ref requires --build-new")
    if args.image and any(
        (args.node_repo, args.cli_repo, args.cli_ref, args.image_repo)
    ):
        raise process.OperationError("Source options cannot be used with --image")
    container_name, statsd_name = docker.select_container_names(store, name)
    network = ui.select_choice(
        args.network,
        "network",
        [("main", "main - the Everscale main network"),
         ("devnet", "devnet - the development network")],
        flag="--network",
        default="main",
    )
    config = {
        "name": name,
        "container_name": container_name,
        "statsd": statsd_name,
        "compose_project": "evernode-" + name,
        "network": network,
        "ip": ip,
        "memory": memory,
    }
    config["adnl_port"] = docker.select_available_node_port(
        store, 58888, "udp", args.adnl_port
    )
    config["metrics_port"] = docker.select_available_node_port(
        store, 9102, "tcp", args.metrics_port
    )
    # The validator role (type, Safe, DePool) is set by node setup once the
    # node runs, so building and syncing never wait for it.
    config["setup_stage"] = "preparing"
    if args.dry_run and args.image:
        selected_image, selected_ref, menu_selected = {"image": args.image}, None, False
    else:
        selected_image, selected_ref, menu_selected = choose_node_image(store, args)
    image_plan = (
        selected_image["image"]
        if selected_image
        else {
            "node_repo": args.node_repo or images.DEFAULT_NODE_REPOSITORY,
            "node_ref": selected_ref,
            "cli_repo": args.cli_repo or images.DEFAULT_CLI_REPOSITORY,
            "cli_ref": args.cli_ref or images.DEFAULT_CLI_REF,
            "image_repo": args.image_repo or images.DEFAULT_IMAGE_REPOSITORY,
        }
    )
    ui.print_json_result(
        {
            "plan": "select or build an image, then prepare and start the node",
            "image": image_plan,
            "configuration": config,
        }
    )
    if args.dry_run:
        return 0
    if not menu_selected:
        ui.confirm_operation(args.yes, "Create this node?")
    if selected_image:
        record = images.inspect_recorded_image(selected_image)
    else:
        record, _ = images.build_managed_image(
            store,
            node_repo=args.node_repo or images.DEFAULT_NODE_REPOSITORY,
            node_ref=selected_ref,
            cli_repo=args.cli_repo or images.DEFAULT_CLI_REPOSITORY,
            cli_ref=args.cli_ref or images.DEFAULT_CLI_REF,
            image_repo=args.image_repo or images.DEFAULT_IMAGE_REPOSITORY,
            rebuild=True,
        )
    config["image"] = record["image"]
    config["image_id"] = record["image_id"]
    try:
        with store.acquire_operation_lock(name):
            provisioning.provision_node_workspace(store, config, *kit)
            nodes.start_node(store, config)
    except BaseException:
        # Only a workspace this run reserved is this run's to remove. Provisioning
        # records the phase once it has checked, under the registry lock, that
        # the name was free; a directory that was already there is left alone.
        if "phase" not in config:
            raise
        try:
            nodes.discard_failed_creation(store, config)
        except (OSError, process.OperationError) as cleanup_error:
            print(
                f"Could not fully remove failed setup for {name}: {cleanup_error}",
                file=sys.stderr,
            )
        else:
            print(f"Removed failed setup for {name}.", file=sys.stderr)
        raise
    print(
        f"Started {name}. Wait for sync (evernode node sync -n {name} --wait), "
        f"then give it its validator role: evernode node setup -n {name}"
    )
    return 0
