"""Fund the next pooling round for the short-round election exercise."""
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
if [[ $E2E_ACTION == tick ]]; then
    payload='te6ccgEBAQEABgAACCiAmCM='
    value=1000000000
else
    payload=$($CALL_CLI -j body --abi "$INPL_DePool_ABI" addOrdinaryStake '{"stake":50000000000000}' | jq -er .Message)
    value=50000500000000
fi
$CALL_CLI message --raw --output "$INPL_ELECTIONS_WORK_DIR/second-round-stake.boc" \
  --lifetime 600 \
  --sign "$INPL_KEYS_DIR/${VALIDATOR_NAME}_1.keys.json" --abi "$INPL_SafeC_Wallet_ABI" \
  "$wallet" submitTransaction \
  "{\"dest\":\"$depool\",\"value\":$value,\"bounce\":true,\"allBalance\":false,\"payload\":\"$payload\"}" >/dev/null
id=$(Send_Message "$wallet" "$depool" "$INPL_ELECTIONS_WORK_DIR/second-round-stake.boc")
Send_MSIG_Trans_Confirmation "$id" "$wallet" "${VALIDATOR_NAME}_2.keys.json"
echo "$E2E_ACTION: transaction $id confirmed"
'''
subprocess.run(['bash', '-c', program], cwd=root / 'scripts', check=True,
               env=dict(os.environ, E2E_ACTION='tick' if sys.argv[2:] == ['tick'] else 'stake'))
