"""Interactive preparation and startup of one managed validator node."""

import getpass
import ipaddress
import re
import sys
import urllib.request

from . import configuration, docker, images, nodes, process, provisioning, ui, wallets


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
    if args.node_ref and not args.build_new:
        raise process.OperationError("--node-ref requires --build-new")
    if args.image and any(
        (args.node_repo, args.cli_repo, args.cli_ref, args.image_repo)
    ):
        raise process.OperationError("Source options cannot be used with --image")
    container_name, statsd_name = docker.select_container_names(store, name)
    validator_type = ui.select_choice(
        args.validator_type,
        "validator type",
        [
            ("depool", "DePool - stake through a DePool contract, shared with participants"),
            ("solo", "Solo - stake directly from this validator's own Safe wallet"),
            ("nominator", "Nominator - stake from a contract a cold owner controls"),
        ],
        flag="--validator-type",
        # Existing automation predates this choice, so a noninteractive run
        # keeps the behaviour it had: a DePool validator.
        default="depool",
    )
    solo = validator_type == "solo"
    nominator = validator_type == "nominator"
    importing = args.import_wallet
    if not importing and sys.stdin.isatty():
        importing = input(
            "Recover an existing Safe wallet from seed phrases? [y/N]: "
        ).strip().lower() in ("y", "yes")
    depool_arguments = (
        args.validator_assurance,
        args.min_stake,
        args.reward_fraction,
        args.balance_threshold,
        args.depool_type,
    )
    if nominator and args.owner_address is None and not sys.stdin.isatty():
        raise process.OperationError(
            "A nominator validator's stake is held by a contract owned by a "
            "cold wallet; name it with --owner-address"
        )
    if args.owner_address is not None and not nominator:
        raise process.OperationError(
            "--owner-address applies only to a nominator validator"
        )
    if (solo or nominator) and any(value is not None for value in depool_arguments):
        raise process.OperationError(
            "DePool parameters do not apply to a solo validator, which stakes "
            "from its own Safe wallet; set its stake with evernode stake set"
        )
    if importing and any(value is not None for value in depool_arguments[:4]):
        raise process.OperationError(
            "DePool deployment parameters do not apply to an imported DePool"
        )
    network = ui.select_choice(
        args.network,
        "network",
        [("main", "main - the Everscale main network"),
         ("devnet", "devnet - the development network")],
        flag="--network",
        default="main",
    )
    # This is the workchain the node itself works in. Where its Safe wallet
    # lives follows from the validator type, not from this: a solo validator
    # stakes straight to the elector, which accepts a stake only from the
    # masterchain, so its Safe is always in -1 whatever the node does.
    workchain = int(
        ui.select_choice(
            None if args.workchain is None else str(args.workchain),
            "node workchain",
            [("0", "0 - basechain"), ("-1", "-1 - masterchain")],
            flag="--workchain",
            default="0",
        )
    )
    cron_interval = ui.prompt_integer(
        args.cron_interval, "Election check interval in minutes", 10
    )
    depool_type = (
        configuration.DEFAULT_ENVIRONMENT["DePool_TYPE"]
        # Neither of these modes has a DePool, so neither should be asked
        # which kind it wants.
        if solo or nominator
        else ui.select_choice(
            args.depool_type,
            "DePool type",
            [("EverX", "EverX - the DePool v3 contract bundled with this CLI"),
             ("StEver", "StEver - the stEVER liquid staking DePool")],
            flag="--depool-type",
            default="EverX",
        )
    )
    if solo or nominator or importing:
        # A solo validator has no DePool, and an imported DePool's terms are
        # on-chain contract state. Keep the internal environment schema
        # complete without pretending these defaults describe either.
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
        "validator_type": validator_type,
    }
    owner = args.owner_address
    if nominator:
        owner = ui.prompt_for_value(owner, "Cold owner wallet address")
        # The contract stores the owner in its initial data and has no
        # change-owner operation, so a wrong address here is permanent.
        if not str(owner).startswith("-1:"):
            raise process.OperationError(
                "The owner authorizes withdrawals from a masterchain "
                "contract; its address must start with -1:"
            )
        if not wallets.ADDRESS_PATTERN.fullmatch(str(owner)):
            raise process.OperationError("Invalid owner address")
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
        "NOMINATOR_OWNER": owner or "",
        "NOMINATOR_ADDR": "",
        # A solo stake is set after the wallet is deployed, by stake set.
        "MSIG_FIX_STAKE": configuration.DEFAULT_ENVIRONMENT["MSIG_FIX_STAKE"],
        "VAL_ACC_RESERVED": configuration.DEFAULT_ENVIRONMENT["VAL_ACC_RESERVED"],
        "MAX_FACTOR": configuration.DEFAULT_ENVIRONMENT["MAX_FACTOR"],
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
    # A nominator validator's Safe only instructs its contract, so one
    # custodian is the usual choice: the key it holds cannot move the
    # principal. More are still allowed, and the kit signs them the same way
    # it signs a solo validator's.
    custodians = (
        args.custodians
        if args.custodians is not None
        else int(
            ui.prompt_for_value(None, "Multisig custodians", "1" if nominator else "3")
        )
    )
    required = (
        args.required_signatures
        if args.required_signatures is not None
        else int(
            ui.prompt_for_value(None, "Required signatures", "1" if nominator else "2")
        )
    )
    wallet_address = args.wallet_address
    depool_address = args.depool_address
    if importing:
        wallet_address = ui.prompt_for_value(wallet_address, "Existing wallet address")
        if solo or nominator:
            # The elector only accepts a stake sent from the masterchain. A
            # solo validator sends it itself; a nominator validator's wallet
            # has to instruct a masterchain contract, which refuses anything
            # else with WRONG_VALIDATOR_WC. Neither can use a basechain Safe.
            if not str(wallet_address).startswith("-1:"):
                raise process.OperationError(
                    f"A {validator_type} validator stakes from the "
                    "masterchain; its wallet address must start with -1:"
                )
        elif depool_address is None and sys.stdin.isatty():
            depool_address = input("Existing DePool address: ").strip() or None
    config["wallet"] = wallets.validate_wallet_profile(
        {
            "mode": "import" if importing else "new",
            "custodians": custodians,
            "required_signatures": required,
            "wallet_address": wallet_address,
            "depool_address": depool_address,
        },
        validator_type=validator_type,
    )
    if nominator:
        wallets.ensure_owner_is_external(store, config, owner)
    wallets.ensure_unassigned(store, config, wallet_address, depool_address)
    config["setup_stage"] = "preparing"
    if importing and not sys.stdin.isatty():
        raise process.OperationError(
            "Seed import requires an interactive terminal; seed phrases are never accepted as command arguments"
        )
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
    except BaseException:
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
    next_action = "wallet recover" if importing else "wallet create"
    print(f"Started {name}. Wait for sync, then run: evernode {next_action} -n {name}")
    return 0
