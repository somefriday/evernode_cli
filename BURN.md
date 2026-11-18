# Burn the config master key

Validators burn the network's config master key by vote. One validator registers
the burn and pays; every validator joins it, and cron votes in every round until
it is decided. Guide: [voting_instruction.md](voting_instruction.md).

Run as root.

The burn's hash, the same on every network:

```text
25276f466b97fe640e038b05aa3da9159a946ef7902fbecea11e8f93cf77a934
```

Commands go to mainnet through its public endpoint. For another network, add
`--endpoint URL` to each command.

## 1. Register the burn

One validator, once, early in a round: a round the burn does not win counts as
lost. The payer needs the price plus 3 tokens; the config keeps the price and
returns the rest.

```bash
evernode vote propose --burn --dry-run   # the hash and the price; sends nothing
```

### From the node's Safe

Solo and nominator validators:

```bash
evernode vote propose -n validator01 --pay-from-safe --burn
```

A DePool validator's Safe is in workchain 0 and cannot pay: use a payer wallet.

### From a payer wallet

```bash
evernode vote mint-wallet --name payer > payer.json   # a new masterchain wallet
chmod 600 payer.json                                  # the file holds its secret key
jq -r .address payer.json                             # send the price plus 3 tokens here
evernode vote propose --from payer.json --burn        # the first payment deploys it
```

## 2. Join the burn

Every validator. `join` shows the change, asks, votes at once and installs the
cron line that votes in every round.

### Nodes managed by evernode

```bash
evernode vote join --all --proposal 25276f466b97fe640e038b05aa3da9159a946ef7902fbecea11e8f93cf77a934
```

### ever-node installed on the host

```bash
pgrep -af ever-node                 # config.json is in its --configs directory
chmod 600 /PATH/TO/config.json
evernode vote join --keyring /PATH/TO/config.json --proposal 25276f466b97fe640e038b05aa3da9159a946ef7902fbecea11e8f93cf77a934
```

### ever-node in Docker

```bash
docker exec CONTAINER chmod 600 /PATH/TO/config.json
evernode vote join --keyring CONTAINER:/PATH/TO/config.json --proposal 25276f466b97fe640e038b05aa3da9159a946ef7902fbecea11e8f93cf77a934
```

## 3. Watch it

```bash
evernode vote status    # the master key shows "burned" once accepted
evernode vote history --proposal 25276f466b97fe640e038b05aa3da9159a946ef7902fbecea11e8f93cf77a934   # this server's votes, round by round
```

- The burn needs 4 won rounds, each with more than 3/4 of the validator weight,
  and allows 2 lost rounds. On mainnet a round lasts about 18 hours, so the burn
  takes about 3 days.
- Cron votes in every round, so it must run until the burn is decided:
  `vote status` warns when no cron daemon runs.
- Once the burn is decided, the cron line is removed.
