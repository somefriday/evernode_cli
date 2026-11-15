# Operations from the Safe

How any transfer from a node's Safe is built, sent, signed and judged. Every stake operation, the
recovery, the DePool tick and the bid follow this one protocol. Its code is
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
   5. Look one last time after the third pause. The message has expired by then, so "not found"
      is final: nothing was submitted.
5. **Sign** (`Sign_To_Execution`, [OP-6](properties.md#op-6)), with more than one signature
   required. Until the deadline:
   1. Read the pending entry by its id. A read that fails is repeated.
   2. The entry is gone: the transaction executed. Go to step 6.
   3. Find this node's key files that have not signed, from the entry's confirmation mask and
      the Safe's custodian list (`Unsigned_Local_Keys`). None left while signatures are missing:
      return 3, the transaction needs a key this node does not hold.
   4. Send one confirmation, signed with the unsigned key of the lowest custodian index.
   5. Wait up to 70 s, and never past the deadline, until the wallet shows that signature or the
      entry is gone (`Await_Signature_Recorded`).
   6. Shown: take the next key. Gone: executed. Neither: send the confirmation again, three
      times at most, and only while a whole lifetime remains before the deadline. Otherwise fail.
6. **Await the effect** (`Await_Effect`, [OP-2](properties.md#op-2)). Ask the operation's own
   question of the chain, at once and then every 2 s, until it holds or the deadline passes.

**Result**

The operation's effect is on chain. The command reports success only then.

**On failure**

- A failure is reported only when nothing sent can still execute
  ([OP-3](properties.md#op-3)).
- With one signature required, a failure means the transfer did not happen. Run the command
  again.
- With more, a failure after step 4 can leave a pending transaction with some signatures. It
  shows in `depool info`, `stake status` or `nominator info`. Finish it with
  [`depool confirm`](../depool/confirm.md) or [`stake confirm`](../stake/confirm.md), or let it
  expire after an hour. An operation that resumes finishes it when it runs again.
- The final message says which case it was: the effect was never seen, or the chain could not be
  read at all.

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
| Recover a stake                        | The staking account's balance is at least the old one plus the stake, less the value sent and 1 token. |
| Fund the nominator deployer            | The deployer's balance is at least the deploy value.                   |
| Tick the DePool                        | One of the DePool's rounds carries the election's id.                  |
| Bid                                    | The elector lists the node's ADNL address ([ELECT-3](properties.md#elect-3)). |

## Who takes which step

Every caller builds, submits, signs and awaits the effect in the same way. Two steps differ.

| Operation                         | Code                                    | Resumes (step 2) | Checks the queue (step 3) |
| --------------------------------- | --------------------------------------- | ---------------- | ------------------------- |
| The five DePool operations        | `depool_functions/*.shinc`              | yes              | yes                       |
| Funding the nominator deployer    | `scripts.NOMINATOR_FUND_DEPLOYER`       | yes              | yes                       |
| `stake recover`                   | `scripts.SOLO_STAKE_RECOVER`            | no               | no                        |
| Proxy top-up, scheduled           | `top_app_account` in `functions.shinc`  | yes              | no                        |
| DePool replenishment, scheduled   | `prepare_elections.sh`                  | no               | yes                       |
| Recovery, scheduled               | `prepare_elections.sh`                  | yes              | yes                       |
| DePool tick                       | `prepare_elections.sh`                  | yes              | yes                       |
| Bid                               | `take_part_in_elections.sh`             | yes              | yes                       |

- The last three stop earlier if a transaction to their destination is already pending
  ([ELECT-6](properties.md#elect-6)). Their resume step meets only a transaction that arrived
  during the run.

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
2. Name the node's own contracts: the elector and the config contract, then the DePool and its
   proxies, or the nominator contract.
3. If the transaction pays none of them, stop unless `--force`. With `--force`, a DePool that
   cannot be read does not stop it either.
4. Ask, showing the value, the destination and whose contract it is, and the signatures it holds.
   `--yes` skips the question but not step 3.
5. Take the election lock.
6. Read the pending list again. If it cannot be read, or the transaction is gone, stop.
7. Set the deadline: now plus `Signing_Window` for the transaction's own number of signatures,
   and at most 5 s before the transaction expires. An expired transaction is refused.
8. Sign as in step 5 above.

**Result**

The transaction has executed. Its transfer is still an internal message that the destination can
refuse, so check the result with `depool info`, `stake status` or `nominator info`.

**On failure**

The transaction keeps the signatures it got. Run the command again before it expires.
