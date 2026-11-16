# Voting on network config changes

Validators change the network's configuration by vote: they burn the config
master key, change a param, or upgrade code. One validator registers a change
and pays; every validator who agrees joins it, and evernode votes in every
round until the change is decided. Flags are in the
[command reference](docs/README.md#network-config-votes).

```bash
sudo evernode vote propose -n validator01 --pay-from-safe --burn      # registers and pays; prints the hash
sudo evernode vote join -n validator01 --proposal HASH                 # votes now and in every round
sudo evernode vote status                                              # the round, the elections, joined proposals
sudo evernode vote history                                             # what this server voted for
```

## Before you start

```bash
sudo evernode host setup --yes                                           # the vote engine, and cron, which votes
sudo evernode vote status -n validator01 --keys                          # shows which current validator key the node holds
```

Votes go through mainnet's public GraphQL endpoint, `https://mainnet.evercloud.dev/graphql`.
For another network, or your own endpoint, add `--endpoint URL` to each command. A joined
proposal keeps the endpoint it was joined with.

## How a change wins

On mainnet a round lasts about 18 hours. A change wins a round when validators
with more than 3/4 of the weight vote for it in that round.

| Change                                 | Rounds to win | Lost rounds allowed |
| -------------------------------------- | ------------- | ------------------- |
| a normal param                         | 2             | 2                   |
| a critical param, the master key, code | 4             | 2                   |

Register a change early in a round: a round it does not win counts as lost.

## Burn the master key

```bash
evernode vote burn-key                   # the key the burn installs: nobody can sign with it
evernode vote propose --burn --dry-run   # the hash and the price; sends nothing
sudo evernode vote propose -n validator01 --pay-from-safe --burn
sudo evernode vote join -n validator01 --proposal 25276f466b97fe640e038b05aa3da9159a946ef7902fbecea11e8f93cf77a934
```

The burn's hash is the same on every network. Once the burn is accepted,
`vote status` shows the master key as `burned`.

## Change a param

```bash
evernode vote param 17                                                      # copy the JSON into p17.json and edit it
evernode vote propose --param 17 --value-json "$(cat p17.json)" --dry-run   # the change, field by field, and the price
sudo evernode vote propose -n validator01 --pay-from-safe --param 17 --value-json "$(cat p17.json)"
sudo evernode vote join -n validator01 --proposal HASH
```

- Write each field as `param` prints it: `propose` refuses a value the network
  would read differently.
- Params 0, 1, 8-12 and 31, the validator sets and code need `--force`.

## Join a proposal

```bash
sudo evernode vote status                                   # open proposals and their hashes
sudo evernode vote join -n validator01 --proposal HASH      # one node
sudo evernode vote join --all --proposal HASH               # every node on this server
```

`join` shows the change and asks, then votes at once and in every round. Cron
retries a missing vote every minute; it never pays.

```bash
sudo evernode vote history --proposal HASH   # its votes, round by round
sudo evernode vote leave --proposal HASH     # stop voting for it
```

## Paying

Only the validator who registers a change pays. On mainnet in November 2026: a
burn 22.8 tokens, a critical param about 22, a normal param 2 to 3. The wallet
needs the price plus 3 tokens; the rest comes back.

```bash
sudo evernode vote propose -n validator01 --pay-from-safe --burn      # the node's Safe: solo and nominator validators
evernode vote propose --pay-from ADDRESS --burn                       # any masterchain wallet; asks for its seed phrase
evernode vote propose --from payer.json --burn                        # a wallet file, below
```

A DePool validator's Safe is in workchain 0 and cannot pay: use `--pay-from` or
`--from`.

### Wallet files

```bash
(umask 077; evernode vote mint-wallet --name payer > payer.json)   # a new wallet; keep the file private
jq -r .address payer.json                                          # send the price plus 3 tokens here
evernode vote propose --from payer.json --burn                     # the first payment deploys it
```

A multisig takes a file like this, with its custodians' key files next to it.
With fewer keys than it needs, `propose` stops at `UNCONFIRMED` until the other
custodians run the same command with their own keys.

```json
{
  "name": "payer-safe",
  "type": "multisig",
  "address": "-1:ADDRESS",
  "keys": ["custodian1.keys.json", "custodian2.keys.json"]
}
```

## Upgrade the config contract or the elector

```bash
evernode vote simulate --config-code @CODE.boc --force   # plays the whole vote offline first
evernode vote propose --from payer.json --config-code @CODE.boc --force
sudo evernode vote join -n validator01 --proposal HASH
```

For the elector, use `--elector-code`; `simulate` shows whether the elector
accepts the new code.

## Vote from another machine

```bash
# on the server: copy the node's keys
(umask 077; sudo jq '{validator_key_ring}' /var/lib/ever-validator/nodes/validator01/node_cfg/config.json > keyring.json)
# anywhere: vote in every round while it runs
evernode vote cast --keyring keyring.json --proposal HASH --watch
```

The node makes a new key at every election: copy the file again when
`evernode vote status --keyring keyring.json --keys` shows `holds []`.

## Vote with a node evernode does not manage

Install evernode as the [README](README.md#install) shows. On a server without
Docker, skip `host setup` and build the vote engine as root with Rust 1.90 or
newer: `cargo install --locked --root /usr/local --path /root/evernode_cli/vote`.

```bash
pgrep -af ever-node                                                         # config.json is in its --configs directory
sudo chmod 600 /PATH/TO/config.json
sudo evernode vote join --keyring /PATH/TO/config.json --proposal HASH
```

For a node in Docker:

```bash
sudo docker exec CONTAINER chmod 600 /PATH/TO/config.json
sudo evernode vote join --keyring CONTAINER:/PATH/TO/config.json --proposal HASH
```

## Messages and what they mean

| You see                                                             | What it means                                                             |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| `no key of the listed nodes is in this round's validator set`       | Your nodes are not validators this round; evernode votes next round.      |
| `pass failed: the endpoint's newest block is … s old`               | The endpoint is behind; evernode tries again in a minute.                 |
| `did not land: another message took the seqno first, or it expired` | Validators voted at the same moment; evernode sends the vote again.       |
| `WARNING     no cron daemon is running`                             | Nothing votes until cron runs: `evernode host setup` starts it.           |
| `WARNING     /etc/cron.d/evernode_vote is missing`                  | Nothing votes: join one of the proposals again.                           |
| A round is lost although you voted                                  | The voters held 3/4 of the weight or less: others must vote too.          |
| `UNCONFIRMED`                                                       | The paying multisig waits for its other custodians.                       |
| `not applied`                                                       | The change expired, lost too many rounds, or the param changed meanwhile. |
