"""Per-election stake of a validator that stakes without a DePool.

A solo validator sends its own stake to the elector from its Safe wallet, so
the amount is a setting of this node rather than a property of a shared
contract. These actions read and change that setting, report what the elector
holds, and ask for finished rounds back.

The amount is always a fixed number of tokens. A mode is arithmetic performed
once, at the moment the operator chooses it, and written down as that number -
never a rule the election scripts re-evaluate later. Two reasons: a reward is
linear in the stake, so adapting the size each round earns nothing it could
not earn by being chosen once; and a bid cannot be taken back, so a sizing
decision made inside a live election window, on a wallet balance that may
still be waiting for a recovery, is the one decision in this system with no
way to correct it until the round ends.
"""

from . import configuration, nodes, process, scripts, ui, wallets

NANO = 10**9

# What each mode divides the spendable balance by. A stake is locked until its
# round finishes, and the next round's election opens while this one is still
# running, so two stakes in flight is what validating every round costs - and
# the ceiling, because the money from a finished round returns inside the
# window of the election two rounds out.
STAKE_MODES = {
    "balanced": (
        2,
        "Balanced - half in each of two rounds, validating every round",
    ),
    "single": (
        1,
        "Single round - everything in one round, validating every other round",
    ),
}
ROUNDS_IN_FLIGHT = 2


def _solo(store, config):
    wallets.require_validator_type(config, "solo", "depool")
    profile = wallets.node_wallet_profile(config)
    address = profile.get("wallet_address")
    if not address:
        raise process.OperationError(
            "This node has no wallet yet; run: evernode wallet create -n "
            + config["name"]
        )
    return profile, address


def _settings(config):
    return dict(configuration.DEFAULT_ENVIRONMENT, **(config.get("settings") or {}))


def recorded_stake_tokens(config):
    """The fixed per-election stake, or 0 when none has been recorded yet."""
    return _settings(config)["MSIG_FIX_STAKE"]


def require_recorded_stake(config):
    """Refuse to let a solo node bid before its stake is a decided number.

    Zero is what the reference scripts read as 'stake the balance', whose own
    sizing depends on a threshold this CLI does not expose. No managed node is
    allowed to reach an election in that state, however it was set up.
    """
    if configuration.validator_type(config) != "solo":
        return
    if recorded_stake_tokens(config) <= 0:
        raise process.OperationError(
            "This solo validator has no stake on record, so it has nothing to "
            "bid with; set one first: evernode stake set -n " + config["name"]
        )


def _chain_facts(config):
    """Read the limits and timings that decide whether a stake can work.

    These are network state, not settings of this installation, so a stake is
    checked against what the chain says now rather than against assumptions
    about one network's parameters.
    """
    limits = nodes.fetch_chain_parameter(config, 17)
    timings = nodes.fetch_chain_parameter(config, 15)

    def quantity(parameter, name):
        # Only the decimal form is read. The companion hex field is a
        # length-prefixed serialization, not a plain integer, and reading it
        # as one yields a limit that is wrong by orders of magnitude.
        try:
            value = int(str(parameter[name + "_dec"]), 10)
        except (KeyError, TypeError, ValueError) as exc:
            raise process.OperationError(
                f"The chain reported no usable {name}_dec in parameter 17"
            ) from exc
        if value <= 0:
            raise process.OperationError(f"The chain reported {name} as {value}")
        return value

    def period(name):
        value = timings.get(name)
        if not isinstance(value, int) or value <= 0:
            raise process.OperationError(
                f"The chain reported no usable {name} in parameter 15"
            )
        return value

    elected_for = period("validators_elected_for")
    held_for = period("stake_held_for")
    end_before = period("elections_end_before")
    factor = limits.get("max_stake_factor")
    return {
        "min_stake_nanotokens": quantity(limits, "min_stake"),
        "max_stake_nanotokens": quantity(limits, "max_stake"),
        # The elector truncates an effective stake above this multiple of the
        # smallest stake in the elected set, and returns the excess unused.
        # The chain states it in 16.16 fixed point, which reads as nothing.
        "max_stake_factor": factor,
        "max_stake_multiple": (
            round(factor / 65536, 2) if isinstance(factor, int) and factor > 0 else None
        ),
        "round_seconds": elected_for,
        "election_opens_before_round_end_seconds": period("elections_start_before"),
        "election_closes_before_round_end_seconds": end_before,
        "stake_held_after_round_seconds": held_for,
        # Whether a finished round's stake returns in time to fund the
        # election two rounds out, which is what makes two stakes enough.
        "stake_returns_within_next_election": held_for + end_before < elected_for,
    }


