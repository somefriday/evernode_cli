# evernode stake confirm

Finish a Safe transaction that is short of signatures.

## Example

```bash
sudo evernode stake confirm -n validator01 --transaction-id 7706532551355990467
```

## What it does

Adds the signatures of the custodian keys held on this node to one named pending transaction.

## Flags

| Flag               | Value | Default  | Meaning                                                          |
| ------------------ | ----- | -------- | ---------------------------------------------------------------- |
| `-n`, `--name`     | NAME  | required | The managed node.                                                |
| `--transaction-id` | ID    | required | Exact id from `stake status` (`nominator info` for a nominator). |
| `--yes`            |       | ask      | Skip the confirmation prompt.                                    |

## Behaviour

- For solo and nominator validators; a DePool validator uses `depool confirm`.
- Signing executes the transfer the transaction carries: read its destination and value first.
- While a request to the elector or the contract is pending, scheduled runs neither recover nor bid. It expires after an hour.

## See also

- [stake status](status.md)
