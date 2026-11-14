# evernode vote burn-key

Print the key a burn installs.

## Example

```bash
evernode vote burn-key
```

## What it does

Derives the key from a public label and shows that it is not a point on the curve, so no signature can ever verify against it.

## Flags

| Flag      | Value | Default                   | Meaning                              |
| --------- | ----- | ------------------------- | ------------------------------------ |
| `--label` | TEXT  | `it is a fight club baby` | Public text the key is derived from. |

## Behaviour

- Offline: needs no endpoint, node or root.
- The same label gives the same key on every machine.

## See also

- [vote propose](propose.md)
