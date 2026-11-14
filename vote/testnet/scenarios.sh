#!/usr/bin/env bash
# Runs every voting scenario live on a network made by testnet/gen.sh, in parallel where they are independent,
# and checks each against what the config contract must do. The scenarios need 12 vote relays
# (gen.sh --relays 12), a dictator key the test holds (gen.sh writes one), and node1 staking more than the
# others (elect --stake 270000 for node1, 100000 for nodes 2 and 3), so that from round 1 on the
# validators weigh about 57/21/21 % while round 0, the zerostate's set, weighs 1/1/1.
#
# Every command reaches the chain through the net file's GraphQL endpoint, so the ever-gql stack must be
# attached to the network (see the README). Start it right after `docker compose up`, the staking loops and
# the attach; it takes 12 rounds (about 65 min with gen.sh's defaults). Logs go to
# RUN_DIR/scenarios/<scenario>.log, the verdicts to RUN_DIR/scenarios/summary.log.
#
# usage: testnet/scenarios.sh RUN_DIR
#
# Everything runs inside main(), so bash reads the whole file before running any of it: editing the file
# while a run is going does not change what that run executes.
main() {
set -uo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
root=$(cd "$here/.." && pwd -P)
B=${EVERNODE_VOTE:-$root/target/release/evernode-vote}
run=$(cd "${1:?run dir}" && pwd -P)
N=$run/net.json
W=$run/wallets
out=$run/scenarios
mkdir -p "$out"
g=$(jq .genesis "$run/net/gen-params.json")
first=$(jq .first_round_seconds "$run/net/gen-params.json")
round=$(jq .round_seconds "$run/net/gen-params.json")
project=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$run/compose.env")

# round k starts at: 0 → genesis, k ≥ 1 → genesis + first round + (k − 1) rounds
at() { if (( $1 == 0 )); then echo "$g"; else echo $(( g + first + ($1 - 1) * round )); fi; }
wait_until() { while (( $(date +%s) < $1 )); do sleep 5; done; }
note() { echo "$(date -u +%T) $*" | tee -a "$out/summary.log"; }
# launch NAME CMD...: run in the background; output in NAME.log, exit code in NAME.exit
launch() {
    local name=$1
    shift
    rm -f "$out/$name.exit"
    ( "$@" >"$out/$name.log" 2>&1; echo $? >"$out/$name.exit" ) &
    echo $! >"$out/$name.pid"
    note "start $name: ${*#"$B" }"
}
# finish NAME DEADLINE: wait for NAME to end, at most until DEADLINE
finish() { while [[ ! -f $out/$1.exit ]] && (( $(date +%s) < $2 )); do sleep 5; done; }
halt() { pkill -P "$(cat "$out/$1.pid")" 2>/dev/null; sleep 1; }
pass=0 fail=0
check() {
    local name=$1 what=$2
    shift 2
    if "$@"; then note "PASS $name: $what"; pass=$((pass + 1)); else note "FAIL $name: $what"; fail=$((fail + 1)); fi
}
has() { grep -q -- "$2" "$out/$1.log"; }
exited() { [[ -f $out/$1.exit && $(cat "$out/$1.exit") == "$2" ]]; }
failed() { [[ -f $out/$1.exit && $(cat "$out/$1.exit") != 0 ]]; }
# a one-cell value: a 32-bit number (params above 100 are unknown to ever-node: stored, read by nothing)
boc32() { python3 -c 'import base64,sys; c=bytes([0,8])+int(sys.argv[1]).to_bytes(4,"big"); print(base64.b64encode(bytes.fromhex("b5ee9c72")+bytes([1,1,1,1,0,len(c),0])+c).decode())' "$1"; }
config_balance() { "$B" status --net "$N" 2>/dev/null | awk '/^config /{for(i=1;i<=NF;i++) if($i=="balance") print $(i+1)}'; }
code_hash() { "$B" status --net "$N" 2>/dev/null | awk -v w="$1" '/^code /{for(i=1;i<=NF;i++) if($i==w) print $(i+1)}'; }
param_hash() { "$B" param --net "$N" -- "$1" 2>/dev/null | awk 'NR==1{print $3}'; }
hash_of() { "$B" boc-hash --boc "$1" 2>/dev/null; }

# operators that hold only some of the validators
jq '.nodes |= map(select(.name == "node1"))' "$N" >"$run/net-a.json"   # 1/3 in round 0, ~57 % later
jq '.nodes |= map(select(.name != "node1"))' "$N" >"$run/net-b.json"   # node2 + node3
jq '.nodes |= map(select(.name != "node3"))' "$N" >"$run/net-ab.json"  # 2/3 in round 0, ~79 % later

P13=te6ccgEBAQEADQAAFRpS6Q7dAAEBIB9I # p13 with the complaint deposit doubled (100 → 200 EVER)
P17='{"min_stake":"2000000000000","max_stake":"10000000000000000","min_total_stake":"3000000000000","max_stake_factor":196608}'
P11_ORIG='{"normal_params":{"min_tot_rounds":2,"max_tot_rounds":3,"min_wins":2,"max_losses":2,"min_store_sec":1000000,"max_store_sec":10000000,"bit_price":1,"cell_price":500},"critical_params":{"min_tot_rounds":4,"max_tot_rounds":7,"min_wins":4,"max_losses":2,"min_store_sec":5000000,"max_store_sec":20000000,"bit_price":2,"cell_price":1000}}'
P11_LOW='{"normal_params":{"min_tot_rounds":2,"max_tot_rounds":3,"min_wins":2,"max_losses":2,"min_store_sec":60,"max_store_sec":10000000,"bit_price":1,"cell_price":500},"critical_params":{"min_tot_rounds":2,"max_tot_rounds":7,"min_wins":2,"max_losses":2,"min_store_sec":5000000,"max_store_sec":20000000,"bit_price":2,"cell_price":1000}}'

note "network $run: genesis $g, round 1 at $(at 1), rounds of ${round}s, endpoint $(jq -r .endpoint "$N")"
"$B" status --net "$N" >/dev/null || { note "the endpoint does not answer for this network: attach the ever-gql stack first"; exit 1; }

# ---------------------------------------------------------------- round 0: the zerostate's validators
launch S01 "$B" dictator-ping --net "$N" --expect accepted
finish S01 $(( $(date +%s) + 120 ))
check S01 "the test dictator key commands the config before any vote" exited S01 0

launch S21 "$B" propose --net "$N" --param 17 --value-json "$P17" --if-hash none --dry-run
finish S21 $(( $(date +%s) + 60 ))
check S21 "a mandatory param without if_hash_equal is refused before anything is paid" bash -c "[[ \$(cat '$out/S21.exit') != 0 ]] && grep -q 'mandatory' '$out/S21.log'"

# S02: a normal param, registered alone so that the config's balance shows exactly the price
before=$(config_balance)
launch S02-propose "$B" propose --net "$N" --param 13 --value-boc "$P13" --from "$W/relay-3.json"
finish S02-propose $(( $(date +%s) + 300 ))
after=$(config_balance)
price=$(grep -o 'price [0-9.]*' "$out/S02-propose.log" | head -1 | awk '{print $2}')
note "S02 config balance $before -> $after, price $price"
check S02-price "registration: the config keeps exactly the price" \
    python3 -c "import sys; from decimal import Decimal as D; sys.exit(D('$after') - D('$before') != D('$price'))"
launch S02 "$B" cast --net "$N" --param 13 --value-boc "$P13" --relay "$W/relay-3.json" --watch

launch S03 "$B" run --net "$N" --param 17 --value-json "$P17" --from "$W/relay-2.json" --relay "$W/relay-2.json"

# S04: a coordinator registers; two operators vote, one by the change, one only by the proposal's hash
launch S04-propose "$B" propose --net "$N" --param 101 --value-boc "$(boc32 101)" --from "$W/relay-4.json"
finish S04-propose $(( $(date +%s) + 300 ))
H101=$(grep -o 'proposal [0-9a-f]\{64\}' "$out/S04-propose.log" | head -1 | awk '{print $2}')
launch S22 "$B" propose --net "$N" --param 101 --value-boc "$(boc32 101)" --from "$W/relay-4.json"
launch S04a "$B" cast --net "$run/net-a.json" --param 101 --value-boc "$(boc32 101)" --relay "$W/relay-4.json" --watch
launch S04b "$B" cast --net "$run/net-b.json" --proposal "$H101" --relay "$W/relay-5.json" --watch

launch S05 "$B" run --net "$run/net-a.json" --param 102 --value-boc "$(boc32 102)" --from "$W/relay-6.json" --relay "$W/relay-6.json"
launch S06 "$B" run --net "$run/net-a.json" --param 103 --value-boc "$(boc32 103)" --from "$W/relay-7.json" --relay "$W/relay-7.json"
launch S07 "$B" run --net "$N" --param 104 --value-boc "$(boc32 1040)" --from "$W/relay-8.json" --relay "$W/relay-8.json"
launch S08x "$B" run --net "$N" --param 105 --value-boc "$(boc32 1051)" --from "$W/relay-9.json" --relay "$W/relay-9.json"
launch S08y "$B" run --net "$N" --param 105 --value-boc "$(boc32 1052)" --from "$W/relay-10.json" --relay "$W/relay-10.json"
launch S09 "$B" burn --net "$N" --from "$W/relay-1.json" --relay "$W/relay-1.json"
launch S15 "$B" run --net "$run/net-ab.json" --param 107 --value-boc "$(boc32 107)" --from "$W/relay-11.json" --relay "$W/relay-11.json"

# S07: once its proposal has won round 0, the dictator changes p104 itself
deadline=$(( $(at 1) - 30 ))
while ! grep -q 'won this round' "$out/S07.log" 2>/dev/null && (( $(date +%s) < deadline )); do sleep 5; done
launch S07-dictator "$B" dictator-set --net "$N" --param 104 --value-boc "$(boc32 1049)"
finish S07-dictator $(( $(date +%s) + 120 ))
check S07-dictator "the dictator sets a param directly while a proposal for it is being voted on" exited S07-dictator 0

finish S22 $(( $(date +%s) + 120 ))
check S22 "registering a proposal that is already registered pays nothing again" \
    bash -c "grep -q 'registered; voting goes on' '$out/S22.log' && ! grep -q ' sent from ' '$out/S22.log'"

# ---------------------------------------------------------------- round 1: weights from the first election
r2=$(at 2)
for s in S02 S04a S04b S07 S08x S08y; do finish $s $(( r2 - 20 )); done
check S02 "normal param: 2 won rounds, applied" has S02 'APPLIED'
check S04 "two operators, neither above 3/4 alone, together win and apply it" bash -c "grep -q APPLIED '$out/S04a.log' && grep -q 'proposal is gone' '$out/S04b.log'"
check S07 "if_hash_equal went stale (the dictator changed p104): accepted without effect" \
    bash -c "grep -q 'REMOVED' '$out/S07.log' && [[ '$(param_hash 104)' == '$(hash_of "$(boc32 1049)")' ]]"
check S08 "two proposals for one param: the first accepted applies, the other is accepted without effect" \
    bash -c "cat '$out/S08x.log' '$out/S08y.log' | grep -c 'APPLIED' | grep -qx 1 && cat '$out/S08x.log' '$out/S08y.log' | grep -q 'REMOVED'"
launch S19 "$B" run --net "$N" --param 13 --value-boc "$P13" --from "$W/relay-3.json"
finish S19 $(( $(date +%s) + 120 ))
check S19 "run for a change already in effect: nothing to do, nothing paid" has S19 'nothing to do'

# ---------------------------------------------------------------- round 2: S05 gets all validators
wait_until "$r2"
halt S05
launch S05b "$B" cast --net "$N" --param 102 --value-boc "$(boc32 102)" --relay "$W/relay-6.json" --watch

# ---------------------------------------------------------------- round 3: the critical changes complete
r4=$(at 4)
for s in S03 S05b S06 S09 S15; do finish $s $(( r4 - 20 )); done
check S03 "critical + mandatory param (p17), if_hash_equal automatic: 4 won rounds, applied" has S03 'APPLIED'
check S05 "two lost rounds (one operator, ~57 %), then two won: applied with 2 losses" \
    bash -c "grep -q 'APPLIED' '$out/S05b.log' && grep -q 'losses 2/2' '$out/S05b.log'"
check S06 "one operator alone (1/3, then ~57 %) loses 3 rounds: the proposal is discarded" \
    bash -c "[[ \$(cat '$out/S06.exit') != 0 ]] && grep -q 'lost too many rounds' '$out/S06.log'"
check S09 "burn: 4 won critical rounds, the dictator key is the burn key" has S09 'APPLIED'
check S15 "weights, not counts: 2 of 3 validators lose round 0 (2/3) and win later (~79 %)" \
    bash -c "grep -q 'APPLIED' '$out/S15.log' && grep -q 'losses 1/2' '$out/S15.log'"
launch S09-after "$B" dictator-ping --net "$N" --expect rejected
finish S09-after $(( $(date +%s) + 120 ))
check S09-after "after the burn the old dictator key is rejected" exited S09-after 0

# ---------------------------------------------------------------- rounds 4-5: change the voting rules
wait_until "$(at 4)"
launch S11 "$B" run --net "$N" --param 11 --value-json "$P11_LOW" --force --from "$W/relay-3.json" --relay "$W/relay-3.json"
r6=$(at 6)
finish S11 $(( r6 - 20 ))
check S11 "p11 (not critical here) by a normal vote: critical changes now need 2 wins, normal proposals may live 60 s" has S11 'APPLIED'

# ---------------------------------------------------------------- rounds 6-7: appoint a new dictator; expiry
wait_until "$r6"
"$B" gen-key >"$run/keys/dictator-2.json"
launch S10 "$B" run --net "$N" --dictator "$(jq -r .public "$run/keys/dictator-2.json")" --allow-usable-key \
    --from "$W/relay-1.json" --relay "$W/relay-1.json"
launch S18 "$B" run --net "$run/net-a.json" --param 109 --value-boc "$(boc32 109)" --ttl 90 --from "$W/relay-7.json" --relay "$W/relay-7.json"
r8=$(at 8)
for s in S10 S18; do finish $s $(( r8 - 20 )); done
check S10 "validators appoint a new dictator after the burn (critical, now 2 wins)" has S10 'APPLIED'
check S18 "a proposal not won within its ttl (90 s) expires" has S18 'expired'
launch S10-after "$B" dictator-ping --net "$N" --keys "$run/keys/dictator-2.json" --expect accepted
finish S10-after $(( $(date +%s) + 120 ))
check S10-after "the new dictator's key commands the config" exited S10-after 0

# ---------------------------------------------------------------- rounds 8-9: burn again; code upgrades
wait_until "$r8"
elector_before=$(code_hash elector)
"$B" wrap-code --net "$N" >"$run/config-code.b64"
"$B" wrap-code --net "$N" --elector >"$run/elector-code.b64"
launch S12 "$B" burn --net "$N" --from "$W/relay-1.json" --relay "$W/relay-1.json"
launch S14 "$B" run --net "$N" --config-code "@$run/config-code.b64" --force --from "$W/relay-2.json" --relay "$W/relay-2.json"
launch S17 "$B" run --net "$N" --elector-code "@$run/elector-code.b64" --force --from "$W/relay-4.json" --relay "$W/relay-4.json"
r10=$(at 10)
for s in S12 S14 S17; do finish $s $(( r10 - 20 )); done
check S12 "burn again: the re-appointed dictator is burned too" has S12 'APPLIED'
check S14 "config code upgrade by vote: the config runs the new code" \
    bash -c "grep -q 'APPLIED' '$out/S14.log' && [[ '$(code_hash config)' == '$(hash_of "@$run/config-code.b64")' ]]"
check S17 "elector code upgrade by vote: the elector installs equivalent Solidity-shaped code (wrap-code --elector)" \
    bash -c "grep -q 'APPLIED' '$out/S17.log' && [[ '$(code_hash elector)' == '$(hash_of "@$run/elector-code.b64")' ]] && [[ '$elector_before' != '$(code_hash elector)' ]]"
launch S12-after "$B" dictator-ping --net "$N" --keys "$run/keys/dictator-2.json" --expect rejected
finish S12-after $(( $(date +%s) + 120 ))
check S12-after "the re-appointed dictator's key is rejected after the second burn" exited S12-after 0

# ---------------------------------------------------------------- rounds 10-11: votes without wallets
wait_until "$r10"
launch S13 "$B" run --net "$N" --param 11 --value-json "$P11_ORIG" --force --ttl 3600 --external --from "$W/relay-3.json"
r12=$(at 12)
finish S13 $(( r12 - 20 ))
check S13 "external votes (no wallet) on the upgraded config code restore p11" \
    bash -c "grep -q 'APPLIED' '$out/S13.log' && grep -q 'by external message' '$out/S13.log'"

# ---------------------------------------------------------------- the network afterwards
"$B" status --net "$N" >"$out/final-status.log" 2>&1
check final "the dictator is burned and no proposal is left" bash -c "grep -q 'burned (not a curve point' '$out/final-status.log' && grep -q 'proposals   none' '$out/final-status.log'"
check final-health "every node still validates" bash -c "for i in 1 2 3; do docker exec $project-node\$i-1 console -C /node/console.json -c getstats 2>&1 | grep -q '\"validation_status\": \"Active\"' || exit 1; done"
note "done: $pass passed, $fail failed"
}

main "$@"