def _spendable_nanotokens(observed, settings):
    """Everything this validator could stake, minus what it keeps for fees."""
    wallet = int(observed["wallet_balance_nanotokens"])
    recoverable = int(observed["recoverable_nanotokens"])
    return wallet + recoverable - settings["VAL_ACC_RESERVED"] * NANO


def _funding(spendable, stake_nanotokens):
    """How many consecutive rounds this much money can bid in."""
    if stake_nanotokens <= 0:
        return 0, "funds no bid"
    rounds = spendable // stake_nanotokens
    if rounds <= 0:
        return 0, "funds no bid yet"
    if rounds == 1:
        return 1, "funds every other round"
    return rounds, "funds consecutive rounds"


def describe_stake(store, config):
    """Report the configured stake beside what the chain says about it."""
    _, address = _solo(store, config)
    settings = _settings(config)
    observed = scripts.read_solo_stake_state(store, config, address)
    facts = _chain_facts(config)
    fixed = settings["MSIG_FIX_STAKE"]
    spendable = _spendable_nanotokens(observed, settings)
    rounds, funding = _funding(spendable, fixed * NANO)
    observed.update(
        {
            "wallet_address": address,
            "configured_stake_tokens": fixed,
            "reserved_tokens": settings["VAL_ACC_RESERVED"],
            "max_factor": settings["MAX_FACTOR"],
            "spendable_tokens": spendable // NANO,
            "rounds_funded": rounds,
            "funding": (
                funding
                if fixed > 0
                else "no stake on record; run evernode stake set"
            ),
            "chain": facts,
        }
    )
    return observed


def _resolve_mode(mode, amount, spendable, facts):
    """Choose a mode, showing what each one would stake on this wallet."""
    if mode is not None or amount is not None:
        return mode
    choices = []
    for name, (divisor, description) in STAKE_MODES.items():
        tokens = spendable // (divisor * NANO)
        affordable = tokens * NANO >= facts["min_stake_nanotokens"]
        choices.append(
            (
                name,
                f"{description} - {tokens} tokens per election"
                + ("" if affordable else ", below the elector's minimum"),
            )
        )
    return ui.select_choice(None, "stake mode", choices, flag="--mode")


