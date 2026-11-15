# evernode depool confirm

Finish a Safe transaction that is short of signatures.

## Example

```bash
sudo evernode depool info -n validator01
sudo evernode depool confirm -n validator01 --transaction-id 7706533199966052161
```

## What it does

Adds the signatures of the custodian keys held on this node to one named pending transaction.

## Flags

| Flag               | Value | Default  | Meaning                                           |
| ------------------ | ----- | -------- | ------------------------------------------------- |
| `-n`, `--name`     | NAME  | required | The managed node.                                 |
| `--transaction-id` | ID    | required | Exact id from `depool info`.                      |
| `--force`          |       | off      | Sign one that pays none of this node's contracts. |
| `--yes`            |       | ask      | Skip the confirmation prompt.                     |

## Behaviour

- Signing executes the transfer the transaction carries. The question shows its value, destination and signatures first.
- Refuses a transaction that pays none of this node's contracts (the elector, the config contract, its DePool and the DePool's proxies) unless `--force`.
- An id the Safe does not hold is refused; a pending transaction expires an hour after it was made.
- A signature the Safe has not recorded within a minute was lost and is sent again, three times at most.
- While one is pending, scheduled election runs stop before bidding.

## See also

- [depool info](info.md)
