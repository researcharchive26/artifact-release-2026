#!/usr/bin/env python3
"""repProxy.py -- live reputation proxy between client and rbuilder.

Sits between Dummy-Client and rbuilder as an HTTP pass-through.
Scores each incoming eth_sendBundle by signer reputation, then:
  HIGH  -> forward to rbuilder immediately
  LOW   -> check if builder has enough HIGH bundles this slot
           idle  -> forward (fill the gap)
           busy  -> drop (defense: attacker blocked)

Also exposes eth_testRep so the client can query reputation without
sending a bundle.

Usage:
    python3 repProxy.py                          # defaults: :8560 -> rbuilder :8645
    python3 repProxy.py --idle-threshold 0.3     # forward LOW when HIGH < 30% of gas limit
"""

import argparse
import json
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.error import URLError
from urllib.request import Request, urlopen

from repSimulator import Bundle, ReputationBundler, Transaction

# ---------------------------------------------------------------------------
# LiveBundler: inherits scoring, overrides routing with adaptive release
# ---------------------------------------------------------------------------

SLOT_SECONDS = 12
DEFAULT_GAS_LIMIT = 30_000_000

class LiveBundler(ReputationBundler):
    """ReputationBundler with adaptive LOW release based on builder state.

    Instead of a fixed delay, LOW bundles are forwarded only when the
    builder appears idle (not enough HIGH bundles this slot).  Since the
    proxy is the sole bundle gateway on the local testnet, its own
    per-slot counters reflect the builder's full picture.
    """

    def __init__(self, rbuilder_url: str, idle_threshold: float = 0.5,
                 block_gas_limit: int = DEFAULT_GAS_LIMIT, **kwargs):
        super().__init__(**kwargs)
        self.rbuilder_url = rbuilder_url
        self.idle_threshold = idle_threshold
        self.block_gas_limit = block_gas_limit

        self._pending_raw: dict[str, tuple[bytes, dict]] = {}
        self._slot_high_gas = 0          # total HIGH gas forwarded this slot
        self._slot_low_fwd = 0           # LOW bundles forwarded this slot
        self._slot_low_drop = 0          # LOW bundles dropped this slot
        self._slot_start = time.monotonic()

    # -- slot tracking --------------------------------------------------------

    def _check_new_slot(self) -> None:
        """Reset per-slot counters every SLOT_SECONDS."""
        now = time.monotonic()
        if now - self._slot_start >= SLOT_SECONDS:
            if self._slot_high_gas or self._slot_low_fwd or self._slot_low_drop:
                print(f"[slot] HIGH gas={self._slot_high_gas:,}  "
                      f"LOW fwd={self._slot_low_fwd} drop={self._slot_low_drop}")
            self._slot_high_gas = 0
            self._slot_low_fwd = 0
            self._slot_low_drop = 0
            self._slot_start = now

    # -- override: adaptive routing instead of L-Buffer -----------------------

    def _route_bundle(self, bd: Bundle) -> None:
        """Override: HIGH -> forward; LOW -> forward if idle, drop if busy."""
        self._check_new_slot()
        label = self.assign_queue(bd)
        score = self.calc_rep(self.get_searcher(bd.signer))
        est_gas = sum(tx.gas_used for tx in bd.txs)
        threshold_gas = int(self.block_gas_limit * self.idle_threshold)

        if label == "H":
            self._slot_high_gas += est_gas
            self.queue_h.append((bd, score))
        else:
            if self._slot_high_gas < threshold_gas:
                # Builder idle: not enough HIGH to fill the block -> admit LOW
                self._slot_low_fwd += 1
                print(f"  [idle] slot_high={self._slot_high_gas:,} "
                      f"< threshold={threshold_gas:,} -> forward LOW")
                self.queue_h.append((bd, score))   # route through same path
            else:
                # Builder busy: enough HIGH -> drop LOW (defense)
                self._slot_low_drop += 1
                print(f"  [busy] slot_high={self._slot_high_gas:,} "
                      f">= threshold={threshold_gas:,} -> drop LOW")
                self.results.append((bd.bundle_id, bd.signer, "L_drop", score))
                self._pending_raw.pop(bd.bundle_id, None)

    # -- raw request stash / forward ------------------------------------------

    def stash_raw(self, bundle_id: str, body: bytes, headers: dict) -> None:
        self._pending_raw[bundle_id] = (body, headers)

    def builder_send_bundle(self, bd: Bundle, level: str, score: float) -> None:
        """Override: POST to rbuilder."""
        super().builder_send_bundle(bd, level, score)
        raw = self._pending_raw.pop(bd.bundle_id, None)
        if raw is None:
            return
        body, headers = raw
        try:
            req = Request(self.rbuilder_url, data=body, method="POST")
            for k, v in headers.items():
                if k.lower() not in ("host", "content-length"):
                    req.add_header(k, v)
            req.add_header("Content-Type", "application/json")
            urlopen(req, timeout=15)
            print(f"  -> forwarded [{level}]")
        except Exception as e:
            print(f"  [!] forward failed: {e}")


