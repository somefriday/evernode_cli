"""Confirm only a reviewed maintenance tick left pending by an earlier attempt."""
import os
import subprocess
import sys

from readme_workflows import root as case_root

root = case_root(sys.argv[1])
program = r'''
set -eo pipefail
source ./env.sh
source ./functions.shinc
wallet=$(cat "$KEYS_DIR/$VALIDATOR_NAME.addr")
depool=$(cat "$KEYS_DIR/depool.addr")
pending=$(Get_MSIG_Trans_List "$wallet")
ids=$(jq -r --arg dest "$depool" '.transactions[] | select(.dest == $dest and .value == "1000000000" and .bounce == true and .sendFlags == "3" and .payload == "te6ccgEBAQEABgAACCiAmCM=") | .id' <<< "$pending")
for id in $ids; do
    Send_MSIG_Trans_Confirmation "$id" "$wallet" "${VALIDATOR_NAME}_2.keys.json"
    echo "Confirmed reviewed tick $id"
done
for ((attempt=0; attempt<60; attempt++)); do
    pending=$(Get_MSIG_Trans_List "$wallet")
    if [[ $(jq '.transactions | length' <<< "$pending") == 0 ]]; then exit 0; fi
    sleep 2
done
echo 'Maintenance confirmation did not execute on-chain' >&2
exit 1
'''
subprocess.run(['bash', '-c', program], cwd=root / 'scripts', check=True)
