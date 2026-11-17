# Operations from the Safe

How any transfer from a node's Safe is built, sent, signed and judged. Every stake operation, the
recovery, the DePool tick, the bid and `wallet send` follow this one protocol. Its code is
`scripts/managed_ops.shinc` in the kit; its rules are [OP-1](properties.md#op-1) to
[OP-9](properties.md#op-9).

## What the Safe does

The protocol relies on these facts of the SafeMultisigWallet contract.

- A Safe that requires one signature executes `submitTransaction` at once. Nothing is left
  pending.
- A Safe that requires more keeps a *pending transaction* with the submitter's signature. Each
  `confirmTransaction` adds one signature. The signature that completes the count executes the
  transfer and removes the entry.
- A pending transaction's id holds its creation time: the time in the high 32 bits, the low 32
  bits of the logical time below.
- A pending transaction expires 3600 s after it was made. The kit reads the value from the
  wallet (`Get_MSIG_Expiration_Time`) and falls back to 3600.
- One custodian may have 5 live pending transactions. A sixth is refused.
- The Safe makes every check before it accepts a message. A refused message is in no block, so
  the chain never says why it failed.
- The transfer a transaction carries is a separate internal message. The destination can still
  refuse it after the Safe has executed.

## Numbers

| Name                        | Value | Meaning                                                      |
| --------------------------- | ----- | ------------------------------------------------------------ |
| `MSG_LIFETIME`              | 60 s  | How long every signed message lives ([OP-1](properties.md#op-1)). |
| `LC_Send_MSG_Timeout`       | 10 s  | Pause after a message is handed to the node.                 |
| `Signing_Window`            | 120 s, or 240 s | An operation's own patience: two lifetimes for a Safe with one signature, four for more. |
| `EFFECT_POLL_INTERVAL`      | 2 s   | Time between two looks at the chain.                         |
| `EFFECT_MIN_WAIT`           | 10 s  | The least time an effect gets after the last signature.      |
| `MSIG_MAX_QUEUED_REQUESTS`  | 5     | The Safe's limit per custodian.                              |

The first two are set in `env.sh`. The others are in `managed_ops.shinc`.

## One operation

**Requires**

- A node that answers and is synchronized ([NODE-1](properties.md#node-1)).
- A readable Safe: its custodians and its required signatures
  ([OP-8](properties.md#op-8)).
- Whatever the operation itself needs, such as a balance.

**Logic**

1. **Build.** Make one `submitTransaction` message for the Safe: destination, value, bounce
   flag and payload. Sign it with the first custodian's key file. It lives 60 s.
2. **Resume.** With more than one signature required, look for the same transaction already
   pending (`Find_Matching_Pending_Trans`, [OP-4](properties.md#op-4)):
   - one match: use its id and skip steps 3 and 4 ([OP-5](properties.md#op-5));
   - more than one: refuse;
   - the Safe cannot be read: stop.
3. **Queue.** With more than one signature required, stop if this key has 5 live transactions
   (`Require_Queue_Space`, [OP-7](properties.md#op-7)).
4. **Submit and identify** (`Submit_And_Identify`):
   1. Hand the message to the node and wait 10 s. A node that does not acknowledge it is a
      warning, not a verdict.
   2. With one signature required, return now. There is no transaction to find; step 6 decides.
   3. Look for the matching pending transaction made since this attempt began, less 60 s for
      clock difference.
   4. Found: return its id. Two found: refuse. Not found: wait 10 s more and send the same
      message again. It is sent three times at most.
   5. Look one last time once the message's lifetime and 10 s more have passed since the first
      send. It cannot execute after that, so "not found" is final: nothing was submitted. A last
      look that cannot read the wallet decides nothing: the state is unknown, and an operation
      run again finishes a submission it finds ([OP-8](properties.md#op-8)).
5. **Sign** (`Sign_To_Execution`, [OP-6](properties.md#op-6)), with more than one signature
   required. Until the deadline:
   1. Read the pending entry by its id. A read that fails is repeated.
   2. The entry is gone: the transaction executed. Go to step 6.
   3. Find this node's key files that have not signed, from the entry's confirmation mask and
      the Safe's custodian list (`Unsigned_Local_Keys`). None left while signatures are missing:
      return 3, the transaction needs a key this node does not hold.
   4. Send one confirmation, signed with the unsigned key of the lowest custodian index, only
      while its lifetime and 10 s for its block remain before the deadline. Otherwise fail, and
      send nothing ([OP-3](properties.md#op-3)).
   5. Wait up to 70 s, and never past the deadline, until the wallet shows that signature or the
      entry is gone (`Await_Signature_Recorded`).
   6. Shown: take the next key. Gone: executed. Neither: send the confirmation again, under
      the rule in step 4, three times at most. Otherwise fail.
6. **Await the effect** (`Await_Effect`, [OP-2](properties.md#op-2)). Ask the operation's own
   question of the chain, at once and then every 2 s, until it holds or the deadline passes.

**Result**

The operation's effect is on chain. The command reports success only then.

**On failure**

- A failure is reported only when nothing sent can still execute
  ([OP-3](properties.md#op-3)).
- With one signature required, the transfer happens at the submit, so a failure says only that
  its effect was not seen. Check the result before running the command again.
- With more, a failure after step 4 can leave a pending transaction with some signatures. It
  shows in `depool info`, `stake status` or `nominator info`. Finish it with
  [`depool confirm`](../depool/confirm.md) or [`stake confirm`](../stake/confirm.md), or let it
  expire after an hour. An operation that resumes finishes it when it runs again.
- The final message says which case it was: the effect was not seen, or the chain did not answer
  the last look, which leaves the state unknown. Neither advises a rerun: a transfer that executed
  can still take effect.

## Deadlines

`Complete_And_Await` runs steps 5 and 6 for an operator's command. Its deadline is the earlier of:

- the transaction's expiry: its creation time plus the Safe's expiration time;
- now plus `Signing_Window`.

A transaction that has already expired is not signed. Its effect is checked once, because an
earlier attempt may have completed it.

After the last signature the effect gets at least 10 s, whatever signing left over.

The election scripts set their own deadlines for steps 5 and 6. They are in
[elections.md](elections.md).

## Effects

Each operation is judged by one question (`Effect_` functions). Every question has three
answers: yes, not yet, and "the chain could not be read". A question becomes true once and stays
true.

| Operation                              | The effect that proves it                                              |
| -------------------------------------- | ---------------------------------------------------------------------- |
| Stake into the DePool                  | The Safe's stake in the DePool is at least the old stake plus the amount. |
| Withdraw part                          | The DePool records a pending withdrawal of exactly the amount.         |
| Withdraw all                           | The DePool's reinvest flag for the Safe is off.                        |
| Cancel a withdrawal                    | No withdrawal is recorded and the reinvest flag is on.                 |
| Replenish the DePool                   | The DePool's own balance is at least the old one plus the amount, less the fee. |
| Top up a proxy                         | The proxy's balance is at least the old one plus 4 tokens ([ELECT-7](properties.md#elect-7)). |
| Recover a stake                        | The elector owes the staking account less than before, and its balance is above the old one. |
| Fund the nominator deployer            | The deployer's balance is at least the deploy value.                   |
| Tick the DePool                        | One of the DePool's rounds carries the election's id.                  |
| Bid                                    | The elector lists the node's ADNL address ([ELECT-3](properties.md#elect-3)). |
| Send to an address                     | The Safe's balance is below the one before the send.                   |

## Who takes which step

Every caller builds, submits, signs and awaits the effect in the same way. Two steps differ.

| Operation                         | Code                                    | Resumes (step 2) | Checks the queue (step 3) |
| --------------------------------- | --------------------------------------- | ---------------- | ------------------------- |
| The five DePool operations        | `depool_functions/*.shinc`              | yes              | yes                       |
| Funding the nominator deployer    | `scripts.NOMINATOR_FUND_DEPLOYER`       | yes              | yes                       |
| `stake recover`                   | `scripts.SOLO_STAKE_RECOVER`            | no               | no                        |
| Proxy top-up                      | `top_app_account` in `functions.shinc`  | yes              | no                        |
| DePool replenishment, scheduled   | `prepare_elections.sh`                  | no               | yes                       |
| Recovery, scheduled               | `prepare_elections.sh`                  | yes              | yes                       |
| DePool tick                       | `prepare_elections.sh`                  | yes              | yes                       |
| Bid                               | `take_part_in_elections.sh`             | yes              | yes                       |
| `wallet send`                     | `scripts.SAFE_SEND`                     | no               | yes                       |

- The DePool tick, the bid and the scheduled recovery stop earlier if a transaction to their
  destination is already pending ([ELECT-6](properties.md#elect-6)). Their resume step meets only
  a transaction that arrived during the run.

## Confirm by hand

Commands: [`depool confirm`](../depool/confirm.md), [`stake confirm`](../stake/confirm.md). Code:
`setup_actions._review_transaction`, `wallets.require_confirmable`,
`wallets.confirm_wallet_transaction`, `Sign_Trans.sh`.

**Requires**

- A DePool validator for `depool confirm`; a solo or nominator validator for `stake confirm`.
- A setup stage of `depool-deployed`, `imported` or `ready-for-elections`.
- A transaction id of digits only.

These are checked before anything is read.

**Logic**

1. Read the Safe's pending list. If it holds no transaction with that id, stop. Nothing is sent
   ([OP-9](properties.md#op-9)).
2. If the transaction pays the DePool, both proxies must read as Active, with or without
   `--force` ([ELECT-8](properties.md#elect-8)): a tick or a bid left pending would make the
   DePool send to the elector through them. The refusal says that a withdrawal or a
   replenishment left pending is finished by running its own command again
   ([OP-5](properties.md#op-5)).
3. Name the node's own contracts: the elector and the config contract, then the DePool and its
   proxies, or the nominator contract.
4. If the transaction pays none of them, stop unless `--force`. With `--force`, a DePool that
   cannot be read does not stop it either.
5. Ask, showing the value, the destination and whose contract it is, and the signatures it holds.
   `--yes` skips the question but not steps 2 and 4.
6. Take the election lock.
7. Read the pending list again. If it cannot be read, or the transaction is gone, stop.
8. Set the deadline: now plus `Signing_Window` for the transaction's own number of signatures,
   and at most 5 s before the transaction expires. An expired transaction is refused.
9. Sign as in step 5 above.

**Result**

The transaction has executed. Its transfer is still an internal message that the destination can
refuse, so check the result with `depool info`, `stake status` or `nominator info`.

**On failure**

The transaction keeps the signatures it got. Run the command again before it expires.

## Send to an address

Command: [`wallet send`](../wallet/send.md). Code: `setup_actions._review_send`,
`setup_actions._own_accounts`, `wallets.plan_send`, `wallets.wallet_send`, `scripts.SAFE_SEND`,
`scripts.send_from_safe`.

The send is kept apart from everything else the node does: it never runs beside the node's
election schedule, and it never pays an account that only the node's own commands pay.

**Requires**

- A Safe on chain: any setup stage but `preparing`, `node-started` and `wallet-created`.
- An address in workchain -1 or 0, and whole tokens from 1 to 10^9.
- No election schedule installed: no `/etc/cron.d/evernode-NAME`.
- An address that is none of the node's own accounts (step 3).

**Logic**

1. Check the stage, the address and the amount (`wallets.plan_send`). Nothing is read yet. The
   address is used in lower case from here on.
2. Refuse if the node's election schedule is installed.
3. Name the node's own accounts (`setup_actions._own_accounts`) and refuse the address if it is
   one of them:
   - the elector, the config contract and the Safe itself;
   - a nominator validator's contract, once `nominator prepare` has recorded its address;
   - a DePool validator's DePool, once `depool prepare` has made one. If the DePool's address
     reads as `Nonexist` or `Uninit`, the DePool has no proxies yet. Otherwise its proxies are
     read the way `depool confirm` reads them (`setup_actions._own_contracts`).

   A read that fails refuses the send.
4. Ask, showing the amount, the Safe, the address and whether the transfer bounces. `--yes`
   skips the question but no check.
5. Take the election lock.
6. Make the checks of step 1 again.
7. From here the send runs to its end (`wallets.wallet_send`): the first Ctrl+C only prints that
   it finishes within 3 minutes, or 6 with more than one signature required; a second one stops
   it. The program runs in its own session, so Ctrl+C reaches evernode alone.
8. Read the Safe. Stop, sending nothing, if it cannot be read, is not active, or holds less than
   the amount plus the reserve, which names the most it can send:
   - 1 token for each signature the Safe requires: each is a message it pays for;
   - in the masterchain, the storage the Safe owes since its `last_paid`, which its balance does
     not show yet: never less than 0.2 token a day, and more when a Safe counted as 50,000 bits
     and 100 cells costs more at the current p18 masterchain prices. At mainnet's prices that
     Safe costs 0.132 token a day and a real one 0.09, so 0.2 is what counts. A `last_paid` that
     cannot be read stops it.

   The Safe sends with "ignore errors": a transfer it cannot pay is dropped while it still pays
   its fees, which step 12 would take for the transfer.
9. Build one transfer to the address, with an empty payload and the bounce flag on unless
   `--no-bounce` ([one operation](#one-operation), step 1).
10. Resume nothing: step 2 is skipped. With more than one signature required, check the queue
    (step 3).
11. Submit, identify and sign (steps 4 and 5). The identification looks at entries made in the
    last 60 s, so a run within a minute of an identical entry left waiting can sign that entry
    instead of its own, or refuse when it sees both. Either way at most one transfer goes out.
12. Await the effect (step 6): the Safe's balance is below the one read in step 8.

**Result**

The Safe has paid the transfer. The destination is not read. With bounce, a destination with no
contract, or one that refuses the transfer, sends the value back minus fees; the Safe's balance
stays below the one before, because the fees are not returned. The node record does not change.

**On failure**

- A failure in steps 1 to 10 sends nothing.
- A failure the program reports itself is final ([OP-3](properties.md#op-3)): nothing it sent
  can still go out, so the send can run again at once. Only a line that says the state is
  unknown leaves the outcome open.
- The kit's own lines are shown, and the whole output is in
  `logs/validator/wallet-send-failed-TIME.log`.
- A send stopped from outside (a second Ctrl+C, a killed program, a lost session) may still have
  a message in flight: wait a minute and check the Safe in an explorer, since a second run sends a
  second transfer.
- With more than one signature required, a transaction left short of signatures is not sent by
  any run: a scheduled run stops only at a transaction to its own destination
  ([ELECT-6](properties.md#elect-6)), and `depool confirm` or `stake confirm` signs it only with
  `--force`, which sends it. Its message says so. It expires an hour after it was made, and until
  then counts toward the submitting key's limit of 5 pending transactions.
