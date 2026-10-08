"""Command routing for evernode."""

import os
import shutil
import subprocess
import sys
import time

from . import (
    arguments,
    configuration,
    creation,
    docker,
    elections,
    host,
    images,
    nodes,
    process,
    setup_actions,
    storage,
    ui,
)


def require_root_privileges():
    if os.geteuid() != 0:
        raise process.OperationError("Use sudo evernode for host/node management")


def select_node_names(store, args):
    if getattr(args, "all", False):
        if getattr(args, "name", None):
            raise process.OperationError("Use -n or --all, not both")
        return store.list_node_names()
    if getattr(args, "name", None):
        return [configuration.validate_node_name(args.name)]
    names = store.list_node_names()
    if len(names) != 1:
        raise process.OperationError(
            "Select a node with -n NAME"
            if names
            else "No managed nodes. Use evernode node create"
        )
    return names


def handle_image_build(store, args):
    if args.action == "rebuild":
        previous = images.find_image_record(store, args.image)
        inputs = previous["inputs"]
        settings = {
            "node_repo": inputs["node_repo"],
            "node_ref": inputs["node_commit"],
            "cli_repo": inputs["cli_repo"],
            "cli_ref": inputs["cli_commit"],
            "rust_version": inputs["rust_version"],
            "node_features": inputs["node_features"],
            "image_repo": previous["image"].rsplit(":", 1)[0],
            "rebuild": True,
        }
    else:
        settings = {
            "node_repo": args.node_repo or images.DEFAULT_NODE_REPOSITORY,
            "node_ref": args.node_ref or images.DEFAULT_NODE_REF,
            "cli_repo": args.cli_repo or images.DEFAULT_CLI_REPOSITORY,
            "cli_ref": args.cli_ref or images.DEFAULT_CLI_REF,
            "image_repo": args.image_repo or images.DEFAULT_IMAGE_REPOSITORY,
            "rust_version": args.rust_version or images.DEFAULT_RUST_VERSION,
            "rebuild": False,
        }
    ui.print_json_result({"plan": "build managed node image", "settings": settings})
    if args.dry_run:
        return 0
    ui.confirm_operation(args.yes, "Build this Docker image?")
    record, built = images.build_managed_image(store, **settings)
    ui.print_json_result({"image": record, "built": built})
    return 0


def handle_image_removal(store, args):
    if args.image.startswith("-"):
        raise process.OperationError("Invalid image reference")
    ui.print_json_result({"remove_image": args.image})
    if not args.dry_run:
        ui.confirm_operation(args.yes, "Remove this Docker image?")
    images.remove_managed_image(store, args.image, dry_run=args.dry_run)
    return 0


def handle_node_lifecycle(store, args):
    names = select_node_names(store, args)
    if getattr(args, "timeout", 1) < 1:
        raise process.OperationError("Timeout must be positive")
    # Resolve/validate metadata for all targets before beginning a batch.
    for name in names:
        store.load_node_config(name)
    if args.action == "remove" and not args.dry_run:
        ui.confirm_operation(
            args.yes,
            "Remove selected containers"
            + (" and delete database/logs" if args.purge_data else "")
            + "? Keys/configuration remain; duties are not cancelled.",
        )
    failed = False
    for name in names:
        ui.print_json_result(
            {
                "action": args.action,
                "node": name,
                "timeout": getattr(args, "timeout", None),
                "purge_data": getattr(args, "purge_data", False),
            }
        )
        if args.dry_run:
            continue
        try:
            nodes.apply_node_lifecycle_operation(
                store,
                name,
                args.action,
                timeout=getattr(args, "timeout", 30),
                purge_data=getattr(args, "purge_data", False),
            )
            ui.print_json_result({"node": name, "result": "success"})
        except (process.OperationError, OSError, ValueError) as exc:
            failed = True
            ui.print_json_result({"node": name, "result": "failed", "error": str(exc)})
    return 1 if failed else 0


def handle_node_local_sync(store, args):
    source = configuration.validate_node_name(args.source)
    target = configuration.validate_node_name(args.target)
    plan = nodes.inspect_local_sync(store, source, target)
    ui.print_json_result(plan)
    if args.dry_run:
        return 0
    ui.confirm_operation(
        args.yes, "Stop both nodes and seed the target database from the source?"
    )
    ui.print_json_result(nodes.local_sync(store, source, target))
    print(
        f"Database seeded. Wait for sync before continuing: evernode node sync -n {target} --wait"
    )
    return 0


