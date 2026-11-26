# evernode node setup

Give a created node its validator role: how it stakes, and its Safe and DePool or pools.

## Example

```bash
sudo evernode node setup -n validator01
sudo evernode node setup -n validator01 --import-wallet --wallet-address ADDRESS --depool-address ADDRESS
```

## What it does

Asks for the validator type, a new or an existing Safe and, for a DePool validator, the DePool's terms (or takes them from flags). It shows the role, then saves it in the node's configuration and its `env.sh`. Nothing reaches the chain, and no seed phrase is asked for.

## Flags

| Flag                         | Value                                     | Default                                        | Meaning                                                          |
| ---------------------------- | ----------------------------------------- | ---------------------------------------------- | ---------------------------------------------------------------- |
| `-n`, `--name`               | NAME                                      | the only node                                  | The managed node; needed when the host has several.              |
| `--validator-type`           | `depool` `solo` `nominator` `denominator` | menu; `depool` without a terminal              | How the validator stakes.                                        |
| `--import-wallet`            |                                           | asked on a terminal                            | Use an existing Safe; its phrases are typed at `wallet recover`. |
| `--wallet-address`           | ADDRESS                                   | asked with `--import-wallet`                   | Existing Safe; needs `--import-wallet`.                          |
| `--depool-address`           | ADDRESS                                   | asked for an imported DePool                   | Existing DePool; needs `--import-wallet`, DePool type only.      |
| `--custodians`               | N                                         | asked; `3` without a terminal; nominator: menu | Safe custodians, 1-31.                                           |
| `--required-signatures`      | N                                         | asked; `2` without a terminal; nominator: menu | Signatures a Safe transaction needs.                             |
| `--owner-address`            | `-1:…` or `0:…`                           | asked                                          | Nominator: the cold owner wallet. Cannot be changed later.       |
| `--depool-type`              | `EverX` `StEver`                          | menu; `EverX` without a terminal               | DePool contract. DePool validators only.                         |
| `--validator-assurance`      | TOKENS                                    | asked; `50000` without a terminal              | DePool: validator's own stake, at least the minimum stake.       |
| `--min-stake`                | TOKENS                                    | asked; `10` without a terminal                 | DePool: smallest participant stake, at least 1 token.            |
| `--reward-fraction`          | PERCENT                                   | asked; `65` without a terminal                 | DePool: participants' share of rewards, 1 to 99.                 |
| `--balance-threshold`        | TOKENS                                    | asked; `20` without a terminal                 | DePool: its own operating balance.                               |
| `--cron-interval`            | MINUTES                                   | asked; `10` without a terminal                 | Minutes between election runs; use a divisor of 60.              |
| `--workchain`                | `0` `-1`                                  | menu; `0` without a terminal                   | Workchain of a DePool validator's Safe; its DePool stays in 0.   |
| `--pools`                    | `1` `2`                                   | menu; `2` without a terminal                   | Denominator: pools; 2 bid in alternate elections.                |
| `--pool-reward-share`        | PERCENT                                   | asked; `40` without a terminal                 | Denominator: validator's share of each reward, 0 to 100.         |
| `--pool-max-stakers`         | N                                         | asked; `40` without a terminal                 | Denominator: most stakers a pool takes, 1 to 40.                 |
| `--pool-min-staker-stake`    | TOKENS                                    | asked; `1000` without a terminal               | Denominator: smallest staker deposit, at least 100.              |
| `--pool-min-validator-stake` | TOKENS                                    | asked; `1000` without a terminal               | Denominator: validator's funds a pool needs, at least 1000.      |
| `--yes`                      |                                           | ask                                            | Skip the confirmation prompt.                                    |
| `--dry-run`                  |                                           | off                                            | Print the role and change nothing.                               |

## Behaviour

- Needs root and a node that `node create` finished; one still being created or removed is refused.
- Without a terminal, pass the flags the role needs: the type defaults to `depool`, with a new Safe.
- An existing Safe's seed phrases are typed at `wallet recover`, which checks them against the chain at once.
- Refuses a Safe or DePool another node on this host uses, and a nominator owner this host holds.
- Solo, nominator and denominator Safes are always in the masterchain; a DePool validator's Safe is in the node's workchain.
- Denominator: each pool keeps the reward share, the staker limit and both minimums for life.
- A new nominator Safe is offered one key (also the default without a terminal) or several, which then asks for custodians and signatures.
- Can run again until the wallet setup begins: once the node holds the Safe's keys or phrases, the role is fixed.
- Prints the next command: `wallet create`, or `wallet recover` for an existing Safe.

## See also

- [node create](create.md)
- [wallet create](../wallet/create.md)
- [wallet recover](../wallet/recover.md)
