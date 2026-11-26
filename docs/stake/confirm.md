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
| `--force`          |       | off      | Sign one that pays none of this node's contracts.                |
| `--yes`            |       | ask      | Skip the confirmation prompt.                                    |

## Behaviour

- For solo, nominator and denominator validators; a DePool validator uses `depool confirm`.
- Signing executes the transfer the transaction carries. The question shows its value, destination and signatures first.
- Refuses a transaction that pays none of this node's contracts (the elector, the config contract, its nominator contract or pools) unless `--force`.
- A signature the Safe has not recorded within a minute was lost and is sent again, three times at most.
- While a request to the elector or the contract is pending, scheduled runs neither recover nor bid. It expires after an hour.

## See also

- [stake status](status.md)
