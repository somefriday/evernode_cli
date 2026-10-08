"""Interactive preparation and startup of one managed validator node."""

import getpass
import ipaddress
import re
import sys
import urllib.request

from . import configuration, docker, images, nodes, process, provisioning, ui, wallets


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
    existing = set(store.list_node_names())
    name = configuration.validate_node_name(
        ui.prompt_for_value(args.name, "Validator name")
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
    network = ui.prompt_for_value(args.network, "Network", "main")
    workchain = ui.prompt_integer(args.workchain, "Wallet workchain", 0)
    cron_interval = ui.prompt_integer(
        args.cron_interval, "Election check interval in minutes", 10
    )
    depool_type = ui.prompt_for_value(args.depool_type, "DePool type", "EverX")
    if importing:
        # Existing DePool terms are on-chain contract state. Keep the internal
        # environment schema complete without pretending these defaults alter
        # or describe the imported contract.
        assurance = configuration.DEFAULT_ENVIRONMENT["ValidatorAssuranceT"]
        min_stake = configuration.DEFAULT_ENVIRONMENT["MinStakeT"]
        reward_fraction = configuration.DEFAULT_ENVIRONMENT["ParticipantRewardFraction"]
        balance_threshold = configuration.DEFAULT_ENVIRONMENT["BalanceThresholdT"]
    else:
        assurance = ui.prompt_integer(
            args.validator_assurance, "Validator assurance", 50000
        )
        min_stake = ui.prompt_integer(args.min_stake, "Minimum DePool stake", 10)
        reward_fraction = ui.prompt_integer(
            args.reward_fraction, "Participant reward fraction", 65
        )
        balance_threshold = ui.prompt_integer(
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
        else int(ui.prompt_for_value(None, "Multisig custodians", "3"))
    )
    required = (
        args.required_signatures
        if args.required_signatures is not None
        else int(ui.prompt_for_value(None, "Required signatures", "2"))
    )
    wallet_address = args.wallet_address
    depool_address = args.depool_address
    if importing:
        wallet_address = ui.prompt_for_value(wallet_address, "Existing wallet address")
        if depool_address is None and sys.stdin.isatty():
            depool_address = input("Existing DePool address: ").strip() or None
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
    ui.print_json_result(
        {
            "plan": "build/select image and prepare node; no startup, wallet creation or elections",
            "image": image_plan,
            "configuration": config,
        }
    )
    if args.dry_run:
        return 0
    ui.confirm_operation(args.yes, "Build/select image and prepare this node?")
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
