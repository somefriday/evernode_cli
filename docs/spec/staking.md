# Staking commands

The commands that move or report stake outside a scheduled run: the DePool operations, the solo
and nominator stake, and the nominator owner's requests. Every transfer here follows
[wallet-operations.md](wallet-operations.md). Flags are on each command's page.

Every command here takes the node lock, requires a role and, unless noted, a synchronized node
([setup.md](setup.md#what-every-setup-command-does-first)).

## DePool operations

Commands: [`depool stake`](../depool/stake.md), [`depool withdraw`](../depool/withdraw.md),
[`depool replenish`](../depool/replenish.md). Code: `wallets.py`, `stake_to_depool.sh`,
`depool_functions/`.

| Command                        | Kit operation      | Message to the DePool    | Value sent              |
| ------------------------------ | ------------------ | ------------------------ | ----------------------- |
| `depool stake --amount N`      | `ordinary`         | `addOrdinaryStake`       | N tokens plus the stake fee |
| `depool withdraw --amount N`   | `WD-part`          | `withdrawPart`           | the stake fee           |
| `depool withdraw --all`        | `WD-all`           | `withdrawAll`            | the stake fee           |
| `depool withdraw --cancel`     | `cancelWithdrawal` | `cancelWithdrawal`       | the stake fee           |
| `depool replenish --amount N`  | `replenish`        | `receiveFunds`           | N tokens                |

The stake fee is the DePool's own `stakeFee`. The DePool returns what it does not use.

**Requires**

- Validator type `depool` and stage `imported` or `ready-for-elections`.
- An amount of at least 1 token, where an amount is given. `depool withdraw` takes exactly one
  of its three forms.
- For `depool stake`, both proxies read as Active ([ELECT-8](properties.md#elect-8)), and a low
  one is topped up first ([ELECT-7](properties.md#elect-7)). evernode does both before it runs the
  script. A withdrawal and a replenishment go ahead whatever state the proxies are in.
- Checked by `stake_to_depool.sh` before any operation:
  - the Safe is active and holds at least 1 token;
  - the DePool is active and not closed.
- Checked for every operation but `replenish`: the DePool's own balance is at least its balance
  threshold less 2 tokens, and at least 12 tokens.
- Checked for a stake, not for a withdrawal: each proxy holds at least 1.92 tokens.
- Checked by the operation:
  - stake: the Safe holds the amount plus 1 token, and the amount is at least the DePool's
    minimum stake;
  - replenish: the Safe holds the amount plus 1 token;
  - withdraw part: the Safe has a stake in the DePool.

**Logic**

1. evernode asks for confirmation, takes the election lock, checks the proxies and tops up a low
   one (for `depool stake`) and runs `stake_to_depool.sh` with the operation. The script does not
   ask again.
2. The script makes the checks above.
3. The operation builds its message and follows the protocol
   ([wallet-operations.md](wallet-operations.md#one-operation)), with resume and the queue check.
4. The effect it waits for is in the table of
   [effects](wallet-operations.md#effects).

**Result**

- Stake: the amount joins the DePool's round that is collecting now. A round that is already
  being elected does not get it.
- Withdraw: the DePool records the request. It pays when the round that holds the stake
  completes, so the Safe's balance changes later.
- Cancel: reinvestment is on again and no withdrawal is recorded.
- Replenish: the DePool's own balance grows.

**On failure**

As in [wallet-operations.md](wallet-operations.md#one-operation). Running the same command again
with the same amount finishes a transaction left pending and does not add a second one.

## DePool report

Command: [`depool info`](../depool/info.md). Code: `wallets.depool_info`,
`scripts.read_depool_state`.

**Requires**

- Validator type `depool` and stage `depool-deployed`, `imported` or `ready-for-elections`.

**Logic**

It reads through the kit's getters and sends nothing: the DePool's settings and proxies, its
rounds, its own balance, the Safe's stake, pending withdrawal and reinvest flag, and every pending
transaction in the Safe. It does not take the election lock.

## Set the stake

Command: [`stake set`](../stake/set.md). Code: `stake.set_stake`, `stake.ask_mode`.

A solo or nominator validator stakes one fixed number of whole tokens in every election
([SETUP-5](properties.md#setup-5)). The *staking account* is the Safe for a solo validator and the
nominator contract for a nominator validator.

**Requires**

- Validator type `solo` or `nominator`.
- A Safe address; for a nominator validator, a prepared contract.

**Logic**

1. Ask for confirmation. If neither an amount nor a mode was given, read the balance and show
   the mode menu. Both happen before the election lock ([LOCK-3](properties.md#lock-3)).
2. Take the election lock: a scheduled run reads this number from `env.sh`.
3. Read the staking account's balance and what the elector owes it from finished rounds
   (`compute_returned_stake`).
4. Read config param 17 for the minimum and maximum stake, and config param 15 for the round
   timings.
5. Compute the spendable balance: balance plus what the elector owes, less `VAL_ACC_RESERVED`,
   50 tokens.
6. Choose the number:
   - `--amount N`: N;
   - mode `balanced`: half of the spendable balance, in whole tokens;
   - mode `single`: all of it, in whole tokens.
7. Refuse a number below the least stake: the elector's minimum plus 1 token, rounded up
   (`stake.least_stake_tokens`).
8. Above the elector's maximum, refuse a named amount and cap a mode's number.
9. Add warnings: the number cannot fund one bid yet; it funds one round at a time; the node is
   in a validator set, so its stake at the elector was not counted; part of the balance is still
   at the elector.
10. Save the number as `MSIG_FIX_STAKE` in the record and write `env.sh`.
11. The stage becomes `ready-for-elections`, from `wallet-deployed`, `imported` or
    `nominator-verified`.

**Result**

Every later bid stakes this number until `stake set` runs again. A mode is not kept: it was
arithmetic done once.

**On failure**

Nothing is saved before step 10.

## Stake report

Command: [`stake status`](../stake/status.md). Code: `stake.describe_stake`.

It reads what `stake set` reads and reports it beside the recorded number: the staking account's
balance, what the elector owes, the open election's id, the spendable balance, how many rounds it
funds, the chain's limits and timings, and a solo validator's pending transactions to the elector.
It sends nothing and does not take the election lock.

## Recover a stake

Command: [`stake recover`](../stake/recover.md). Code: `stake.recover`,
`scripts.SOLO_STAKE_RECOVER`.

A scheduled run makes the same request in its preparation
([elections.md](elections.md#preparation-solo-and-nominator)). This command makes it without
waiting for an election.

**Requires**

- Validator type `solo` or `nominator`.

**Logic**

1. Ask for confirmation and take the election lock.
2. Read what the elector owes the staking account. If it is nothing, say so and stop.
3. Read the Safe's custodians and required signatures.
4. Have the console build the recovery request (`recover_stake`).
5. Build the Safe's message:
   - solo: to the elector, with 1 token;
   - nominator: to the contract, with `NOMINATOR_OP_FEE`, 2 tokens. The contract asks the
     elector and receives the stake.
6. Submit, sign and wait for the effect
   ([wallet-operations.md](wallet-operations.md#one-operation)): the elector owes the staking
   account less than before the request, and the account holds more than before it.

**Result**

The finished rounds' stake is back in the staking account.

**On failure**

This command neither resumes nor checks the queue
([wallet-operations.md](wallet-operations.md#who-takes-which-step)). A run that fails after its
submit can leave a pending transaction to the elector or the contract. Scheduled runs stop at it
([ELECT-6](properties.md#elect-6)). Finish it with `stake confirm`; do not run `stake recover`
again while it is pending.

## Nominator report

Command: [`nominator info`](../nominator/info.md). Code: `nominator.info`.

It reads and sends nothing: the contract's state and balance, what the owner could withdraw
(the balance less `NOMINATOR_RESERVE`), what the elector owes the contract, the Safe's balance,
and every pending transaction in the Safe. The Safe pays for each instruction, so an empty Safe
stops the bidding while the contract is full.

Stake still at the elector is shown by round, without amounts:

- `open_bid`: the node's key for the open election (`node_cfg/config.json`), looked up by its
  ADNL address with the kit's `Elector_ADNL_Search`, the search the election run uses.
- `at_elector`: the published sets (p32, p34, p36) that hold one of the node's keys, matched by
  ADNL address. Each returns at the set's end plus p15 `stake_held_for`; a round past that is in
  the elector's credit instead. ever-node keeps a node's two newest keys and drops the oldest at
  the next bid, which on mainnet's p15 comes when the previous round's hold ends.
- A source that fails reads as `unknown`, for its field only.

## Owner requests

Commands: [`nominator withdraw`](../nominator/withdraw.md),
[`nominator rotate`](../nominator/rotate.md), [`nominator submit`](../nominator/submit.md). Code:
`nominator._owner_request`, `nominator.submit`.

Only the owner may withdraw from the contract or replace its hot wallet. The owner's key is not
on the server ([SEC-3](properties.md#sec-3)), so these commands work in two halves.

**Requires**

- Validator type `nominator` and a prepared contract.
- `nominator rotate`: a new hot wallet address in the masterchain.

**Logic**

1. `nominator withdraw` or `nominator rotate` builds a message *body* in the tools image and
   writes it to `elections/nominator/`. Nothing is sent, and no confirmation is asked.
   - withdraw an amount: the amount in nanotokens;
   - withdraw all: the contract's own code for "everything liquid above the reserve";
   - rotate: the new hot wallet's address.
2. The command prints the body, the contract's address, the value to attach
   (`NOMINATOR_OP_FEE`) and that bounce must be set.
3. The owner signs a message with that body on the machine that holds the owner's key.
4. `nominator submit --message FILE` asks for confirmation, takes the election lock, and sends
   the signed file through the node's console.

**Result**

`nominator submit` reports that the message was sent, not that it executed. Check
`nominator info` for the effect. A message that expired or that the owner did not sign is ignored
by the chain.

After a rotation the contract ignores this node's Safe. Stop the node's election schedule first.
