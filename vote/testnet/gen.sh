#!/usr/bin/env bash
# Generates a local ever-node network to test the burn on: 3 validators in Docker, a fresh zerostate,
# short validator rounds, and a config contract whose dictator key we hold (so a test can show the key
# works before the burn and not after it).
#
# Follows the migration project's ever-docker/gen.sh (itself upstream's tests/test_run_net/test_run_net.sh),
# with every ever-node tool run inside the existing image ever-node-local:e406862, and these differences:
# - its own compose project and subnet (default evernode-vote, 172.33.0.0/24), so it runs next to other
#   local networks; the DHT entries in the global config are signed for <prefix>.11.. port 30303;
# - wallets (one per validator, a giver and --relays vote relays) are minted by evernode-vote, not tycho;
# - the config contract's dictator key is replaced by a fresh key in keys/dictator.json;
# - console.json gets the validator wallet (election requests are signed for it);
# - control servers listen on 0.0.0.0:3031 inside the containers; compose publishes them on the host's
#   loopback as 127.0.0.1:1303<i>, and keys/console-node<i>.json are console key files pointing there —
#   everything evernode-vote needs to reach a node, as an operator would through an SSH tunnel;
# - writes net.json, the network description evernode-vote works from: the GraphQL endpoint it reads and
#   sends through (the ever-gql stack attached to this network, see the README), the giver as payer, and the
#   nodes (node3 signs its votes from its key ring, nodes 1 and 2 through their control servers).
# The zerostate template is ever-node's tests/test_run_net/zero_state_blanc_{1,2}.json (copied to net/template).
#
# Restarting from zerostate (as the migration project's docs/ever-node.md §8 describes for its network):
# stop the staking loops, `docker compose … down -v`, then `gen.sh --out NEW --keys-from OLD`, `up -d`, and
# start the loops again. --keys-from re-mints the old run's wallets from their secrets (same addresses and
# keys, balances reset to this run's zerostate amounts) and keeps its dictator key. Node identities are
# always new: a permanent validator key is bound to one election id (the genesis) and every bid adds a key,
# so an old run's node dirs do not match a new zerostate.
#
# usage: gen.sh --out RUN_DIR [--keys-from OLD_RUN_DIR] [--relays 2] [--round 300] [--first-round 600]
#               [--prefix 172.33.0] [--project evernode-vote] [--validator-balance 1000000] [--giver-balance 1000000]
#               [--endpoint http://127.0.0.1:4000/graphql]
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
root=$(cd "$here/.." && pwd -P)
image=${IMAGE:-ever-node-local:e406862}
bd=${EVERNODE_VOTE:-$root/target/release/evernode-vote}
tpl=$here/template

out="" nodes=3 round=300 first_round=600 prefix=172.33.0 project=evernode-vote validator_balance=1000000 giver_balance=1000000
keys_from="" relays=2 endpoint=http://127.0.0.1:4000/graphql
while [[ $# -gt 0 ]]; do
    case "$1" in
        --out) out="$2"; shift 2 ;;
        --round) round="$2"; shift 2 ;;
        --first-round) first_round="$2"; shift 2 ;;
        --prefix) prefix="$2"; shift 2 ;;
        --project) project="$2"; shift 2 ;;
        --validator-balance) validator_balance="$2"; shift 2 ;;
        --giver-balance) giver_balance="$2"; shift 2 ;;
        --keys-from) keys_from="$2"; shift 2 ;;
        --relays) relays="$2"; shift 2 ;;
        --endpoint) endpoint="$2"; shift 2 ;;
        *) echo "ERROR: unknown argument $1" >&2; exit 1 ;;
    esac
