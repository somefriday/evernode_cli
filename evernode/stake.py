"""Per-election stake of a validator that stakes without a DePool.

A solo validator sends its own stake to the elector from its Safe wallet, so
the amount is a setting of this node rather than a property of a shared
contract. These actions read and change that setting, report what the elector
holds, and ask for finished rounds back.
"""

from . import configuration, process, scripts, storage, wallets

NANO = 10**9


def _solo(store, config):
    wallets.require_validator_type(config, "solo", "depool")
    profile = wallets.validate_wallet_profile(config["wallet"])
    address = profile.get("wallet_address")
    if not address:
        raise process.OperationError(
            "This node has no wallet yet; run: evernode wallet create -n "
            + config["name"]
        )
    return profile, address


def _settings(config):
    return dict(configuration.DEFAULT_ENVIRONMENT, **(config.get("settings") or {}))


def describe_stake(store, config):
    """Report the configured stake beside what the chain says about it."""
    _, address = _solo(store, config)
    settings = _settings(config)
    observed = scripts.read_solo_stake_state(store, config, address)
    fixed = settings["MSIG_FIX_STAKE"]
    observed.update(
        {
            "wallet_address": address,
            "configured_stake_tokens": fixed or "all-balance",
            "reserved_tokens": settings["VAL_ACC_RESERVED"],
            "max_factor": settings["MAX_FACTOR"],
        }
    )
    return observed


def set_stake(store, config, amount=None, all_balance=False):
    """Record what this validator stakes in each election.

    A stake stays locked until the round that used it has finished, so taking
    part in consecutive rounds needs about twice the amount, plus fees, on the
    wallet. The command says so rather than leaving it to be discovered.
    """
    _, address = _solo(store, config)
    if all_balance == (amount is not None):
        raise process.OperationError("Use --amount N or --all-balance, not both")
    if amount is not None and amount <= 0:
        raise process.OperationError("A stake must be a positive number of tokens")
    settings = _settings(config)
    settings["MSIG_FIX_STAKE"] = 0 if all_balance else amount
    config["settings"] = settings
    if config.get("setup_stage") == "wallet-deployed":
        config["setup_stage"] = "ready-for-elections"
    configuration.write_node_environment(
        store.node_directory(config["name"]) / "scripts" / "env.sh",
        configuration.build_node_environment(config),
    )
    result = {
        "wallet_address": address,
        "stake_tokens": amount if amount is not None else "all-balance",
        "stage": config.get("setup_stage"),
        "note": (
            "A stake is locked until its round finishes, so bidding in "
            "consecutive rounds needs about twice this amount plus fees on the "
            "wallet. The kit stakes half the balance in the first election when "
            "no fixed amount is set."
        ),
        "next": wallets.next_setup_command(config)
        or f"Check: evernode stake status -n {config['name']}",
    }
    return config, result


def recover(store, config):
    """Ask the elector to return the stake of rounds that have finished."""
    _, address = _solo(store, config)
    return scripts.recover_solo_stake(store, config, address)
