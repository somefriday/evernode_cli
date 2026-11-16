# evernode vote param

Print a config param.

## Example

```bash
evernode vote param 17
```

## What it does

Prints the param's hash, whether it is mandatory and critical, how many rounds a change must win, and its value as JSON (or as a BOC when ever-node has no JSON for it).

## Flags

| Flag         | Value | Default              | Meaning                          |
| ------------ | ----- | -------------------- | -------------------------------- |
| ID           | N     | required             | Param number.                    |
| `--endpoint` | URL   | mainnet's public     | GraphQL endpoint of the network. |

## Behaviour

- Start a `--value-json` from this output: write each field as printed.
- A number's hex field may be left out next to its `_dec` field.

## See also

- [vote propose](propose.md)
