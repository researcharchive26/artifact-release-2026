import json
import requests
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct


######### Dummy Client #########

SENDER_PRIVATE_KEY = "YOUR_SENDER_PRIVATE_KEY"
CLIENT_SIGNER_PRIVATE_KEY = "YOUR_BUNDLE_SIGNER_PRIVATE_KEY"

sender = Account.from_key(SENDER_PRIVATE_KEY)
client_signer = Account.from_key(CLIENT_SIGNER_PRIVATE_KEY)


def sign_tx(nonce):
    tx = {
        "type": 2,
        "chainId": 33333,
        "nonce": nonce,
        "to": sender.address,
        "value": 0,
        "gas": 21000,
        "maxFeePerGas": Web3.to_wei(1, "gwei"),
        "maxPriorityFeePerGas": Web3.to_wei(0.1, "gwei"),
        "data": b"",
    }
    return sender.sign_transaction(tx)


def client_send_bundle():
    signed1 = sign_tx(0)
    signed2 = sign_tx(1)

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_sendBundle",
        "params": [
            {
                "txs": [
                    signed1.raw_transaction.hex(),
                    signed2.raw_transaction.hex(),
                ],
                "blockNumber": hex(25031000),
            }
        ],
    }

    body = json.dumps(payload, separators=(",", ":"))

    msg = encode_defunct(text=Web3.keccak(text=body).hex())
    sig = Account.sign_message(
        msg,
        CLIENT_SIGNER_PRIVATE_KEY
    ).signature.hex()

    headers = {
        "Content-Type": "application/json",
        "X-Flashbots-Signature": f"{client_signer.address}:{sig}",
    }

    return reputation_server_receive_bundle(body, headers)


######### Reputation Server #########

bundle_buffer = []


def reputation_server_receive_bundle(body, headers):
    BUILDER_ENDPOINT = "http://127.0.0.1:8545"

    bundle_buffer.append({
        "body": body,
        "headers": headers,
    })

    req = bundle_buffer.pop(0)

    payload = json.loads(req["body"])
    txs = payload["params"][0]["txs"]
    target_block = payload["params"][0]["blockNumber"]

    print("reputation server received bundle")
    print("target block:", target_block)
    print("num txs:", len(txs))

    r = requests.post(
        BUILDER_ENDPOINT,
        data=req["body"],
        headers=req["headers"],
        timeout=15,
    )

    return r.text


if __name__ == "__main__":
    print(client_send_bundle())
