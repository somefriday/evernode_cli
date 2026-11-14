# evernode stake recover

Ask the elector to return the stake of finished rounds now.

## Example

```bash
sudo evernode stake recover -n validator01
```

## What it does

Sends the recovery request from the Safe, signs it through and waits until the staking account's balance rises.

## Flags

| Flag           | Value | Default  | Meaning                       |
| -------------- | ----- | -------- | ----------------------------- |
| `-n`, `--name` | NAME  | required | The managed node.             |
| `--yes`        |       | ask      | Skip the confirmation prompt. |

## Behaviour

- Solo: the stake returns to the Safe. Nominator: the Safe asks the contract, and the stake returns to the contract.
- The scheduled election run does the same at every open election; this is for recovering outside one.
- Reports `recovered_nanotokens: 0` when nothing is owed, and sends nothing.

## See also

- [stake status](status.md)
