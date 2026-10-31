# Elector-ever-node.abi.json

The elector interface description that **ever-node itself** ships and uses.

| | |
|---|---|
| Source | `everx-labs/ever-node`, `src/validator/Elector.abi.json` (also at `bin/`) |
| Last upstream change | `d0084a5968a2d17c96001df4fdde9559a3a95e79`, 2023-01-26 |
| Fetched from | repository HEAD `e4068621a96d78969dd6df3087504d25b06fca6e` (node 0.60.11) |
| sha256 | `d03a21da40be24b59828031e8cb3e5ed22768aada5c8c8dd499f47fc47bedb7c` |
| ABI version | 2.2 |

It is kept **beside** `Elector.abi.json` rather than replacing it, because the
two describe different elector builds and neither is correct everywhere:

- `Elector.abi.json` is ABI 2.3 and types `credits` as `map(uint256,varuint16)`.
- this file is ABI 2.2 and types `credits` as `map(uint256,uint128)`.

On the elector deployed to the network this CLI is tested against (code hash
`883a642b8bd7595e2908a9b2519f1f2ad0109f6ad58b79d6b60d40ce52c20a36`), only
this file decodes the `get` method; the 2.3 file fails with "Failed to decode
output messages". On a chain running the other build the reverse may hold.

So the scripts do not pick one. `Resolve_Elector_ABI` in `functions.shinc`
tries each candidate against the live elector and uses whichever decodes,
which is why adding a build here is enough to support it - no code changes.