done
[[ -n $out ]] || { echo "ERROR: --out is required" >&2; exit 1; }
[[ -x $bd ]] || { echo "ERROR: build evernode-vote first (cargo build --release)" >&2; exit 1; }
if [[ -n $keys_from ]]; then
    keys_from=$(cd "$keys_from" && pwd -P)
    [[ -n $(ls "$keys_from"/wallets/*.json 2>/dev/null) ]] || { echo "ERROR: $keys_from/wallets has no wallet json" >&2; exit 1; }
fi
mkdir -p "$out"
out=$(cd "$out" && pwd -P)
net=$out/net
[[ ! -e $net ]] || { echo "ERROR: $net exists; every run gets fresh keys" >&2; exit 1; }
mkdir -p "$net/zerostate" "$out/wallets" "$out/keys"
chmod 700 "$out/wallets" "$out/keys"

genesis=$(date +%s)
port=30303
control_port=3031
ip() { echo "$prefix.1$1"; }
in_image() { docker run --rm --entrypoint bash -v "$1:/w" -w /w "$image" -c "$2"; }

# global config with an empty DHT, only so that the node can start for key generation.
# Written literally: jq turns the shard -9223372036854775808 into a float, which ever-node rejects.
fake_global='{"@type": "config.global",
 "dht": {"@type": "dht.config.global", "k": 6, "a": 3, "static_nodes": {"@type": "dht.nodes", "nodes": []}},
 "validator": {"@type": "validator.config.global", "zero_state": {"workchain": -1, "shard": -9223372036854775808, "seqno": 0,
  "root_hash": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", "file_hash": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}}}'

# ---------- wallets and the dictator key ----------
# with --keys-from, a wallet the old run had is minted again from its secret: same address, fresh balance
old_secret() {
    if [[ -n $keys_from && -f $keys_from/wallets/$1.json ]]; then jq -r .secret "$keys_from/wallets/$1.json"; fi
}
mint() {
    local secret
    secret=$(old_secret "$1")
    "$bd" mint-wallet --name "$1" --balance "$2" ${secret:+--secret "$secret"} >"$out/wallets/$1.json"
}
for i in $(seq "$nodes"); do
    mint "validator-node$i" "$validator_balance"
done
mint giver "$giver_balance"
# vote relays: a wallet that stakes must not also relay votes (EverWallet rejects a message older than one
# it already accepted, so interleaved senders lose messages); concurrent campaigns need one each
for r in $(seq "$relays"); do
    mint "relay-$r" 100000
done
if [[ -n $keys_from && -f $keys_from/keys/dictator.json ]]; then
    cp "$keys_from/keys/dictator.json" "$out/keys/dictator.json"
else
    "$bd" gen-key >"$out/keys/dictator.json"
fi
[[ -z $keys_from ]] || echo "reused from $keys_from: $(cd "$out/wallets" && ls | tr '\n' ' ')and the dictator key"

# ---------- nodes: configs, console keys, validator keys ----------
validators=()
for i in $(seq "$nodes"); do
    nd=$net/node$i
    mkdir -p "$nd"
    chmod 700 "$nd"
    cp "$here/log_cfg.yml" "$nd/log_cfg.yml"
    echo "$fake_global" >"$nd/ton-global.config.json"
    jq -n --arg ip "0.0.0.0:$port" --argjson cp "$control_port" '{
        log_config_name: "log_cfg.yml",
        ton_global_config_name: "ton-global.config.json",
        internal_db_path: "/db",
        ip_address: $ip,
        control_server_port: $cp,
        validation_countdown_mode: "except-zerostate"
    }' >"$nd/default_config.json"
    wallet=$(jq -r .address "$out/wallets/validator-node$i.json")

    # console client key, node config (ADNL keys, control server), validator key via the console
    in_image "$nd" '
        set -e
        keygen >console_keys.json
        ever-node --configs /w --ckey "$(jq -c .public console_keys.json)" >init.log 2>&1 &
        for _ in $(seq 60); do [ -f console_config.json ] && break; sleep 1; done
        sleep 3
        jq --argjson c "$(cat console_config.json)" --argjson k "$(jq .private console_keys.json)" --arg w "'"$wallet"'" \
            "{config: (\$c + {client_key: \$k}), wallet_id: \$w, max_factor: 3}" -n >console.json
        key=$(console -C console.json -c newkey | grep -o "key hash: [^ ]*" | cut -d" " -f3)
        [ -n "$key" ] || { console -C console.json -c newkey; exit 1; }
        console -C console.json -c "addpermkey $key '"$genesis"' '"$((genesis + 10 * 365 * 86400))"'" >/dev/null
        console -C console.json -c "exportpub $key" | grep "imported key:" | awk "{print \$3}" >validator.pub
        kill %1; wait || true
    '
    [[ -s $nd/validator.pub ]] || { echo "ERROR: no validator key for node$i (see $nd/init.log)" >&2; exit 1; }
    validators+=("$(cat "$nd/validator.pub")")
    # advertise the container address instead of 0.0.0.0, and let the control server accept connections
    # from the published port (python keeps 64-bit integers exact)
    python3 -c 'import json,sys
c=json.load(open(sys.argv[1])); c["adnl_node"]["ip_address"]=sys.argv[2]; c["control_server"]["address"]="0.0.0.0:3031"
json.dump(c, open(sys.argv[1], "w"), indent=2)' "$nd/config.json" "$(ip "$i"):$port"
    # the console key file an operator holds: server address and key, client secret
    jq --arg a "127.0.0.1:$((13030 + i))" '{config: (.config + {server_address: $a})}' "$nd/console.json" >"$out/keys/console-node$i.json"
done

# ---------- zerostate ----------
python3 - "$tpl" "$net/zerostate/zero_state.json" "$genesis" "$first_round" "$round" "$nodes" \
    "$out/wallets" "$bd" "$(jq -r .public "$out/keys/dictator.json")" "${validators[@]}" <<'PY'
import glob, json, subprocess, sys
tpl, dst, genesis, first_round, rnd, nodes, wallets, bd, dictator = sys.argv[1:10]
keys = sys.argv[10:]
genesis, first_round, rnd, nodes = int(genesis), int(first_round), int(rnd), int(nodes)
raw = open(f"{tpl}/zero_state_blanc_1.json").read() + '{"public_key": "00", "weight": "1"}' + open(f"{tpl}/zero_state_blanc_2.json").read()
raw = raw.replace("nowdate", str(genesis)).replace("p34_total_weight", "1").replace("p34_total", "1")
z = json.loads(raw)
c = z["master"]["config"]
c["p15"] = {"validators_elected_for": rnd, "elections_start_before": rnd * 2 // 3,
            "elections_end_before": rnd * 4 // 15, "stake_held_for": rnd * 4 // 15}
# one unsplit basechain shard, as on Everscale mainnet
for wc, descr in list(c["p12"].items()) if isinstance(c["p12"], dict) else enumerate(c["p12"]):
    if isinstance(descr, dict) and descr.get("workchain_id", 0) != -1:
        descr["actual_min_split"] = descr["min_split"] = descr["max_split"] = 0
c["p16"]["min_validators"] = nodes
c["p17"]["min_stake"] = "1000000000000"
c["p17"]["min_total_stake"] = str(nodes * 1000 * 10**9)
c["p34"] = {"utime_since": genesis, "utime_until": genesis + first_round, "total": nodes, "main": nodes,
            "total_weight": nodes, "list": [{"public_key": k, "weight": "1"} for k in keys]}
# the dictator: the config account keeps the template's code, params and votes, with our key
for a in z["accounts"]:
    if a["id"] == "-1:" + c["p0"]:
        a["boc"] = subprocess.check_output([bd, "patch-config-key", "--boc", a["boc"], "--key", dictator], text=True).strip()
minted = 0
for f in sorted(glob.glob(f"{wallets}/*.json")):
    w = json.load(open(f))
    z["accounts"].append({"id": w["address"], "workchain_id": "-1", "balance": str(int(w["balance"]) * 10**9), "boc": w["boc"]})
    minted += int(w["balance"]) * 10**9
z["total_balance"] = str(int(z["total_balance"]) + minted)
z["master"]["global_balance"] = str(int(z["master"]["global_balance"]) + minted)
json.dump(z, open(dst, "w"), indent=1)
PY
in_image "$net/zerostate" 'zerostate -i zero_state.json >zerostate.out 2>&1 || { cat zerostate.out; exit 1; }'

# ---------- global config: DHT entries of all nodes + zerostate id ----------
dht="[]"
for i in $(seq "$nodes"); do
    dht_key=$(jq -r '.adnl_node.keys[] | select(.tag == 1) | .data.pvt_key' "$net/node$i/config.json")
    entry=$(docker run --rm --entrypoint gendht "$image" "$(ip "$i"):$port" "$dht_key")
    dht=$(jq --argjson e "$entry" '. + [$e]' <<<"$dht")
done
python3 - "$net/zerostate/config.json" "$net/global-config.json" "$dht" <<'PY'
import json, sys
zs = json.load(open(sys.argv[1]))["zero_state"]
cfg = {"@type": "config.global",
       "dht": {"@type": "dht.config.global", "k": 6, "a": 3,
               "static_nodes": {"@type": "dht.nodes", "nodes": json.loads(sys.argv[3])}},
       "validator": {"@type": "validator.config.global", "zero_state": zs}}
json.dump(cfg, open(sys.argv[2], "w"), indent=2)   # python keeps the shard -9223372036854775808 exact
PY
for i in $(seq "$nodes"); do
    cp "$net/global-config.json" "$net/node$i/ton-global.config.json"
done

# ---------- the network description for evernode-vote, and the compose environment ----------
jq -n --argjson n "$nodes" --arg p "$project" --arg e "$endpoint" --argjson gid "$(jq .global_id "$net/zerostate/zero_state.json")" '{
    endpoint: $e,
    global_id: $gid,
    payer: "wallets/giver.json",
    relay: "wallets/relay-1.json",
    dictator: "keys/dictator.json",
    nodes: [range(1; $n + 1) | {name: "node\(.)", control: "keys/console-node\(.).json",
                                 wallet: "wallets/validator-node\(.).json",
                                 exec: ["docker", "exec", "\($p)-node\(.)-1"]}]
} | .nodes[2].keyring = "net/node3/config.json"' >"$out/net.json"
printf 'RUN_DIR=%s\nNET_PREFIX=%s\nCOMPOSE_PROJECT_NAME=%s\n' "$out" "$prefix" "$project" >"$out/compose.env"

jq -n --argjson g "$genesis" --argjson n "$nodes" --argjson r "$round" --argjson fr "$first_round" --arg prefix "$prefix" \
    --slurpfile zs "$net/zerostate/config.json" \
    '{genesis: $g, nodes: $n, round_seconds: $r, first_round_seconds: $fr, prefix: $prefix,
      elections_open_at: ($g + $fr - ($r * 2 / 3 | floor)), first_switch_at: ($g + $fr), zerostate: $zs[0].zero_state}' \
    | tee "$net/gen-params.json"
