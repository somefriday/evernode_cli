# evernode wallet send

Send tokens from the node's Safe to any address.

## Example

```bash
sudo evernode wallet send -n validator01 --address 0:ADDRESS --amount 100
```

## What it does

Sends one transfer of whole tokens from the node's Safe, signed with as many of the node's custodian keys as the Safe needs, and waits until the Safe has paid it. It never reads the address: an explorer shows whether the address kept the tokens.

## Flags

| Flag           | Value   | Default  | Meaning                                                 |
| -------------- | ------- | -------- | ------------------------------------------------------- |
| `-n`, `--name` | NAME    | required | The managed node whose Safe pays.                       |
| `--address`    | ADDRESS | required | Where to: `-1:` or `0:` and 64 hex digits.              |
| `--amount`     | TOKENS  | required | Whole tokens to send, at least 1.                       |
| `--no-bounce`  |         | off      | Keep the tokens at the address even without a contract. |
| `--yes`        |         | ask      | Skip the confirmation prompt.                           |

## Behaviour

- The same for DePool, solo, nominator and denominator validators, from either workchain to either.
- Needs a synchronized node and a Safe on chain: from `wallet deploy`, or from `wallet recover` for an existing Safe.
- Refuses while the node's election schedule can bid: run `election stop`, send, then `election start`. A denominator validator's pools' cycle, which `election stop` leaves running, waits for the send.
- Refuses the node's own accounts: the elector, the config contract, the Safe itself, the DePool and its proxies, the nominator contract, the pools.
- Keeps back 1 token for each signature the Safe needs and, in the masterchain, the storage the Safe owes since its last transaction, counted as at least 0.2 token a day; a larger amount is refused with the most it can send.
- With bounce, an address with no contract (not deployed or frozen), or one that refuses the tokens, sends them back minus fees.
- With `--no-bounce` the tokens stay at the address; a frozen address first takes the storage it owes.
- Once you answer yes, it runs to its end, within 3 minutes, or 6 for a Safe that needs more than one signature: Ctrl+C then only says so, and a second Ctrl+C stops it.
- Its result is final: after an error nothing it sent can still go out, so it can run again at once, unless the error says the state is unknown.
- After a second Ctrl+C, a killed process or a lost session, wait a minute and check the Safe in an explorer before sending again: a second run sends a second transfer.
- A transfer left waiting for signatures expires an hour after it was made; signing it with `depool confirm` or `stake confirm` and `--force` sends it.
- Next: `election start`, if the schedule was stopped for the send.

## See also

- [election stop](../election/stop.md)
- [election start](../election/start.md)