def main(argv=None):
    p = arguments.build_argument_parser()
    args = p.parse_args(argv)
    if args.group is None:
        p.print_help()
        if os.geteuid() == 0:
            print(
                "Managed nodes:",
                ", ".join(storage.NodeStateStore(args.state_dir).list_node_names())
                or "none",
            )
        return 0
    try:
        if args.group == "host":
            if args.action == "setup":
                require_root_privileges()
                ui.print_json_result({"plan": host.get_setup_plan()})
                if args.dry_run:
                    return 0
                ui.confirm_operation(args.yes, "Install host dependencies?")
                ui.print_json_result(host.install_host_dependencies())
                return 0
            checks = host.collect_host_dependency_report()
            ui.print_json_result(checks)
            return 0 if host.host_dependencies_available(checks) else 1
        require_root_privileges()
        os.umask(0o077)
        store = storage.NodeStateStore(args.state_dir)
        if args.group == "node" and args.action == "create":
            return creation.handle_node_creation(store, args)
        if args.group in ("wallet", "depool"):
            return setup_actions.handle_setup_action(store, args)
        if args.group == "node" and args.action == "lsync":
            return handle_node_local_sync(store, args)
        if args.group == "node" and args.action == "update":
            name = configuration.validate_node_name(args.name)
            record = (
                None if args.rollback else images.find_image_record(store, args.image)
            )
            ui.print_json_result(
                {
                    "plan": (
                        "rollback node image" if args.rollback else "update node image"
                    ),
                    "node": name,
                    "image": record["image"] if record else None,
                }
            )
            if args.dry_run:
                return 0
            ui.confirm_operation(args.yes, "Perform image maintenance on this node?")
            nodes.update_node_image(
                store, name, record, rollback=args.rollback, timeout=args.timeout
            )
            ui.print_json_result(nodes.get_node_status(store.load_node_config(name)))
            return 0
        if args.group == "image":
            if args.action == "list":
                ui.print_json_result(images.list_node_images(store))
                return 0
            if args.action in ("build", "rebuild"):
                return handle_image_build(store, args)
            return handle_image_removal(store, args)
        if args.group == "election":
            if args.action == "list":
                ui.print_json_result(
                    [
                        {
                            "name": n,
                            **elections.election_schedule_status(
                                store, store.load_node_config(n)
                            ),
                        }
                        for n in store.list_node_names()
                    ]
                )
            elif args.action == "status":
                config = store.load_node_config(
                    configuration.validate_node_name(args.name)
                )
                ui.print_json_result(
                    {
                        "name": config["name"],
                        **elections.election_schedule_status(store, config),
                    }
                )
            elif args.action == "start":
                name = configuration.validate_node_name(args.name)
                config = store.load_node_config(name)
                if config.get("setup_stage") not in ("imported", "ready-for-elections"):
                    raise process.OperationError(
                        "Complete wallet/DePool setup before enabling elections"
                    )
                if not nodes.is_node_synchronized(nodes.fetch_node_statistics(config)):
                    raise process.OperationError(
                        "Node is not synchronized; election schedule was not enabled"
                    )
                if args.dry_run:
                    ui.print_json_result({"enable_managed_schedule": name})
                else:
                    ui.confirm_operation(
                        args.yes, "Install this node's election schedule?"
                    )
                    with store.acquire_operation_lock(name):
                        elections.enable_node_election_schedule(store, config)
                        ui.print_json_result(
                            {
                                "name": name,
                                **elections.election_schedule_status(store, config),
                            }
                        )
            elif args.dry_run:
                ui.print_json_result(
                    {"disable_managed_schedules": select_node_names(store, args)}
                )
            else:
                for n in select_node_names(store, args):
                    with store.acquire_operation_lock(n):
                        store.load_node_config(n)
                        elections.disable_node_election_schedule(n)
                        print(
                            f"{n}: managed schedule disabled; existing on-chain duties remain. External jobs are not controlled."
                        )
            return 0
        if args.group == "node" and args.action == "list":
            ui.print_json_result(
                [
                    nodes.get_node_status(store.load_node_config(n))
                    for n in store.list_node_names()
                ]
            )
            return 0
        if args.group == "node" and args.action in (
            "start",
            "stop",
            "restart",
            "remove",
        ):
            return handle_node_lifecycle(store, args)
        names = select_node_names(store, args)
        if (
            getattr(args, "timeout", 1) < 1
            or getattr(args, "interval", 1) < 1
            or getattr(args, "tail", 1) < 0
        ):
            raise process.OperationError(
                "Timeout/interval must be positive; tail must be nonnegative"
            )
        for name in names:
            config = store.load_node_config(name)
            if args.group == "doctor":
                ui.print_json_result(nodes.get_node_status(config))
                directory = store.node_directory(name)
                ui.print_json_result(
                    {
                        "disk_free_bytes": shutil.disk_usage(directory).free,
                        "cron_file_exists": elections.node_election_cron_path(
                            name
                        ).exists(),
                        "hint": "Use logs --component statsd for exporter failures and --component stderr for node failures.",
                    }
                )
            elif args.action == "config":
                ui.print_json_result(config)
            elif args.action == "status":
                ui.print_json_result(nodes.get_node_status(config))
            elif args.action == "resources":
                for container in (
                    configuration.node_container_name(config),
                    config["statsd"],
                ):
                    obj = docker.inspect_managed_container(container, name)
                    if obj:
                        print(
                            process.execute_command(
                                ["docker", "stats", "--no-stream", container]
                            ).stdout,
                            end="",
                        )
                        ui.print_json_result(
                            {
                                "container": container,
                                "memory_limit_bytes": obj["HostConfig"]["Memory"],
                                "oom_killed": obj["State"]["OOMKilled"],
                            }
                        )
            elif args.action == "sync":
                previous = False
                while True:
                    value = nodes.fetch_node_statistics(config)
                    ui.print_json_result(value)
                    if not args.wait:
                        return 0 if nodes.is_node_synchronized(value) else 2
                    current = nodes.is_node_synchronized(value)
                    if current and previous:
                        return 0
                    previous = current
                    time.sleep(args.interval)
            elif args.action == "logs":
                command = nodes.build_node_log_command(
                    store, config, args.component, tail=args.tail, follow=args.follow
                )
                return subprocess.call(command)
        return 0
    except (process.OperationError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "Interrupted. Completed changes remain; inspect node status before retrying. Interrupting logs/sync does not stop the node.",
            file=sys.stderr,
        )
        return 130
