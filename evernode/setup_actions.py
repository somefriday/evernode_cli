"""Named Safe wallet and DePool setup stages for synchronized nodes."""

import contextlib
from pathlib import Path
import sys

from . import configuration, denominator, elections, nominator, nodes, process, scripts, stake, ui, wallets


# Preparing the nominator contract compiles code and derives an address. It
# spends nothing, sends nothing and reads no chain state, so requiring a
# synchronized console would only stop an operator inspecting what they are
# about to deploy before the node has caught up. A wallet link for the owner
# is built from the saved setup alone, so it needs no console either.
OFFLINE_ACTIONS = (("nominator", "prepare"), ("nominator", "link"))


def _require_setup_node(store, name, action):
    config = store.load_node_config(name)
    # The role first: a node from node create has none until node setup, and
    # that step is what to name, before anything the console could refuse.
    configuration.require_role(config)
    # One guard decides whether this node's console can serve an operation, so
    # every command refuses for the same reason in the same words. It is the
    # only transport there is; see signing-reliability-design.md, D13.
    if action not in OFFLINE_ACTIONS:
        scripts.require_node_console(config)
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


def _confirmation_question(args, config):
    name = config["name"]
    if configuration.validator_type(config) == "denominator":
        if (args.group, args.action) == ("stake", "set"):
            # Refused before anything is asked: a pool stakes all it holds.
            denominator.refuse_stake_set(config)
        if (args.group, args.action) == ("stake", "recover"):
            return (
                f"Run {name}'s pool maintenance now: count new validator sets, take back "
                "the stakes the pools allow and pay queued withdrawals?"
            )
    # A nominator's stake leaves from, and returns to, its contract.
    account = (
        "nominator contract"
        if configuration.validator_type(config) == "nominator"
        else "Safe"
    )
    if (args.group, args.action) == ("depool", "withdraw"):
        if args.cancel:
            return f"Cancel the pending DePool withdrawal for {name} and resume reinvestment?"
        target = "the entire ordinary stake" if args.withdraw_all else f"{args.amount} tokens"
        return (
            f"Withdraw {target} from {name}'s DePool? The stake is released when its "
            "round completes, which can end this node's validation."
        )
    if (args.group, args.action) == ("depool", "stake"):
        return f"Stake {args.amount} tokens from {name}'s Safe into its DePool?"
    if (args.group, args.action) == ("depool", "replenish"):
        return f"Send {args.amount} tokens from {name}'s Safe to its DePool balance?"
    if (args.group, args.action) == ("stake", "set"):
        if args.amount is not None:
            return (
                f"Stake {args.amount} tokens from {name}'s {account} in every "
                "election? A stake is locked until its round finishes, so "
                f"consecutive rounds need about twice that in the {account}."
            )
        mode = "single" if args.all_balance else args.mode
        target = (
            f"the {mode} amount" if mode else "an amount chosen from the menu next"
        )
        return (
            f"Record what {name} stakes in every election, computing {target} "
            "from its spendable balance now?"
        )
    if (args.group, args.action) == ("nominator", "prepare"):
        return (
            f"Compile the single-nominator contract for {name} and derive its "
            "address? Nothing is deployed or spent yet."
        )
    if (args.group, args.action) == ("nominator", "deploy"):
        return (
            f"Deploy {name}'s nominator contract? This spends gas from its "
            "Safe to fund a one-shot deployer."
        )
    if (args.group, args.action) == ("nominator", "submit"):
        return (
            f"Broadcast {args.message} as {name}'s owner-signed message? "
            "Review what it authorizes before submitting it."
        )
    if (args.group, args.action) == ("stake", "recover"):
        return f"Ask the elector to return {name}'s finished-round stake to its {account}?"
    return f"Run {args.group} {args.action} for {name}?"


# Signing one named Safe transaction executes the transfer it carries.
CONFIRM_ACTIONS = (("depool", "confirm"), ("stake", "confirm"))


def _own_contracts(store, config):
    """The accounts this node's own operations pay from its Safe, by address."""
    contracts = {
        nodes.fetch_special_address(config, 1): "the elector",
        nodes.fetch_special_address(config, 0): "the config contract",
    }
    kind = configuration.validator_type(config)
    if kind == "depool":
        profile = wallets.node_wallet_profile(config)
        contracts[profile["depool_address"]] = "this node's DePool"
        state = scripts.read_depool_state(
            store, config, profile["depool_address"], profile["wallet_address"]
        )
        for proxy in state.get("proxies") or []:
            contracts[proxy] = "a proxy of this node's DePool"
    elif kind == "nominator":
        address = (config.get("settings") or {}).get("NOMINATOR_ADDR")
        if address:
            contracts[address] = "this node's nominator contract"
    elif kind == "denominator":
        for pool in (config.get("denominator") or {}).get("pools") or []:
            contracts[pool["address"]] = f"this node's pool {pool['label']}"
    return {address.lower(): whose for address, whose in contracts.items()}


