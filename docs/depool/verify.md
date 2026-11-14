# evernode depool verify

Check that an imported DePool belongs to this node's Safe.

## Example

```bash
sudo evernode depool verify -n validator01
```

## What it does

Reads the DePool's validator wallet on chain, compares it with the recovered Safe and installs the address file the election scripts use.

## Flags

| Flag           | Value | Default  | Meaning           |
| -------------- | ----- | -------- | ----------------- |
| `-n`, `--name` | NAME  | required | The managed node. |

## Behaviour

- For imported DePool validators at stage `wallet-verified`; ends at `imported`.
- Start elections only when the former node is fenced: two nodes bidding for one DePool conflict.

## See also

- [election start](../election/start.md)
