"""Named Safe wallet and DePool setup stages for synchronized nodes."""

import sys

from . import configuration, nodes, process, ui, wallets


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
            ui.confirm_operation(
                args.yes, f"Run {args.group} {args.action} for {name}?"
            )
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
        ui.print_json_result(result)
    return 0