def _review_transaction(store, config, args):
    """The question before signing a Safe transaction: what it sends, and to
    whom. One that pays none of this node's own contracts is refused unless
    --force, since signing it sends the money there."""
    wallets.require_confirmable(config, args.group, args.transaction_id)
    name, wanted = config["name"], str(args.transaction_id)
    profile = wallets.node_wallet_profile(config)
    pending = scripts.read_wallet_pending(store, config, profile["wallet_address"]).get("pending_transactions") or []
    entry = next((item for item in pending if str(item.get("id")) == wanted), None)
    if entry is None:
        raise process.OperationError(f"{name}'s Safe holds no pending transaction {wanted}")
    dest = str(entry.get("dest", "")).lower()
    if dest == str(profile.get("depool_address") or "").lower():
        # A tick or a bid left pending makes the DePool ask the elector through
        # its proxies once it executes (ELECT-8). Nothing here tells one from a
        # withdrawal, so the refusal says what becomes of each kind.
        try:
            wallets.require_live_proxies(store, config)
        except process.OperationError as error:
            raise process.OperationError(
                f"{error}. A withdrawal or a replenishment left pending is finished by "
                "running the same depool withdraw or depool replenish again. A stake, "
                "a tick or a bid left pending waits until the proxy is restored: until "
                "then it stops every scheduled run, and it expires an hour after it was "
                "submitted"
            ) from error
    try:
        whose = _own_contracts(store, config).get(dest)
    except process.OperationError:
        # A DePool that cannot be read is one more reason --force exists.
        if not args.force:
            raise
        whose = "could not be checked"
    if whose is None and not args.force:
        raise process.OperationError(
            f"Transaction {wanted} sends to {dest}, which is not one of {name}'s own "
            "contracts. Check where it came from; --force signs it anyway"
        )
    whole, part = divmod(int(entry.get("value", 0)), 10**9)
    tokens = f"{whole}.{part:09d}".rstrip("0").rstrip(".")
    whose = whose or "not one of this node's contracts"
    return (
        f"Sign transaction {wanted}? It sends {tokens} tokens to {dest} "
        f"({whose}) and holds "
        f"{entry.get('signsReceived')} of {entry.get('signsRequired')} signatures; "
        "signing executes it."
    )


def _own_accounts(store, config):
    """This node's own accounts, which wallet send never pays: those
    _own_contracts names, and the Safe itself.

    _own_contracts reads the DePool's proxies, which exist only once the DePool
    is on chain. A DePool node can send before that, and then the list holds
    the DePool's address alone, once it has one. A read that fails refuses the
    send."""
    profile = wallets.node_wallet_profile(config)
    depool = profile.get("depool_address")
    if configuration.validator_type(config) == "depool" and (
        not depool
        or scripts.read_account_status(store, config, depool) in ("Nonexist", "Uninit")
    ):
        contracts = {
            nodes.fetch_special_address(config, 1): "the elector",
            nodes.fetch_special_address(config, 0): "the config contract",
        }
        if depool:
            contracts[depool] = "this node's DePool"
        accounts = {address.lower(): whose for address, whose in contracts.items()}
    else:
        accounts = _own_contracts(store, config)
    accounts[profile["wallet_address"].lower()] = "this node's own Safe"
    return accounts


def _review_send(store, config, args):
    """The question before wallet send, asked after every check that can refuse
    it. The send stays apart from the node's own work: never while the election
    schedule is installed, and never to the node's own accounts."""
    name = config["name"]
    plan = wallets.plan_send(config, args.address, args.amount)
    if elections.schedule_can_bid(config):
        raise process.OperationError(
            f"{name}'s election schedule is installed. Stop it, send, and start it "
            f"again: evernode election stop -n {name}, then evernode election "
            f"start -n {name}"
        )
    whose = _own_accounts(store, config).get(plan["address"])
    if whose:
        raise process.OperationError(f"wallet send never pays {whose} ({plan['address']})")
    bounce = (
        "Bounce is off: the tokens stay at the address even if it has no contract."
        if args.no_bounce
        else "Bounce is on: an address with no contract, or one that refuses the "
        "tokens, sends them back minus fees."
    )
    return (
        f"Send {args.amount} tokens from {name}'s Safe {plan['wallet_address']} "
        f"to {plan['address']}? {bounce}"
    )


# These submit multisig transactions from the node's Safe, so they must not run
# beside a scheduled election attempt that is signing from the same wallet.
ELECTION_LOCKED_ACTIONS = (
    ("wallet", "send"),
    ("depool", "stake-initial"),
    ("depool", "stake"),
    ("depool", "confirm"),
    ("depool", "withdraw"),
    ("depool", "replenish"),
    ("stake", "recover"),
    ("stake", "confirm"),
    # Not a transaction, but it rewrites the env.sh a running election reads
    # while it is sizing a bid.
    ("stake", "set"),
    # Rewrites env.sh as well: the contract address and the tools image a bid uses.
    ("nominator", "prepare"),
    ("nominator", "deploy"),
    ("nominator", "submit"),
)


