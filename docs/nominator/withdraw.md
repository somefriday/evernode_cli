# evernode nominator withdraw

Prepare an owner withdrawal for signing elsewhere.

## Example

```bash
sudo evernode nominator withdraw -n validator01 --amount 1000
```

## What it does

Builds the message body and prints it with the destination, the value to attach and what it authorizes. Nothing is signed or sent.

## Flags

| Flag           | Value  | Default  | Meaning                                        |
| -------------- | ------ | -------- | ---------------------------------------------- |
| `-n`, `--name` | NAME   | required | The managed node.                              |
| `--amount`     | TOKENS |          | Withdraw this much to the owner.               |
| `--all`        |        |          | Withdraw everything above the 1-token reserve. |

## Behaviour

- One of `--amount` and `--all` is required.
- The owner signs a transfer of the printed value to the contract with this body, bounce on, and hands the signed message to `nominator submit`.
- An amount above the liquid balance is clamped by the contract, not refused. Frozen stake cannot be withdrawn.
- The owner key is never read, stored or asked for.

## See also

- [nominator submit](submit.md)
- [nominator link](link.md)