# ---------------------------------------------------------------------------
# HTTP handler (unchanged from previous version)
# ---------------------------------------------------------------------------

class ProxyHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length)

        try:
            rpc = json.loads(raw_body)
        except json.JSONDecodeError:
            self._reply(400, {"error": "invalid JSON"})
            return

        method = rpc.get("method", "")
        rpc_id = rpc.get("id", 1)

        if method == "eth_sendBundle":
            self._handle_send_bundle(raw_body, rpc)
            self._reply(200, {"jsonrpc": "2.0", "id": rpc_id, "result": None})
        elif method == "eth_testRep":
            result = self._handle_test_rep(rpc)
            self._reply(200, {"jsonrpc": "2.0", "id": rpc_id, "result": result})
        else:
            self._forward_raw(raw_body)
            self._reply(200, {"jsonrpc": "2.0", "id": rpc_id, "result": None})

    def _handle_send_bundle(self, raw_body: bytes, rpc: dict) -> None:
        bundler: LiveBundler = self.server.bundler

        sig_header = self.headers.get("X-Flashbots-Signature", "")
        signer = sig_header.split(":")[0] if ":" in sig_header else "unknown"

        params = rpc.get("params", [{}])
        bp = params[0] if params else {}
        txs_raw = bp.get("txs", [])
        blk_hex = bp.get("blockNumber", "0x0")
        target_block = int(blk_hex, 16) if isinstance(blk_hex, str) else int(blk_hex)

        bundle_id = f"live_{int(time.time() * 1000)}"
        est_gas = 21000 * max(len(txs_raw), 1)

        txs = [Transaction(
            from_addr=signer, to_addr="", value=0,
            gas_price=0, gas_used=est_gas, coinbase_delta=0,
            block_number=bundler.current_block,
        )]
        bd = Bundle(bundle_id=bundle_id, signer=signer, txs=txs)

        raw_headers = {k: v for k, v in self.headers.items()}
        bundler.stash_raw(bundle_id, raw_body, raw_headers)

        searcher = bundler.get_searcher(signer)
        score = bundler.calc_rep(searcher)
        label = bundler.assign_queue(bd)
        print(f"[rSB] {signer[:18]}..  txs={len(txs_raw):>2}  "
              f"score={score:<16.2f}  -> {label}")

        bundler.rep_send_bundle(bd)
        bundler.flush_buffer()

    def _handle_test_rep(self, rpc: dict) -> dict:
        bundler: LiveBundler = self.server.bundler
        params = rpc.get("params", [{}])
        signer = params[0].get("signer", "") if params else ""
        searcher = bundler.get_searcher(signer)
        score = bundler.calc_rep(searcher)
        label = "H" if score >= bundler.cutoff else "L"
        return {"signer": signer, "score": score, "label": label}

    def _forward_raw(self, body: bytes) -> None:
        bundler: LiveBundler = self.server.bundler
        try:
            req = Request(bundler.rbuilder_url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            urlopen(req, timeout=15)
        except Exception:
            pass

    def _reply(self, code: int, body: dict) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, fmt, *args):
        pass