def handle_setup_action(store, args):
    name = configuration.validate_node_name(args.name)
    action = (args.group, args.action)
    mutating = action not in (
        ("wallet", "verify"),
        ("depool", "verify"),
        ("depool", "info"),
        ("stake", "status"),
        ("nominator", "info"),
        ("nominator", "withdraw"),
        ("nominator", "rotate"),
        ("nominator", "link"),
    )
    secret_action = action in (("wallet", "create"), ("depool", "prepare"))
    if secret_action and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise process.OperationError(
            "Wallet and DePool seed generation requires an interactive terminal; phrases are never printed to redirected output"
        )
    with contextlib.ExitStack() as stack:
        stack.enter_context(store.acquire_operation_lock(name))
        config = _require_setup_node(store, name, action)
        # Every question comes before the election lock: while one waits for
        # an answer with the lock held, each scheduled election run is skipped.
        if mutating:
            if action in CONFIRM_ACTIONS:
                question = _review_transaction(store, config, args)
            elif action == ("wallet", "send"):
                question = _review_send(store, config, args)
            else:
                question = _confirmation_question(args, config)
            ui.confirm_operation(args.yes, question)
        if action == ("stake", "set") and args.amount is None and args.mode is None and not args.all_balance:
            args.mode = stake.ask_mode(store, config)
        if action in ELECTION_LOCKED_ACTIONS:
            stack.enter_context(elections.hold_election_lock(store, name))
        if action == ("wallet", "create"):
            result_config, result, phrases = wallets.wallet_create(store, config)
            _print_new_secrets("Safe wallet", result["wallet"]["address"], phrases)
            _acknowledge_secret_backup()
        elif action == ("wallet", "deploy"):
            result_config, result = wallets.wallet_deploy(store, config)
        elif action == ("wallet", "recover"):
            if args.replace_seeds or not wallets.import_seeds_stored(store, config):
                wallets.enter_import_seeds(store, config, replace=args.replace_seeds)
            result_config, result = wallets.wallet_recover(store, config)
        elif action == ("wallet", "verify"):
            result_config, result = wallets.wallet_verify(store, config)
        elif action == ("wallet", "send"):
            result_config, result = config, wallets.wallet_send(
                store,
                config,
                address=args.address,
                amount=args.amount,
                bounce=not args.no_bounce,
            )
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
        elif action == ("depool", "info"):
            result_config, result = wallets.depool_info(store, config)
        elif action == ("depool", "withdraw"):
            result_config, result = wallets.depool_withdraw(
                store,
                config,
                amount=args.amount,
                withdraw_all=args.withdraw_all,
                cancel=args.cancel,
            )
        elif action == ("depool", "confirm"):
            result_config, result = wallets.depool_confirm(
                store, config, transaction_id=args.transaction_id
            )
        elif action == ("depool", "stake"):
            result_config, result = wallets.depool_stake(
                store, config, amount=args.amount
            )
        elif action == ("depool", "replenish"):
            result_config, result = wallets.depool_replenish(
                store, config, amount=args.amount
            )
        elif action == ("stake", "set"):
            result_config, result = stake.set_stake(
                store,
                config,
                amount=args.amount,
                mode="single" if args.all_balance else args.mode,
            )
        elif action == ("stake", "status"):
            result_config, result = config, stake.describe_stake(store, config)
        elif action == ("stake", "recover"):
            result_config, result = config, stake.recover(store, config)
        elif action == ("nominator", "prepare"):
            result_config, result = nominator.prepare(
                store,
                config,
                source=args.contract_source,
                new_contract=args.new_contract,
                recipe=Path(__file__).resolve().parent
                / "assets"
                / "nominator-tools.Dockerfile",
            )
        elif action == ("nominator", "deploy"):
            result_config, result = nominator.deploy(store, config)
        elif action == ("nominator", "verify"):
            result_config, result = nominator.verify(store, config)
        elif action == ("nominator", "info"):
            result_config, result = config, nominator.info(store, config)
        elif action == ("nominator", "withdraw"):
            result_config, result = config, nominator.withdraw(
                store, config, amount=args.amount, withdraw_all=args.withdraw_all
            )
        elif action == ("nominator", "rotate"):
            result_config, result = config, nominator.rotate(
                store, config, validator_address=args.validator_address
            )
        elif action == ("nominator", "submit"):
            result_config, result = config, nominator.submit(
                store, config, message=args.message
            )
        elif action == ("nominator", "link"):
            result_config, result = config, nominator.owner_link(
                config, value=args.value, body=args.body
            )
        elif action == ("stake", "confirm"):
            result_config, result = wallets.stake_confirm(
                store, config, transaction_id=args.transaction_id
            )
        else:
            raise process.OperationError("Unsupported wallet or DePool action")
        store.save_node_config(result_config)
        ui.print_json_result(result)
    return 0
