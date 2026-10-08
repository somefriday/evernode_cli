"""Interactive input, command routing and user-facing results."""

import ipaddress
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

from . import (
    arguments,
    host,
    configuration,
    docker,
    elections,
    images,
    nodes,
    process,
    provisioning,
    storage,
    wallets,
)


def print_json_result(value):
    print(json.dumps(value, indent=2))


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


def prompt_for_value(value, label, default=None):
    if value is not None:
        return value
    if not sys.stdin.isatty():
        if default is not None:
            return default
        raise process.OperationError(
            f"Missing {label}; supply its command flag in noninteractive mode"
        )
    answer = input(label + (f" [{default}]" if default else "") + ": ").strip()
    return answer or default or prompt_for_value(None, label)


def confirm_operation(yes, text):
    if yes:
        return
    if not sys.stdin.isatty() or input(text + " [y/N]: ").strip().lower() not in (
        "y",
        "yes",
    ):
        raise process.OperationError(
            "Cancelled; no action taken (use --yes for explicit noninteractive approval)"
        )


def prompt_integer(value, label, default):
    raw = prompt_for_value(None if value is None else str(value), label, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise process.OperationError(f"{label} must be an integer") from exc


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
                    "custodians",
                    "required_signatures",
                    "wallet_address",
                    "depool_address",
                )
            )
            or args.import_wallet
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
        print_json_result(
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
    existing = set(store.list_node_names())
    name = configuration.validate_node_name(
        prompt_for_value(args.name, "Validator name")
    )
    if name in existing:
        raise process.OperationError(
            "Node already exists; use --resume for unfinished preparation"
        )
    memory = prompt_for_value(
        args.memory, "Memory ceiling (e.g. 40G; choose from measured requirements)"
    )
    import re

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
        ip = prompt_for_value(
            None, "Public IPv4 (confirm NAT/forwarding independently)", default
        )
    try:
        ip = str(ipaddress.IPv4Address(ip))
    except ValueError as exc:
        raise process.OperationError("Invalid IPv4 address") from exc
    if args.project_source and (args.project_repo or args.project_ref):
        raise process.OperationError(
            "Use --project-source or --project-repo/--project-ref"
        )
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
    container_name, statsd_name = docker.select_container_names(store)
    importing = args.import_wallet
    if not importing and sys.stdin.isatty():
        importing = input(
            "Recover an existing Safe wallet from seed phrases? [y/N]: "
        ).strip().lower() in ("y", "yes")
    if importing and any(
        value is not None
        for value in (
            args.validator_assurance,
            args.min_stake,
            args.reward_fraction,
            args.balance_threshold,
        )
    ):
        raise process.OperationError(
            "DePool deployment parameters do not apply to an imported DePool"
        )
    network = prompt_for_value(args.network, "Network", "main")
    workchain = prompt_integer(args.workchain, "Wallet workchain", 0)
    cron_interval = prompt_integer(
        args.cron_interval, "Election check interval in minutes", 10
    )
    depool_type = prompt_for_value(args.depool_type, "DePool type", "EverX")
    if importing:
        # Existing DePool terms are on-chain contract state. Keep the internal
        # environment schema complete without pretending these defaults alter
        # or describe the imported contract.
        assurance = configuration.DEFAULT_ENVIRONMENT["ValidatorAssuranceT"]
        min_stake = configuration.DEFAULT_ENVIRONMENT["MinStakeT"]
        reward_fraction = configuration.DEFAULT_ENVIRONMENT["ParticipantRewardFraction"]
        balance_threshold = configuration.DEFAULT_ENVIRONMENT["BalanceThresholdT"]
    else:
        assurance = prompt_integer(
            args.validator_assurance, "Validator assurance", 50000
        )
        min_stake = prompt_integer(args.min_stake, "Minimum DePool stake", 10)
        reward_fraction = prompt_integer(
            args.reward_fraction, "Participant reward fraction", 65
        )
        balance_threshold = prompt_integer(
            args.balance_threshold, "DePool balance threshold", 20
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
    if (
        network not in ("main", "devnet")
        or depool_type not in ("EverX", "StEver")
        or not 1 <= cron_interval <= 59
        or workchain not in (-1, 0)
        or any(value < 0 for value in (assurance, min_stake, balance_threshold))
        or not 0 <= reward_fraction <= 100
    ):
        raise process.OperationError(
            "Invalid workchain, cron interval or DePool numeric setting"
        )
    config["settings"] = {
        "NODE_WC": workchain,
        "CRONTAB_INTERVAL": cron_interval,
        "DePool_TYPE": depool_type,
        "ValidatorAssuranceT": assurance,
        "MinStakeT": min_stake,
        "ParticipantRewardFraction": reward_fraction,
        "BalanceThresholdT": balance_threshold,
    }
    config["adnl_port"] = docker.select_available_node_port(
        store, 58888, "udp", args.adnl_port
    )
    config["metrics_port"] = docker.select_available_node_port(
        store, 9102, "tcp", args.metrics_port
    )
    custodians = (
        args.custodians
        if args.custodians is not None
        else int(prompt_for_value(None, "Multisig custodians", "3"))
    )
    required = (
        args.required_signatures
        if args.required_signatures is not None
        else int(prompt_for_value(None, "Required signatures", "2"))
    )
    wallet_address = args.wallet_address
    depool_address = args.depool_address
    if importing:
        wallet_address = prompt_for_value(wallet_address, "Existing wallet address")
        if depool_address is None and sys.stdin.isatty():
            depool_address = (
                input("Existing DePool address (optional): ").strip() or None
            )
    config["wallet"] = wallets.validate_wallet_profile(
        {
            "mode": "import" if importing else "new",
            "custodians": custodians,
            "required_signatures": required,
            "wallet_address": wallet_address,
            "depool_address": depool_address,
        }
    )
    wallets.ensure_unassigned(store, config, wallet_address, depool_address)
    config["setup_stage"] = "preparing"
    if importing and not sys.stdin.isatty():
        raise process.OperationError(
            "Seed import requires an interactive terminal; seed phrases are never accepted as command arguments"
        )
    image_plan = args.image or {
        "node_repo": args.node_repo or images.DEFAULT_NODE_REPOSITORY,
        "node_ref": args.node_ref or images.DEFAULT_NODE_REF,
        "cli_repo": args.cli_repo or images.DEFAULT_CLI_REPOSITORY,
        "cli_ref": args.cli_ref or images.DEFAULT_CLI_REF,
        "image_repo": args.image_repo or images.DEFAULT_IMAGE_REPOSITORY,
    }
    print_json_result(
        {
            "plan": "build/select image and prepare node; no startup, wallet creation or elections",
            "image": image_plan,
            "configuration": config,
        }
    )
    if args.dry_run:
        return 0
    confirm_operation(args.yes, "Build/select image and prepare this node?")
    if args.image:
        record = images.find_image_record(store, args.image)
        process.execute_command(["docker", "image", "inspect", record["image_id"]])
    else:
        record, _ = images.build_managed_image(
            store,
            node_repo=args.node_repo or images.DEFAULT_NODE_REPOSITORY,
            node_ref=args.node_ref or images.DEFAULT_NODE_REF,
            cli_repo=args.cli_repo or images.DEFAULT_CLI_REPOSITORY,
            cli_ref=args.cli_ref or images.DEFAULT_CLI_REF,
            image_repo=args.image_repo or images.DEFAULT_IMAGE_REPOSITORY,
        )
    config["image"] = record["image"]
    config["image_id"] = record["image_id"]
    with store.acquire_operation_lock(name):
        provisioning.provision_node_workspace(
            store,
            config,
            args.project_source,
            args.project_repo or configuration.DEFAULT_ORCHESTRATION_REPOSITORY,
            args.project_ref,
        )
        if importing:
            phrases = [
                getpass.getpass(f"Seed phrase {index}/{custodians}: ")
                for index in range(1, custodians + 1)
            ]
            wallets.store_import_seeds(store, config, phrases)
        nodes.start_node(store, config)
        config = store.load_node_config(name)
        config["setup_stage"] = "node-started"
        store.save_node_config(config)
    next_action = "wallet recover" if importing else "wallet create"
    print(f"Started {name}. Wait for sync, then run: evernode {next_action} -n {name}")
    return 0


def _require_synchronized_setup_node(store, name):
    config = store.load_node_config(name)
    statistics = nodes.fetch_node_statistics(config)
    if not nodes.is_node_synchronized(statistics):
        raise process.OperationError(
            "Node is not synchronized; use evernode node sync -n " + name + " --wait"
        )
    if "wallet" not in config:
        raise process.OperationError(
            "This legacy node has no wallet setup profile; create a new managed node"
        )
    return config


def _print_new_secrets(label, address, phrases):
    print(f"\nSave these {label} seed phrases offline before continuing.")
    for index, phrase in enumerate(phrases, 1):
        print(f"{label} seed phrase {index}: {phrase}")
    print(f"{label} address: {address}\n")


def _acknowledge_secret_backup():
    if input("Type SAVED after storing every phrase offline: ").strip() != "SAVED":
        raise process.OperationError(
            "Seed phrases were generated but setup stage was not advanced; rerun the same command to display them again"
        )


def handle_setup_action(store, args):
    name = configuration.validate_node_name(args.name)
    action = (args.group, args.action)
    mutating = action not in (("wallet", "verify"), ("depool", "verify"))
    secret_action = action in (("wallet", "create"), ("depool", "prepare"))
    if secret_action and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise process.OperationError(
            "Wallet and DePool seed generation requires an interactive terminal; phrases are never printed to redirected output"
        )
    with store.acquire_operation_lock(name):
        config = _require_synchronized_setup_node(store, name)
        if mutating:
            confirm_operation(args.yes, f"Run {args.group} {args.action} for {name}?")
        if action == ("wallet", "create"):
            result_config, result, phrases = wallets.wallet_create(store, config)
            _print_new_secrets("Safe wallet", result["wallet"]["address"], phrases)
            _acknowledge_secret_backup()
        elif action == ("wallet", "deploy"):
            result_config, result = wallets.wallet_deploy(store, config)
        elif action == ("wallet", "recover"):
            result_config, result = wallets.wallet_recover(store, config)
        elif action == ("wallet", "verify"):
            result_config, result = wallets.wallet_verify(store, config)
        elif action == ("depool", "prepare"):
            result_config, result, phrase = wallets.depool_prepare(store, config)
            _print_new_secrets("DePool", result["depool_address"], [phrase])
            _acknowledge_secret_backup()
        elif action == ("depool", "deploy"):
            result_config, result = wallets.depool_deploy(store, config)
        elif action == ("depool", "stake-initial"):
            result_config, result = wallets.depool_stake_initial(store, config)
        elif action == ("depool", "verify"):
            result_config, result = wallets.depool_verify(store, config)
        else:
            raise process.OperationError("Unsupported wallet or DePool action")
        store.save_node_config(result_config)
        print_json_result(result)
    return 0


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
    print_json_result({"plan": "build managed node image", "settings": settings})
    if args.dry_run:
        return 0
    confirm_operation(args.yes, "Build this Docker image?")
    record, built = images.build_managed_image(store, **settings)
    print_json_result({"image": record, "built": built})
    return 0


def handle_image_removal(store, args):
    if args.image.startswith("-"):
        raise process.OperationError("Invalid image reference")
    print_json_result({"remove_image": args.image})
    if not args.dry_run:
        confirm_operation(args.yes, "Remove this Docker image?")
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
        confirm_operation(
            args.yes,
            "Remove selected containers"
            + (" and delete database/logs" if args.purge_data else "")
            + "? Keys/configuration remain; duties are not cancelled.",
        )
    failed = False
    for name in names:
        print_json_result(
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
            print_json_result({"node": name, "result": "success"})
        except (process.OperationError, OSError, ValueError) as exc:
            failed = True
            print_json_result({"node": name, "result": "failed", "error": str(exc)})
    return 1 if failed else 0


def handle_node_local_sync(store, args):
    source = configuration.validate_node_name(args.source)
    target = configuration.validate_node_name(args.target)
    plan = nodes.inspect_local_sync(store, source, target)
    print_json_result(plan)
    if args.dry_run:
        return 0
    confirm_operation(
        args.yes, "Stop both nodes and seed the target database from the source?"
    )
    print_json_result(nodes.local_sync(store, source, target))
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
                print_json_result({"plan": host.get_setup_plan()})
                if args.dry_run:
                    return 0
                confirm_operation(args.yes, "Install host dependencies?")
                print_json_result(host.install_host_dependencies())
                return 0
            checks = host.collect_host_dependency_report()
            print_json_result(checks)
            return 0 if host.host_dependencies_available(checks) else 1
        require_root_privileges()
        os.umask(0o077)
        store = storage.NodeStateStore(args.state_dir)
        if args.group == "node" and args.action == "create":
            return handle_node_creation(store, args)
        if args.group in ("wallet", "depool"):
            return handle_setup_action(store, args)
        if args.group == "node" and args.action == "lsync":
            return handle_node_local_sync(store, args)
        if args.group == "node" and args.action == "update":
            name = configuration.validate_node_name(args.name)
            record = (
                None if args.rollback else images.find_image_record(store, args.image)
            )
            print_json_result(
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
            confirm_operation(args.yes, "Perform image maintenance on this node?")
            nodes.update_node_image(
                store, name, record, rollback=args.rollback, timeout=args.timeout
            )
            print_json_result(nodes.get_node_status(store.load_node_config(name)))
            return 0
        if args.group == "image":
            if args.action == "list":
                print_json_result(images.list_node_images(store))
                return 0
            if args.action in ("build", "rebuild"):
                return handle_image_build(store, args)
            return handle_image_removal(store, args)
        if args.group == "election":
            if args.action == "list":
                print_json_result(
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
                print_json_result(
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
                    print_json_result({"enable_managed_schedule": name})
                else:
                    confirm_operation(
                        args.yes, "Install this node's election schedule?"
                    )
                    with store.acquire_operation_lock(name):
                        elections.enable_node_election_schedule(store, config)
                        print_json_result(
                            {
                                "name": name,
                                **elections.election_schedule_status(store, config),
                            }
                        )
            elif args.dry_run:
                print_json_result(
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
            print_json_result(
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
                print_json_result(nodes.get_node_status(config))
                directory = store.node_directory(name)
                print_json_result(
                    {
                        "disk_free_bytes": shutil.disk_usage(directory).free,
                        "cron_file_exists": elections.node_election_cron_path(
                            name
                        ).exists(),
                        "hint": "Use logs --component statsd for exporter failures and --component stderr for node failures.",
                    }
                )
            elif args.action == "config":
                print_json_result(config)
            elif args.action == "status":
                print_json_result(nodes.get_node_status(config))
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
                        print_json_result(
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
                    print_json_result(value)
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
