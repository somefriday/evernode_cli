# evernode node setup

Give a created node its validator role: how it stakes, and its Safe and DePool.

## Example

```bash
sudo evernode node setup -n validator01
sudo evernode node setup -n validator01 --import-wallet --wallet-address ADDRESS --depool-address ADDRESS
```

## What it does

Asks for the validator type, a new or an existing Safe and, for a DePool validator, the DePool's terms (or takes them from flags). It shows the role, then saves it in the node's configuration and its `env.sh`. Nothing reaches the chain, and no seed phrase is asked for.

## Flags

| Flag                    | Value                       | Default                                        | Meaning                                                          |
| ----------------------- | --------------------------- | ---------------------------------------------- | ---------------------------------------------------------------- |
| `-n`, `--name`          | NAME                        | the only node                                  | The managed node; needed when the host has several.              |
| `--validator-type`      | `depool` `solo` `nominator` | menu; `depool` without a terminal              | How the validator stakes.                                        |
| `--import-wallet`       |                             | asked on a terminal                            | Use an existing Safe; its phrases are typed at `wallet recover`. |
| `--wallet-address`      | ADDRESS                     | asked with `--import-wallet`                   | Existing Safe; needs `--import-wallet`.                          |
| `--depool-address`      | ADDRESS                     | asked for an imported DePool                   | Existing DePool; needs `--import-wallet`, DePool type only.      |
| `--custodians`          | N                           | asked; `3` without a terminal; nominator: menu | Safe custodians, 1-31.                                           |
| `--required-signatures` | N                           | asked; `2` without a terminal; nominator: menu | Signatures a Safe transaction needs.                             |
| `--owner-address`       | `-1:…`                      | asked                                          | Nominator: the cold owner wallet. Cannot be changed later.       |
| `--depool-type`         | `EverX` `StEver`            | menu; `EverX` without a terminal               | DePool contract. DePool validators only.                         |
| `--validator-assurance` | TOKENS                      | asked; `50000` without a terminal              | DePool: stake the validator must hold.                           |
| `--min-stake`           | TOKENS                      | asked; `10` without a terminal                 | DePool: smallest participant stake.                              |
| `--reward-fraction`     | PERCENT                     | asked; `65` without a terminal                 | DePool: participants' share of rewards.                          |
| `--balance-threshold`   | TOKENS                      | asked; `20` without a terminal                 | DePool: its own operating balance.                               |
| `--cron-interval`       | MINUTES                     | asked; `10` without a terminal                 | Minutes between election runs; use a divisor of 60.              |
| `--workchain`           | `0` `-1`                    | menu; `0` without a terminal                   | Workchain of a DePool validator's Safe; its DePool stays in 0.   |
| `--yes`                 |                             | ask                                            | Skip the confirmation prompt.                                    |
| `--dry-run`             |                             | off                                            | Print the role and change nothing.                               |

## Behaviour

- Needs root and a node that `node create` finished; one still being created or removed is refused.
- Without a terminal, pass the flags the role needs: the type defaults to `depool`, with a new Safe.
- An existing Safe's seed phrases are typed at `wallet recover`, which checks them against the chain at once.
- Refuses a Safe or DePool another node on this host uses, and a nominator owner this host holds.
- Solo and nominator Safes are always in the masterchain; a DePool validator's Safe is in the node's workchain.
- A new nominator Safe is offered one key (also the default without a terminal) or several, which then asks for custodians and signatures.
- Can run again until the wallet setup begins: once the node holds the Safe's keys or phrases, the role is fixed.
- Prints the next command: `wallet create`, or `wallet recover` for an existing Safe.

## See also

- [node create](create.md)
- [wallet create](../wallet/create.md)
- [wallet recover](../wallet/recover.md)
