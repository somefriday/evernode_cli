# evernode nominator prepare

Compile the single-nominator contract and derive its address.

## Example

```bash
sudo evernode nominator prepare -n validator01
```

## What it does

Builds the pinned tool image, compiles the contract, checks its code hash against the pinned one and derives the address from the code, the cold owner and this node's Safe.

## Flags

| Flag                | Value | Default                              | Meaning                                   |
| ------------------- | ----- | ------------------------------------ | ----------------------------------------- |
| `-n`, `--name`      | NAME  | required                             | The managed node.                         |
| `--contract-source` | PATH  | the contract installed with evernode | The vendored contract directory.          |
| `--new-contract`    |       | off                                  | Point a deployed node at the new address. |
| `--yes`             |       | ask                                  | Skip the confirmation prompt.             |

## Behaviour

- Nominator validators only. Sends nothing and spends nothing; works before the node is synchronized.
- Needs the Safe to exist: the address depends on it.
- Run it again after an evernode upgrade that changes the tools, or after an image prune removed the tools image: the image is rebuilt and the address stays the same.
- Once the contract is deployed, code that gives another address is refused unless `--new-contract`: the node would bid from an empty contract while its stake sits in the old one. A refused prepare leaves the contract's files as they were.
- Next: `nominator deploy` for a new contract; `nominator verify` for an imported node or a contract already deployed.
- A node whose kit was named with `--project-source` takes the `single-nominator` beside that kit when there is one. A directory named with `--contract-source` is remembered for later runs; the installed contract is not, so a later run takes the one installed then.
- Refused while a scheduled election run holds the node's lock; try again when it finishes.
- Artifacts and `manifest.json` are kept in the node's `elections/nominator/`.

## See also

- [nominator deploy](deploy.md)
