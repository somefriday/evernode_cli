"""Give a created node its validator role: how it stakes, and its Safe and DePool.

node create builds, starts and syncs a node with no role. node setup asks the
role's questions and saves the answers: the validator type, a new or an
existing Safe, the DePool's terms and the election settings. The wallet steps
that follow (wallet create, or wallet recover for an existing Safe) need them.
"""

import sys

from . import configuration, process, ui, wallets

NOMINATOR_SAFE_KEYS = [
    ("one", "One key - a single seed phrase signs every operation (recommended)"),
    ("several", "Several keys - each operation is proposed, then approved by others"),
]


def choose_safe_keys(args, nominator, importing):
    """Return (custodians, required signatures) for the node's Safe.

    A nominator validator's Safe only instructs its contract: the key it holds
    cannot move the principal, so a new one is offered a single key first.
    Several are still allowed, and the kit signs them the same way it signs a
    solo validator's. Flags, an imported Safe and the other validator types
    keep the plain prompts.
    """
    keys = None
    flagged = args.custodians is not None or args.required_signatures is not None
    if nominator and not importing and not flagged:
        keys = ui.select_choice(
            None, "Safe keys", NOMINATOR_SAFE_KEYS, flag="--custodians", default="one"
        )
    if keys == "one":
        return 1, 1
    several = keys == "several" or not nominator
    custodians = (
        args.custodians
        if args.custodians is not None
        else int(ui.prompt_for_value(None, "Multisig custodians", "3" if several else "1"))
    )
    required = (
        args.required_signatures
        if args.required_signatures is not None
        else int(ui.prompt_for_value(None, "Required signatures", "2" if several else "1"))
    )
    return custodians, required


def require_role_can_be_set(store, config):
    """A role is set once the node is created, and can change until its wallet
    setup begins: from then on the Safe's keys depend on it."""
    name = config["name"]
    if config["phase"] == "preparing":
        raise process.OperationError(
            f"{name} was not fully created: run evernode node create --resume -n {name}"
        )
    if config["phase"] == "removed":
        raise process.OperationError(
            f"{name} was removed: run evernode node start -n {name} first"
        )
    if not configuration.has_role(config):
        return
    stage = config.get("setup_stage", "node-started")
    if stage not in ("preparing", "node-started"):
        raise process.OperationError(
            f"{name}'s wallet setup has begun (stage {stage}); its role can no longer change"
        )
    seeds = wallets.seed_directory(store, config)
    if seeds.is_dir() and any(seeds.iterdir()):
        raise process.OperationError(
            f"{name} holds wallet keys or seed phrases in {seeds}; its role "
            "cannot change while they are there"
        )


def ask_role(store, config, args):
    """Ask the role's questions; return the validator type, settings and wallet profile."""
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
            "Use an existing Safe wallet? Its seed phrases are typed at wallet recover. [y/N]: "
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
        depool_type not in ("EverX", "StEver")
        or not 1 <= cron_interval <= 59
        or workchain not in (-1, 0)
        or any(value < 0 for value in (assurance, min_stake, balance_threshold))
        or not 0 <= reward_fraction <= 100
    ):
        raise process.OperationError(
            "Invalid workchain, cron interval or DePool numeric setting"
        )
    settings = {
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
    custodians, required = choose_safe_keys(args, nominator, importing)
    wallet_address = args.wallet_address
    depool_address = args.depool_address
    if not importing and (wallet_address or depool_address):
        # A new wallet's address is generated; one named here could never match it.
        raise process.OperationError(
            "--wallet-address and --depool-address name existing contracts; "
            "add --import-wallet to recover them"
        )
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
    profile = wallets.validate_wallet_profile(
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
    return {"validator_type": validator_type, "settings": settings, "wallet": profile}


def handle_node_setup(store, name, args):
    config = store.load_node_config(name)
    require_role_can_be_set(store, config)
    role = ask_role(store, config, args)
    ui.print_json_result({"plan": "set the validator role", "node": name, **role})
    if args.dry_run:
        return 0
    ui.confirm_operation(args.yes, f"Save this role for {name}?")
    with store.acquire_operation_lock(name):
        config = store.load_node_config(name)
        require_role_can_be_set(store, config)
        # The registry lock keeps another node's setup from taking the same
        # Safe or DePool between this check and the save.
        with store.acquire_operation_lock():
            profile = role["wallet"]
            wallets.ensure_unassigned(
                store, config, profile["wallet_address"], profile["depool_address"]
            )
            config.update(role)
            configuration.write_node_environment(
                store.node_directory(name) / "scripts" / "env.sh",
                configuration.build_node_environment(config),
            )
            store.save_node_config(config)
    ui.print_json_result(
        {
            "node": name,
            "validator_type": config["validator_type"],
            "wallet": config["wallet"]["mode"],
            "next": wallets.next_setup_command(config)
            or f"evernode node start -n {name}",
        }
    )
    return 0
