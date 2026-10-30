"""Print public on-chain evidence for a managed test node."""
import json
import os
from pathlib import Path
import subprocess
import sys

from evernode import nodes, scripts, storage

store = storage.NodeStateStore(Path(os.environ['E2E_BASE']) / sys.argv[1])
name = sys.argv[2]
config = store.load_node_config(name)
profile = config['wallet']
environment = dict(os.environ, E2E_WALLET=profile['wallet_address'], E2E_DEPOOL=profile['depool_address'])
program = r'''
set -eo pipefail
source ./env.sh
source ./functions.shinc
pending=$(Get_MSIG_Trans_List "$E2E_WALLET")
info=$(Get_DP_Info "$E2E_DEPOOL")
rounds=$(Get_DP_Rounds "$E2E_DEPOOL")
participants=$(Get_DP_Parts_List "$E2E_DEPOOL")
if jq -e --arg wallet "$E2E_WALLET" '.participants | index($wallet) != null' <<< "$participants" >/dev/null; then
    participant=$(Get_DP_Part_Info "$E2E_DEPOOL" "$E2E_WALLET")
else
    participant='{"error":"participant absent"}'
fi
jq -cn --argjson p "$pending" --argjson i "$info" --argjson r "$rounds" --argjson s "$participant" \
    '{pending: $p, depool: $i, rounds: $r, participant: $s}'
'''
result = subprocess.run(['bash', '-c', program], cwd=store.node_directory(name) / 'scripts',
                        env=environment, capture_output=True, text=True, check=True)
evidence = json.loads(result.stdout.strip().splitlines()[-1])
evidence['stage'] = config['setup_stage']
evidence['wallet'] = scripts.verify_imported_safe(store, config, profile['wallet_address'])
evidence['stats'] = nodes.fetch_node_statistics(config)
print(json.dumps(evidence, indent=2))
