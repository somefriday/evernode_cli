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
- A transaction to the DePool is refused, even with `--force`, while one of its proxies does not read as Active: a tick or a bid there would make the DePool send to the elector through it. A frozen proxy, or one without code (uninit or gone), gets a red alarm. A withdrawal or a replenishment left pending is finished by running the same `depool withdraw` or `depool replenish` again. A stake, a tick or a bid left pending waits until the proxy is restored; until then it stops every scheduled run, and it expires an hour after it was submitted.
- A signature the Safe has not recorded within a minute was lost and is sent again, three times at most.
- While one is pending, scheduled election runs stop before bidding.

## See also

- [depool info](info.md)