# ---------------------------------------------------------------------------
# Background: chain polling (H_U updates)
# ---------------------------------------------------------------------------

def poll_chain(bundler: LiveBundler, el_url: str, stop: threading.Event):
    last_block = 0
    while not stop.is_set():
        try:
            raw = _rpc(el_url, "eth_blockNumber", [])
            tip = int(raw, 16)
            if tip > last_block:
                for bn in range(max(last_block + 1, tip - 5), tip + 1):
                    _land_block(bundler, el_url, bn)
                bundler.current_block = tip
                last_block = tip
        except Exception:
            pass
        stop.wait(SLOT_SECONDS)


def _land_block(bundler: LiveBundler, el_url: str, block_num: int):
    try:
        block = _rpc(el_url, "eth_getBlockByNumber", [hex(block_num), True])
        if not block or not block.get("transactions"):
            return
        count = 0
        for tx in block["transactions"]:
            signer = tx.get("from", "")
            if not signer:
                continue
            gas_used = int(tx.get("gas", "0x0"), 16)
            gas_price = int(
                tx.get("effectiveGasPrice", tx.get("gasPrice", "0x0")), 16)
            landing = Transaction(
                from_addr=signer, to_addr=tx.get("to", "") or "",
                value=int(tx.get("value", "0x0"), 16),
                gas_price=gas_price, gas_used=gas_used,
                coinbase_delta=0, block_number=block_num,
            )
            bundler.simulate_landing(signer, [landing])
            count += 1
        if count:
            print(f"[chain] block {block_num}: {count} txs -> H_U updated")
    except Exception:
        pass


def _rpc(url: str, method: str, params: list):
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                        "method": method, "params": params}).encode()
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    resp = json.loads(urlopen(req, timeout=5).read())
    return resp.get("result")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Live reputation proxy for rbuilder.")
    ap.add_argument("--port", type=int, default=8560)
    ap.add_argument("--rbuilder", default="http://127.0.0.1:8645",
                    help="rbuilder JSON-RPC endpoint")
    ap.add_argument("--el-node", default="http://127.0.0.1:8550",
                    help="execution-layer node for chain polling")
    ap.add_argument("--window", type=int, default=1000)
    ap.add_argument("--cutoff", type=float, default=5.0)
    ap.add_argument("--idle-threshold", type=float, default=0.5,
                    help="forward LOW when slot HIGH gas < threshold * gas_limit "
                         "(default 0.5 = 50%%)")
    ap.add_argument("--block-gas-limit", type=int, default=DEFAULT_GAS_LIMIT,
                    help=f"block gas limit (default {DEFAULT_GAS_LIMIT:,})")
    args = ap.parse_args()

    bundler = LiveBundler(
        rbuilder_url=args.rbuilder,
        buffer_size=1,
        window=args.window,
        cutoff=args.cutoff,
        idle_threshold=args.idle_threshold,
        block_gas_limit=args.block_gas_limit,
    )

    stop = threading.Event()
    threading.Thread(target=poll_chain,
                     args=(bundler, args.el_node, stop), daemon=True).start()

    HTTPServer.allow_reuse_address = True
    server = HTTPServer(("127.0.0.1", args.port), ProxyHandler)
    server.bundler = bundler

    idle_pct = int(args.idle_threshold * 100)
    print(f"repProxy: :{args.port} -> {args.rbuilder}")
    print(f"  EL node  : {args.el_node}")
    print(f"  window={args.window}  cutoff={args.cutoff}  "
          f"idle_threshold={idle_pct}%")
    print(f"  LOW forwarded when slot HIGH gas < "
          f"{int(args.block_gas_limit * args.idle_threshold):,}")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nrepProxy: shutting down")
        stop.set()
        server.shutdown()


if __name__ == "__main__":
    main()