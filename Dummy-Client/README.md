# Dummy Flashbots Reputation Server

This project is a local dummy prototype for the following architecture:

```text
Dummy Client  ->  Reputation Server  ->  Flashbots rbuilder
```

The dummy client creates two signed Ethereum transactions, wraps them into an `eth_sendBundle` request, signs the bundle request with `X-Flashbots-Signature`, and sends it to the local reputation server. The reputation server stores the request in a buffer and forwards it to the builder.

---

# Prerequisites

> **Windows users**: Install [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) first (`wsl --install`), then run all commands below inside WSL.

```bash
# Docker (required by builder-playground)
# Linux (Ubuntu/Debian):
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
# macOS: install Docker Desktop from https://docs.docker.com/desktop/install/mac-install/

# System build dependencies (required to compile rbuilder)
# Linux (Ubuntu/Debian):
sudo apt-get update && sudo apt-get install -y build-essential pkg-config libssl-dev libclang-dev protobuf-compiler
# macOS: xcode-select --install && brew install protobuf

# Rust (required to build rbuilder)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source ~/.cargo/env
export PROTOC=$(which protoc)

# builder-playground
curl -sSfL https://raw.githubusercontent.com/flashbots/builder-playground/main/install.sh | bash

# Python dependencies
pip3 install web3 eth-account requests
```

---

# 1. Prepare a Private Blockchain

Start a local L1 environment with [builder-playground](https://github.com/flashbots/builder-playground):

```bash
builder-playground start l1
```

This starts Reth (EL), Lighthouse (CL), and mev-boost-relay. The pre-funded accounts use [Foundry's default private keys](https://github.com/flashbots/builder-playground#static-prefunded-accounts) with nonce starting from `0`.

The code hardcodes `sign_tx(0)` and `sign_tx(1)` (nonce 0 and 1). If your sender account nonce is not `0`, update the nonce values in the Python code.

---

# 2. Run Flashbots rbuilder

In a new terminal, clone and run [rbuilder](https://github.com/flashbots/rbuilder) against the playground environment:

```bash
git clone https://github.com/flashbots/rbuilder.git
cd rbuilder
cp examples/config/rbuilder/config-playground.toml my-config.toml
python3 -c "import os; p='my-config.toml'; open(p,'w').write(open(p).read().replace('\$HOME',os.environ['HOME']))"
cargo run --bin rbuilder run my-config.toml
```

rbuilder accepts `eth_sendBundle` on port `8645` by default (`jsonrpc_server_port` in the config). Update `BUILDER_ENDPOINT` in the Python code if your port differs:

```python
BUILDER_ENDPOINT = "http://127.0.0.1:8645"
```

---

# 3. Replace Private Key Placeholders

```bash
cd Dummy-Client/

python3 -c "
p='Dummy-Client-ReputationServer.py'
t=open(p).read()
t=t.replace('YOUR_SENDER_PRIVATE_KEY','0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80')
t=t.replace('YOUR_BUNDLE_SIGNER_PRIVATE_KEY','0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80')
open(p,'w').write(t)
"
```

`SENDER_PRIVATE_KEY` signs the actual Ethereum transactions.

`CLIENT_SIGNER_PRIVATE_KEY` signs the Flashbots bundle request header (`X-Flashbots-Signature`).

For simple local testing, these two keys can be the same.

---

# 4. Run the Program

```bash
python3 Dummy-Client-ReputationServer.py
```

---

# Notes

- This is a dummy prototype. It does not dynamically query the account nonce and balance.
- Update `chainId` in the code to match your local chain if needed (check with `curl -s -X POST http://127.0.0.1:8545 -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}'`).
