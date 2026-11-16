# Elections

How a node bids in every election without an operator: the schedule, and one scheduled run step
by step. The rules are [ELECT-1](properties.md#elect-1) to [ELECT-6](properties.md#elect-6); every
transfer follows [wallet-operations.md](wallet-operations.md).

## Words and timing

- An *election* is the time in which the elector takes bids for one round. Its *election id* is
  the time the elected validator set starts, in Unix seconds. The elector reports it as
  `active_election_id`, or 0 when no election is open.
- Config param 15 holds the timings. On mainnet a round lasts 65536 s, about 18 h. An election
  opens 32768 s before the running round ends and closes 8192 s before it, so it is open for
  about 6.8 h.
- A run starts every `CRONTAB_INTERVAL` minutes, 10 unless `node setup` set another. On mainnet
  that is about 40 runs per election. On the local test network a round lasts minutes.

## The schedule

Commands: [`election start`](../election/start.md), [`election stop`](../election/stop.md),
[`election status`](../election/status.md), [`election list`](../election/list.md). Code:
`cli.main`, `elections.py`.

**Requires**, for `election start` ([SETUP-6](properties.md#setup-6)):

- A role, and setup stage `imported` or `ready-for-elections`.
- For a solo or nominator validator, a recorded stake.
- A synchronized node. A server clock that is late but within 10 s is a warning.
- For a DePool validator, both proxies read as Active ([ELECT-8](properties.md#elect-8)).
- For a nominator validator, the tools image that `nominator prepare` builds is on the host: a
  bid splices its stake with it, and an image prune removes it.
- No schedule installed yet.

**Logic** of `election start`:

1. Check the requirements. `--dry-run` ends here. Ask.
2. Under the node lock, write the two programs (`elections.write_election_programs`):
   - the *sequence*: `run-election-sequence.sh` for a DePool validator,
     `run-solo-election-sequence.sh` for a solo or nominator validator. The other type's file is
     removed if it is there. A sequence writes a mark into the cron log when a run starts, and
     one with its exit status when it ends, each on a line of its own. A run killed by a signal
     (HUP, INT, TERM) ends with 128 plus the signal; one killed outright leaves no end mark;
   - the *runner*, `run-elections.sh`.
3. Write `/etc/cron.d/evernode-NAME`: every N minutes, as root, the runner, with its output
   appended to `logs/validator/election-cron.log`.

Each file is written under a temporary name and renamed
([STATE-2](properties.md#state-2)). `node sync-scripts` writes the two programs again for a node
whose schedule is installed, so they follow the installed evernode.

`election stop`, under the node lock, removes the cron file. It refuses a file that does not start
with `# Managed by evernode`. It does not end a run that is in progress.

`election status` and `election list` report two things separately and never one from the other:

- the schedule: whether the cron file is installed, and whether a cron daemon runs on the host;
- the chain: whether the node reports itself in the current set and in the next set, and whether
  it is validating;
- the latest run, read back between its marks (`elections.last_run`): when it started and ended,
  its exit status, what it came to (`no election open`, `already bid in this election`, or a stop
  at a half-signed transaction in the Safe, whose kit line is listed with the errors), its last
  error lines, and each proxy alarm it raised. The kit exits 1 on the first two, and prints "There
  is no elections now" as an error on solo and nominator nodes; neither is listed as an error.
  `election status` shows a proxy alarm again, in red.

**Result**

Cron starts a run every N minutes until `election stop`, `node stop` or `node remove`
([NODE-5](properties.md#node-5)).

## One run

```
cron ─ run-elections.sh ─ election lock ─ sequence ─┬─ prepare_elections.sh
                                                    └─ take_part_in_elections.sh
```

1. The runner takes the node's election lock without waiting and starts the sequence. If the
   lock is held it writes a `skipped` line and ends with status 200
   ([LOCK-5](properties.md#lock-5)).
2. The sequence runs the kit's two scripts from the node's `scripts/` directory.

The two sequences differ in what a failed preparation means
([ELECT-5](properties.md#elect-5)).

**DePool validator** (`elections._depool_sequence_program`):

1. Run `prepare_elections.sh`. Any status but 0 ends the run.
2. Wait 120 s.
3. Run `take_part_in_elections.sh`.

**Solo or nominator validator** (`elections._solo_sequence_program`):

1. Run `prepare_elections.sh` and keep its output in `elections/last-preparation.log`.
2. Status 3: say that a pending request stopped it. Any other status but 0: write a warning,
   unless the output says the node already took part or no election is open.
3. If the output shows a recovered amount above 0, wait 120 s for it to arrive. A test network
   may shorten the wait with `ELECTION_SETTLE_SECONDS`.
4. Run `take_part_in_elections.sh`, whatever step 1 returned.

Both scripts also hold a lock of their own, so a second copy of the same script ends at once.

## Preparation

Code: `prepare_elections.sh`. Preparation gets back what earlier rounds released and, for a DePool
validator, moves the DePool into the open election.

**Logic**, common start:

1. Require the node ([NODE-1](properties.md#node-1)), with a limit of 20 s.
2. Read the election id. The working directory is `elections/ID/`, or `elections/` when no
   election is open.
3. If `ID_elector-confirmed-bid.txt` exists, end with status 1: the node has bid in this election.
4. Read the Safe. It must be readable and hold at least 2 tokens.
5. Read the Safe's custodians and required signatures.
6. Read the elector's address and type.
7. Read the Safe's pending transactions. End with status 3 or 4 at a pending transaction to the
   run's destination ([ELECT-6](properties.md#elect-6)).

A failed read in steps 4 to 7 ends preparation with status 1
([OP-8](properties.md#op-8)).

### Preparation, solo and nominator

The *staking account* is the Safe for a solo validator and the contract for a nominator
validator.

8. With no election open, end with status 1.
9. Ask the elector what it owes the staking account (`compute_returned_stake`). Nothing owed:
   end with status 0.
10. Have the console build the recovery request.
11. Build the Safe's message: to the elector with 1 token (solo), or to the contract with
    `NOMINATOR_OP_FEE` (nominator).
12. Follow the protocol ([wallet-operations.md](wallet-operations.md#one-operation)). The
    deadline is now plus `Signing_Window`, and at most 5 s before the transaction expires.
13. Wait for the effect: the staking account's balance grows by at least half the amount owed
    (`Recovery_Target`). The elector returns the stake in one message, and what the account pays
    meanwhile (the value sent, fees, the storage it owes) never comes near half of it. The effect
    gets at least 10 s.

### Preparation, DePool

8. Read both proxies, then judge each ([ELECT-8](properties.md#elect-8)):
   - Frozen, or without code (read without error as nonexistent or uninit): show its alarm and
     raise an alert.
   - Not readable, or in another state: log it and raise an alert. The next run reads it again.

   Any of these ends preparation with status 1, before the tick. Then each proxy whose balance,
   less its storage owed, is under 1.92 tokens gets 5 tokens plus the storage owed in whole tokens
   from the Safe, in a transfer that does not bounce, and preparation waits for its balance to
   show the old one plus 4 tokens ([ELECT-7](properties.md#elect-7)). One that owes more than 50
   tokens gets nothing. A top-up that fails is logged and raises an alert; if the proxy owes more
   storage than it holds, preparation ends with status 1, before the tick, and the next run tries
   again. Otherwise preparation goes on.
9. Read the DePool's own balance (`getDePoolBalance`). If it is below the DePool's balance
   threshold less 3 tokens, send the difference plus 10 tokens from the Safe. Follow the protocol
   ([wallet-operations.md](wallet-operations.md#one-operation)) and wait for the balance to show
   them. A failure ends preparation.
10. With no election open, end with status 1. Steps 8 and 9 have still run.
11. If the DePool's round already carries this election's id, end with status 0.
12. Send the *tick*: 1 token to the DePool with the tick payload. Follow the protocol, with the
    deadline as in the solo case.
13. Wait for the effect: one of the DePool's rounds carries the election's id. The effect gets at
    least 60 s after the last signature.

**Result**

- Solo, nominator: the released stake is in the staking account.
- DePool: both proxies and the DePool can pay for their work, and the DePool has entered the
  election.

**On failure**

Preparation ends with a status other than 0 and the sequence decides what follows. The next run
starts from the top.

## The bid

Code: `take_part_in_elections.sh`.

**Requires**

- An open election.
- A Safe that is active and holds at least 2 tokens.
- For a solo or nominator validator, a Safe in the masterchain; for a nominator validator, a
  contract address in the masterchain ([SETUP-4](properties.md#setup-4)).

**Logic**

1. Read the Safe, its custodians and required signatures, and require the node, with a limit of
   20 s. A failed read ends the run with status 1.
2. Read the election id. With no election open, end with status 1.
3. If `ID_elector-confirmed-bid.txt` exists, end with status 1: nothing is left to do in this
   election.
4. DePool validator only:
   1. The DePool must name this Safe as its validator wallet.
   2. Write the DePool's two proxies to `keys/proxy0.addr` and `keys/proxy1.addr`.
   3. The DePool's round must carry this election's id. If it does not, run
      `prepare_elections.sh` once more. If it still does not, raise an alert and end with status 1.
   4. The round's proxy is proxy 0 or 1: the round's id modulo 2.
5. **Has the node bid already?** ([ELECT-2](properties.md#elect-2)) Take the newest validator key
   in the node's key ring and look for its ADNL address in the elector's participant list
   (`Elector_ADNL_Search`):
   - the key ring is empty: the node never bid. Go on;
   - found: write `ID_elector-confirmed-bid.txt` and end with status 0;
   - not found: go on;
   - the list cannot be decoded by any bundled interface description: if `ID_query.boc` exists,
     the node has bid, so end with status 0; if not, go on and remember that the elector is
     unreadable;
   - anything else, such as a read that failed or an election that closed: end with status 1.
6. If `ID_bid-stopped.txt` exists, end with status 0 ([ELECT-4](properties.md#elect-4)).
7. Read the Safe's pending transactions and append their counts to `transactions.log`. End with
   status 3 or 4 at a pending transaction to the run's destination
   ([ELECT-6](properties.md#elect-6)).
8. Read config param 15.
9. **The request** ([ELECT-1](properties.md#elect-1)). If `ID_query.boc` exists, use it.
   Otherwise:
   1. Write the address that stakes into `console.json` as `wallet_id`: the round's proxy
      (DePool), the Safe (solo) or the contract (nominator). The file is replaced only if the new
      one still holds the console's key ([STATE-2](properties.md#state-2)).
   2. Run the console's `election-bid`. It makes a new validator key and ADNL key for this
      election in the node's key ring and writes the signed request to `ID_query.boc`.
10. **The transfer**, by type:

    | Type      | Destination | Value                       | Payload                         | Checked first                              |
    | --------- | ----------- | --------------------------- | ------------------------------- | ------------------------------------------ |
    | solo      | the elector | `MSIG_FIX_STAKE` tokens     | the request                     | the Safe holds more whole tokens than the stake |
    | nominator | the contract | `NOMINATOR_OP_FEE`, 2 tokens | the request with the stake written in, `ID_stake.boc` | the contract is active and holds the stake plus `NOMINATOR_RESERVE`; the Safe holds the fee plus 1 token |
    | DePool    | the DePool  | 1 token                     | the request                     |                                            |

    The nominator's payload is built by `tools/cells.cjs` in the tools image, with no network.
    A check that fails ends the run with status 1. No bid was sent, so it is not counted as an
    attempt.
11. Build the Safe's message, `ID_vaidator-query-msg.boc`, and submit it
    ([wallet-operations.md](wallet-operations.md#one-operation)), with resume and the queue check.
12. **Sign**, when the Safe needs more than one signature. The deadline is the later of now plus
    `Signing_Window` and the election's close, and at most 5 s before the transaction expires.
    - Signed through: go on.
    - The transaction needs a key this node does not hold, and the node holds fewer key files
      than signatures required: say which transaction must be signed elsewhere, and end with
      status 0.
    - Any other failure: end with status 1.
13. **The verdict** ([ELECT-3](properties.md#elect-3)). Read this election's ADNL address from
    the key ring (`Get_Engine_ADNL_For_Election`). Then, for two message lifetimes, 120 s, ask the
    elector every 10 s whether it lists that address.
    - Listed: write `ID_elector-confirmed-bid.txt` and end with status 0.
    - Not listed: step 14.
    - The elector was unreadable in step 5: step 15.
14. **Not registered** ([ELECT-4](properties.md#elect-4)). Add a line to `ID_bid-attempts.txt`.
    At 3 lines write `ID_bid-stopped.txt`. A DePool validator whose DePool holds no stake in the
    round being elected gets `ID_bid-stopped.txt` at once: no retry can change that. A nominator
    validator's message reports the contract's balance, which shows whether the stake came back.
    Raise an alert and end with status 1.
15. **Unreadable elector.**
    - DePool validator: say that the bid was sent and not confirmed. End with status 0.
    - Solo or nominator validator: wait up to 120 s for the staking account's balance to fall by
      the stake, with 2 tokens of slack. Fallen: say that the stake left and is not confirmed,
      and end with status 0. Not fallen: the elector did not take the stake; raise an alert and
      end with status 1.

**Result**

The elector lists the node for the open election. `ID_elector-confirmed-bid.txt` keeps every later
run in this election from asking or bidding again. Whether the node is then elected is the
elector's decision; `election status` shows it once the next set is published.

**On failure**

- Before step 11 nothing was sent. The next run tries again.
- After step 11 the next run decides from the chain: step 5 finds a bid that registered late,
  step 7 stops at a transaction left pending, and otherwise the same request is sent again, three
  times at most.
- A run that ends with status 3 or 4 needs `stake confirm` or `depool confirm`, or an hour for
  the transaction to expire.

## Exit statuses

A run's status is the last script's. Status 1 is also how a script says "nothing to do".

| Status | `prepare_elections.sh`                                         | `take_part_in_elections.sh`                                     |
| ------ | -------------------------------------------------------------- | --------------------------------------------------------------- |
| 0      | done, or nothing was owed, or the DePool was already set       | the bid is registered, was registered before, is stopped, was sent to an unreadable elector, or waits for a signature elsewhere |
| 1      | a failure; no election open; the node already bid              | a failure; no election open; the node already bid; the bid did not register |
| 3      | solo, nominator: a pending transaction to the elector or the contract | the same                                                 |
| 4      | DePool: a pending transaction to the DePool                    | DePool: a pending transaction to the DePool or a proxy          |

The runner itself ends with status 200 when it skipped the run.

## What a run leaves

- Its whole output in `logs/validator/election-cron.log`.
- The election's files in `elections/ID/` ([state.md](state.md#elections-directory)): the log of
  decisions, the request, the messages and the three markers.
- Lines that start with `###-ERROR` are failures. The scripts also send these as alerts to
  Telegram when `env.sh` names a bot and a chat; evernode sets neither. `election status` shows
  the latest run's.