def set_stake(store, config, amount=None, mode=None):
    """Record what this validator stakes in each election.

    A mode is resolved to a number here, against the balance and the chain
    limits as they are now, and the arithmetic is reported rather than left
    to be inferred. Afterwards the election scripts only ever read a fixed
    amount, which is the one sizing they handle without a hidden threshold.
    """
    _, address = _solo(store, config)
    if amount is not None and mode is not None:
        raise process.OperationError("Use --amount N or --mode, not both")
    if amount is not None and amount <= 0:
        raise process.OperationError("A stake must be a positive number of tokens")
    settings = _settings(config)
    observed = scripts.read_solo_stake_state(store, config, address)
    facts = _chain_facts(config)
    spendable = _spendable_nanotokens(observed, settings)
    mode = _resolve_mode(mode, amount, spendable, facts)
    if mode is not None and mode not in STAKE_MODES:
        raise process.OperationError(
            "Unknown stake mode; choose one of: " + ", ".join(STAKE_MODES)
        )

    divisor = STAKE_MODES[mode][0] if mode else 1
    if mode is None:
        tokens = amount
    else:
        if spendable <= 0:
            raise process.OperationError(
                "This wallet holds nothing to stake beyond the "
                f"{settings['VAL_ACC_RESERVED']} tokens reserved for fees; "
                "fund it first, or name an amount with --amount"
            )
        tokens = spendable // (divisor * NANO)
    stake_nanotokens = tokens * NANO
    minimum = facts["min_stake_nanotokens"]
    maximum = facts["max_stake_nanotokens"]
    warnings = []
    if stake_nanotokens < minimum:
        if mode == "balanced":
            raise process.OperationError(
                f"Half of this wallet is {tokens} tokens, below the "
                f"{minimum // NANO} the elector accepts, so this much capital "
                "can only validate every other round: use --mode single"
            )
        if mode == "single":
            raise process.OperationError(
                f"This wallet has {spendable // NANO} tokens spendable, below "
                f"the {minimum // NANO} the elector accepts as a stake; fund it "
                "first"
            )
        raise process.OperationError(
            f"The elector rejects a stake below {minimum // NANO} tokens"
        )
    if stake_nanotokens > maximum:
        # A named amount the chain would reject is the operator's mistake to
        # see. A mode is arithmetic, so it caps at what the chain allows and
        # says that it did rather than leaving no usable answer.
        if mode is None:
            raise process.OperationError(
                f"The elector caps a stake at {maximum // NANO} tokens"
            )
        tokens = maximum // NANO
        stake_nanotokens = tokens * NANO
        warnings.append(
            f"Capped at the {tokens} tokens the elector accepts as one stake; "
            "the rest stays in the Safe."
        )

    if stake_nanotokens > spendable:
        warnings.append(
            f"This wallet cannot fund one bid of {tokens} tokens yet; it has "
            f"{spendable // NANO} spendable. Fund it before the next election."
        )
    elif stake_nanotokens * ROUNDS_IN_FLIGHT > spendable:
        warnings.append(
            "This funds one round at a time, not consecutive rounds: a stake "
            "is locked until its round finishes, so bidding in the next one "
            f"needs about {2 * tokens} tokens spendable, and this wallet has "
            f"{spendable // NANO}."
        )
    recoverable = int(observed["recoverable_nanotokens"])
    if recoverable > 0:
        warnings.append(
            f"{recoverable // NANO} tokens of that are still at the elector; "
            "the scheduled cycle recovers them before it places a bid."
        )
    if not facts["stake_returns_within_next_election"]:
        warnings.append(
            "On this network a finished round's stake is released too late to "
            "fund the election two rounds out, so consecutive rounds need "
            "money beyond these two stakes."
        )

    settings["MSIG_FIX_STAKE"] = tokens
    config["settings"] = settings
    if config.get("setup_stage") in ("wallet-deployed", "imported"):
        config["setup_stage"] = "ready-for-elections"
    configuration.write_node_environment(
        store.node_directory(config["name"]) / "scripts" / "env.sh",
        configuration.build_node_environment(config),
    )
    rounds, funding = _funding(spendable, stake_nanotokens)
    result = {
        "wallet_address": address,
        "mode": mode or "exact",
        "stake_tokens": tokens,
        "arithmetic": {
            "wallet_balance_tokens": int(observed["wallet_balance_nanotokens"]) // NANO,
            "recoverable_tokens": recoverable // NANO,
            "reserved_tokens": settings["VAL_ACC_RESERVED"],
            "spendable_tokens": spendable // NANO,
            "divided_by": divisor,
        },
        "chain": facts,
        "rounds_funded": rounds,
        "funding": funding,
        "warnings": warnings,
        "stage": config.get("setup_stage"),
        "next": wallets.next_setup_command(config)
        or f"Check: evernode stake status -n {config['name']}",
    }
    return config, result


def recover(store, config):
    """Ask the elector to return the stake of rounds that have finished."""
    _, address = _solo(store, config)
    return scripts.recover_solo_stake(store, config, address)
