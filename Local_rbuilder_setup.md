# Local rBuilder + Builder Playground Setup

## Legend

| Prefix | Meaning |
|----------|----------|
| `$` | Local machine terminal |
| `docker#` | shell inside Docker container |

---

## Step 1. Start Builder Playground

```bash
$ git clone https://github.com/flashbots/builder-playground.git

$ cd builder-playground

$ go run main.go start l1 --use-reth-for-validation
```

Keep this terminal running.

---

## Step 2. Verify Playground

```bash
$ docker ps
```

Check Docker network:

```bash
$ docker network ls | grep pumped-jay
```

---

## Step 3. Clone rBuilder

```bash
$ cd ~/Downloads

$ git clone https://github.com/flashbots/rbuilder.git

$ cd rbuilder
```

---

## Step 4. Launch Docker Build Environment

```bash
$ docker run --rm -it \
  --network builder-playground-pumped-jay \
  -v /path/to/rbuilder:/rbuilder \
  -v /path/to/builder-playground-session:/session \
  -v /var/folders/16/9d7f36jj0k5cncqgdfbpyklr0000gp/T/builder-playground/pumped-jay/bind-mount-volumes/volume-el-data:/data_reth \
  rust:1.85-bookworm \
  bash
```

You should now see:

```text
root@xxxxxxxx:/#
```

---

## Step 5. Build rBuilder

If required:

```bash
docker# apt update

docker# apt install -y \
    cmake \
    protobuf-compiler \
    pkg-config \
    libssl-dev \
    clang
```

Build:

```bash
docker# cd /rbuilder

docker# CMAKE_POLICY_VERSION_MINIMUM=3.5 \
cargo build --release -j 1 --bin rbuilder
```

---

## Step 6. Create rBuilder Configuration

```bash
docker# vim examples/config/rbuilder/config-docker-pumped-jay.toml
```

Paste the following content:

```toml
chain = "/session/genesis.json"

reth_datadir = "/data_reth"

relay_secret_key = "5eae315483f028b5cdd5d1090ff0c7618b18737ea9bf3c35047189db22835c48"

el_node_ipc_path = "/data_reth/reth.ipc"

live_builders = ["mgp-ordering"]

enabled_relays = ["playground"]

log_level = "info,rbuilder=debug"

coinbase_secret_key = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

cl_node_url = "http://pumped-jay-beacon-1:3500"

root_hash_use_sparse_trie=true
root_hash_compare_sparse_trie=false
```

---

## Step 7. Run rBuilder

```bash
docker# cd /rbuilder

docker# ./target/release/rbuilder run \
examples/config/rbuilder/config-docker-pumped-jay.toml
```

The process should continue running.

---

## Step 8. Verify Local Blockchain

Open a new Local machine terminal.

Get latest block:

```bash
$ curl -X POST http://localhost:8550 \
  -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}'
```

Example:

```json
{"result":"0x700"}
```

Run it again a few seconds later:

```bash
$ curl -X POST http://localhost:8550 \
  -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}'
```

If the block number increases, the chain is producing blocks.

---

## Step 9. Inspect Latest Block

```bash
$ curl -X POST http://localhost:8550 \
  -H "Content-Type: application/json" \
  --data '{
    "jsonrpc":"2.0",
    "id":1,
    "method":"eth_getBlockByNumber",
    "params":["latest", true]
  }'
```

Example output:

```json
"transactions":[]
```

## Step 10. Send bundle
```bash
$ python3 -m pip install web3 requests

$ vim send_bundle.py
```

```Python
import json
import requests
from web3 import Web3

RPC_URL = "http://localhost:8550"

BUNDLE_RPC_URL = "http://localhost:6069"

PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
FROM_ADDR = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"
TO_ADDR = "0x000000000000000000000000000000000000dEaD"

w3 = Web3(Web3.HTTPProvider(RPC_URL))

chain_id = w3.eth.chain_id
nonce = w3.eth.get_transaction_count(FROM_ADDR)
latest_block = w3.eth.block_number

base_fee = w3.eth.get_block("latest").get("baseFeePerGas", 0)
priority_fee = Web3.to_wei(1, "gwei")
max_fee = base_fee + priority_fee * 2

tx = {
    "chainId": chain_id,
    "nonce": nonce,
    "to": Web3.to_checksum_address(TO_ADDR),
    "value": 1,
    "gas": 21000,
    "maxFeePerGas": max_fee,
    "maxPriorityFeePerGas": priority_fee,
    "type": 2,
}

signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
raw_tx = signed.raw_transaction.hex()

target_block = latest_block + 2
target_block_hex = hex(target_block)

payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "eth_sendBundle",
    "params": [
        {
            "txs": [raw_tx],
            "blockNumber": target_block_hex,
        }
    ],
}

resp = requests.post(BUNDLE_RPC_URL, json=payload)

```

```bash
$ python3 send_bundle.py
```
