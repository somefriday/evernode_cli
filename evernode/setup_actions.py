"""Named Safe wallet and DePool setup stages for synchronized nodes."""

import contextlib
import sys

from . import configuration, elections, nodes, process, scripts, ui, wallets


def _require_synchronized_setup_node(store, name):
    config = store.load_node_config(name)
    # One guard decides whether this node's console can serve an operation, so
    # every command refuses for the same reason in the same words. It is the
    # only transport there is; see signing-reliability-design.md, D13.
    scripts.require_node_console(config)
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


def _confirmation_question(args, name):
    if (args.group, args.action) == ("depool", "withdraw"):
        if args.cancel:
            return f"Cancel the pending DePool withdrawal for {name} and resume reinvestment?"
        target = "the entire ordinary stake" if args.withdraw_all else f"{args.amount} tokens"
        return (
            f"Withdraw {target} from {name}'s DePool? The stake is released when its "
            "round completes, which can end this node's validation."
        )
    if (args.group, args.action) == ("depool", "confirm"):
        return (
            f"Add {name}'s remaining signatures to transaction "
            f"{args.transaction_id}? Review it in depool info first; signing "
            "executes the transfer it carries."
        )
    if (args.group, args.action) == ("depool", "stake"):
        return f"Stake {args.amount} tokens from {name}'s Safe into its DePool?"
    if (args.group, args.action) == ("depool", "replenish"):
        return f"Send {args.amount} tokens from {name}'s Safe to its DePool balance?"
    return f"Run {args.group} {args.action} for {name}?"


# These submit multisig transactions from the node's Safe, so they must not run
# beside a scheduled election attempt that is signing from the same wallet.
ELECTION_LOCKED_ACTIONS = (
    ("depool", "stake-initial"),
    ("depool", "stake"),
    ("depool", "confirm"),
    ("depool", "withdraw"),
    ("depool", "replenish"),
)


def handle_setup_action(store, args):
    name = configuration.validate_node_name(args.name)
    action = (args.group, args.action)
    mutating = action not in (
        ("wallet", "verify"),
        ("depool", "verify"),
        ("depool", "info"),
    )
    secret_action = action in (("wallet", "create"), ("depool", "prepare"))
    if secret_action and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise process.OperationError(
            "Wallet and DePool seed generation requires an interactive terminal; phrases are never printed to redirected output"
        )
    with contextlib.ExitStack() as stack:
        stack.enter_context(store.acquire_operation_lock(name))
        if action in ELECTION_LOCKED_ACTIONS:
            stack.enter_context(elections.hold_election_lock(store, name))
        config = _require_synchronized_setup_node(store, name)
        if mutating:
            ui.confirm_operation(args.yes, _confirmation_question(args, name))
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
        else:
            raise process.OperationError("Unsupported wallet or DePool action")
        store.save_node_config(result_config)
        ui.print_json_result(result)
    return 0
