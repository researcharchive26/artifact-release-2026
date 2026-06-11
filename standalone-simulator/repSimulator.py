import argparse
import csv
import json
import sys
from collections import deque
from dataclasses import dataclass, field


# -- Data Structures -------------------------------------------------------

@dataclass
class Transaction:
    from_addr: str
    to_addr: str
    value: int
    gas_price: int       # p_T (wei per gas)
    gas_used: int        # g_T
    coinbase_delta: int  # delta_coinbase_T (wei)
    block_number: int    # which block this tx belongs to, for window W filtering
    onchain: bool = True # whether this tx lands on-chain (H_U) or only in S_U


@dataclass
class Bundle:
    bundle_id: str
    signer: str          # U
    txs: list[Transaction]


@dataclass
class Searcher:
    address: str
    h_u: list[Transaction] = field(default_factory=list)  # landed on-chain
    s_u: list[Transaction] = field(default_factory=list)  # all submitted


# -- Core Logic -------------------------------------------------------------

class ReputationBundler:
    def __init__(self, buffer_size: int, window: int, cutoff: float,
                 low_delay_blocks: int = 1):
        self.buffer_size = buffer_size
        self.window = window
        self.cutoff = cutoff                          # CO
        # A LOW bundle waits this many blocks in the L-Buffer before being
        # submitted to the (dummy) builder.  0 == submit at once like HIGH.
        # Default 1 block (~12s).  HIGH is always submitted immediately.
        self.low_delay_blocks = low_delay_blocks
        self.current_block: int = 0
        self.buffer: deque[Bundle] = deque()
        self.queue_h: deque[tuple[Bundle, float]] = deque()
        # L-Buffer: LOW bundles held until enough blocks have passed.
        # Each entry is (enqueue_block, bundle, score_at_label).
        # The score is snapshotted at label time because S_U/H_U keep
        # changing while the bundle waits.
        self.l_buffer: deque[tuple[int, Bundle, float]] = deque()
        self.searchers: dict[str, Searcher] = {}
        # bSB exit log: one (bundle_id, signer, label, score) per bundle that
        # left the system toward the builder.
        self.results: list[tuple[str, str, str, float]] = []

    def get_searcher(self, address: str) -> Searcher:
        """Get or create per-signer state."""
        if address not in self.searchers:
            self.searchers[address] = Searcher(address=address)
        return self.searchers[address]

    def calc_rep(self, searcher: Searcher) -> float:
        """
        r(U,W) = sum_{H_U,W}(coinbase_delta + g*p) / sum_{S_U,W}(g)
        Returns 0.0 when S_U,W is empty.
        """
        earliest = self.current_block - self.window

        # Denominator: total gas of all submitted txs in window
        s_w = [tx for tx in searcher.s_u
               if tx.block_number >= earliest]
        denom = sum(tx.gas_used for tx in s_w)
        if denom == 0:
            return 0.0

        # Numerator: total value of landed txs in window
        h_w = [tx for tx in searcher.h_u
               if tx.block_number >= earliest]
        numer = sum(tx.coinbase_delta + tx.gas_used * tx.gas_price
                    for tx in h_w)

        return numer / denom

    def assign_queue(self, bd: Bundle) -> str:
        """
        if S(u,w) = empty -> "L"
        else: r(U,W) >= CO -> "H", otherwise "L"
        """
        searcher = self.get_searcher(bd.signer)
        earliest = self.current_block - self.window
        s_w = [tx for tx in searcher.s_u
               if tx.block_number >= earliest]
        if not s_w:
            return "L"
        r = self.calc_rep(searcher)
        return "H" if r >= self.cutoff else "L"

    def _route_bundle(self, bd: Bundle) -> None:
        """Label one bundle and route it: HIGH -> queue_h (drained to the
        builder right away), LOW -> l_buffer (held for low_delay_blocks).
        """
        label = self.assign_queue(bd)
        score = self.calc_rep(self.get_searcher(bd.signer))
        if label == "H":
            self.queue_h.append((bd, score))
        else:
            self.l_buffer.append((self.current_block, bd, score))

    def rep_send_bundle(self, bd: Bundle) -> None:
        """rSB(Bundle bd) -- entry point for incoming bundles."""
        # Record txs to signer's S_U (needed by formula)
        searcher = self.get_searcher(bd.signer)
        searcher.s_u.extend(bd.txs)

        # bf.add(bd)
        self.buffer.append(bd)

        # if (bf.length >= threshold) -> label the batch, drain HIGH now;
        # LOW stays parked in the L-Buffer for its delayed release.
        if len(self.buffer) >= self.buffer_size:
            while self.buffer:
                self._route_bundle(self.buffer.popleft())
            self.drain_high()

    def flush_buffer(self) -> None:
        """Label and route every buffered bundle now, regardless of size.

        rep_send_bundle() only flushes when the buffer reaches buffer_size.
        The scoring pass calls this at each block boundary so no bundle is
        left unlabelled.  HIGH bundles are sent to the builder immediately;
        LOW bundles wait in the L-Buffer (release_low handles them).
        """
        while self.buffer:
            self._route_bundle(self.buffer.popleft())
        self.drain_high()

    def drain_high(self) -> None:
        """Send every queued HIGH bundle to the builder immediately."""
        while self.queue_h:
            bd, score = self.queue_h.popleft()
            self.builder_send_bundle(bd, "H", score)

    def release_low(self) -> None:
        """Submit LOW bundles that have waited low_delay_blocks.

        Called once per block in the replay loop.  The L-Buffer is filled
        in arrival order, so popping from the front while the front is
        due releases exactly the bundles whose wait has elapsed.
        """
        while (self.l_buffer and
               self.current_block - self.l_buffer[0][0] >= self.low_delay_blocks):
            _enqueue_block, bd, score = self.l_buffer.popleft()
            self.builder_send_bundle(bd, "L", score)

    def flush_low(self) -> None:
        """Submit any LOW bundles still in the L-Buffer when the run ends."""
        while self.l_buffer:
            _enqueue_time, bd, score = self.l_buffer.popleft()
            self.builder_send_bundle(bd, "L", score)

    def builder_send_bundle(self, bd: Bundle, level: str,
                            score: float) -> None:
        """bSB() -- the bundle leaves the system toward the builder.

        This is the one place a real submission to rbuilder (an
        eth_sendBundle JSON-RPC call) would happen.  In this build it records
        (bundle_id, signer, label, score) instead of making a network call.
        `score` is the value taken when the label was decided, so it matches
        the label even for a LOW that waited.
        """
        fee = sum(tx.gas_used * tx.gas_price for tx in bd.txs)
        self.results.append((bd.bundle_id, bd.signer, level, score, fee))

    def simulate_landing(self, signer: str,
                         txs: list[Transaction]) -> None:
        """Mark txs as landed on-chain (add to H_U).
        Only txs with onchain=True are added to H_U."""
        searcher = self.get_searcher(signer)
        searcher.h_u.extend(tx for tx in txs if tx.onchain)


# -- File-driven simulation -------------------------------------------------

def load_history(path: str) -> tuple[dict[str, list[Transaction]], int]:
    """Read a gen1/gen2 CSV (H.csv or S.csv) produced by gen1.py / gen2.py.

    Returns ({signer: [Transaction, ...]}, max_block_number). The CSV columns
    are: signer, block_number, gas_used, gas_price, coinbase_transfer,
    to_address.
    """
    per_signer: dict[str, list[Transaction]] = {}
    max_block = 0
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        next(reader, None)                       # discard header row
        for row in reader:
            if not row:
                continue
            signer, block, gas_used, gas_price, coinbase, to_addr = row
            signer = sys.intern(signer)
            block = int(block)
            tx = Transaction(
                from_addr=signer,                # eoa_address is the sender
                to_addr=sys.intern(to_addr),
                value=0,                         # not present in source data
                gas_price=int(gas_price),
                gas_used=int(gas_used),
                coinbase_delta=int(coinbase),
                block_number=block,
            )
            per_signer.setdefault(signer, []).append(tx)
            if block > max_block:
                max_block = block
    return per_signer, max_block


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score Flashbots bundles with the reputation simulator.")
    parser.add_argument("--H", default="H.csv",
                        help="landed-history CSV from gen1.py (default H.csv)")
    parser.add_argument("--S", default="S.csv",
                        help="submitted-history CSV from gen2.py "
                             "(default S.csv)")
    parser.add_argument("--bundle", default="bundle.json",
                        help="incoming bundles JSON from gen1.py "
                             "(default bundle.json)")
    parser.add_argument("--output", default="result.csv",
                        help="output CSV of labels (default result.csv)")
    parser.add_argument("--window", type=int, default=1000,
                        help="reputation window W in blocks (default 1000)")
    parser.add_argument("--cutoff", type=float, default=5e9,
                        help="HIGH/LOW reputation cutoff C (default 5e9 = 5 Gwei/gas)")
    parser.add_argument("--buffer-size", type=int, default=100,
                        help="bundler buffer size (default 100)")
    parser.add_argument("--low-delay", type=int, default=1,
                        metavar="N",
                        help="LOW bundle waits N blocks in the L-Buffer "
                             "before being submitted to the dummy builder "
                             "(default 1). HIGH is submitted immediately. "
                             "0 = submit LOW immediately too.")
    parser.add_argument("--mode", choices=("static", "dynamic"),
                        default="static",
                        help="scoring mode: 'static' scores every bundle "
                             "against the fixed loaded H_U/S_U (constant "
                             "score per signer); 'dynamic' replays bundles "
                             "block-by-block so S_U/H_U evolve "
                             "(default static)")
    parser.add_argument("--no-landing", action="store_true",
                        help="dynamic mode only: never land bundles into "
                             "H_U during the pass -- H_U stays frozen and "
                             "scores decay as S_U grows (ignored if static)")
    parser.add_argument("--landing-delay", type=int, default=0, metavar="N",
                        help="dynamic mode only: land each block's bundles N "
                             "blocks after they are scored (default 0 = same "
                             "block; ignored if static)")
    parser.add_argument("--max-land", type=int, default=0, metavar="N",
                        help="max bundles per signer per block that land "
                             "(0 = unlimited). Bundles beyond N still enter "
                             "S_U but not H_U.")
    args = parser.parse_args()

    sim = ReputationBundler(buffer_size=args.buffer_size,
                            window=args.window,
                            cutoff=args.cutoff,
                            low_delay_blocks=args.low_delay)

    # 1. H.csv -> landed history H_U for every signer.
    print(f"repSimulator: loading landed history    {args.H}")
    h_by_signer, h_max_block = load_history(args.H)
    for signer, txs in h_by_signer.items():
        sim.simulate_landing(signer, txs)

    # 2. S.csv -> submitted history S_U for every signer.
    print(f"repSimulator: loading submitted history {args.S}")
    s_by_signer, _ = load_history(args.S)
    for signer, txs in s_by_signer.items():
        sim.get_searcher(signer).s_u.extend(txs)

    # 3. current_block = max block_number found in H.csv.
    sim.current_block = h_max_block
    print(f"repSimulator: {len(sim.searchers):,} signers loaded; "
          f"current_block={sim.current_block:,}, "
          f"window={sim.window}, cutoff={sim.cutoff}")
    print(f"repSimulator: LOW wait = {args.low_delay} block(s) in L-Buffer "
          f"before submission (HIGH submitted immediately)")

    # 4. Score the incoming bundles from bundle.json -- two modes.
    #
    #    static mode  -- score every bundle against the FIXED H_U / S_U
    #      loaded from the CSVs.  current_block stays at the step-3 value, so
    #      r(U,W) is constant per signer (cached).  rep_send_bundle(),
    #      flush_buffer() and simulate_landing() are not used -- S_U and H_U
    #      never change during the pass.
    #
    #    dynamic mode -- replay the bundles chronologically, block by block,
    #      through rep_send_bundle()/flush_buffer(): S_U grows on submission,
    #      current_block advances (window W slides), and landed bundles grow
    #      H_U so later blocks score against updated history.
    #        --no-landing      freezes H_U (only S_U grows -> scores decay).
    #        --landing-delay N defers each block's landings by N blocks via a
    #                          pending queue (--no-landing takes priority).
    if args.mode == "static":
        cfg = "[mode: static]"
    elif args.no_landing:
        cfg = "[mode: dynamic, landing: off]"
    else:
        cfg = f"[mode: dynamic, landing: on, delay: {args.landing_delay}]"
    print(f"repSimulator: scoring bundles  {args.bundle}  {cfg}")

    with open(args.bundle, "r", encoding="utf-8") as fh:
        bundle_list = json.load(fh)

    def build_bundle(bobj: dict) -> Bundle:
        """Turn one bundle.json object into a Bundle of Transactions."""
        block = int(bobj["block_number"])
        txs = [
            Transaction(
                from_addr=t["eoa_address"],
                to_addr=t["to_address"],
                value=0,
                gas_price=int(t["gas_price"]),
                gas_used=int(t["gas_used"]),
                coinbase_delta=int(t["coinbase_transfer"]),
                block_number=block,
                onchain=str(t.get("onchain", "1")) == "1",
            )
            for t in bobj["txs"]
        ]
        # The true bundle signer is not in on-chain data; use the first tx's
        # eoa_address as the signer identity U.
        signer = txs[0].from_addr if txs else ""
        return Bundle(bundle_id=bobj["bundle_id"], signer=signer, txs=txs)

    if args.mode == "static":
        # Frozen H_U / S_U: assign_queue() + a per-signer score cache, exactly
        # how the simulator scored before the dynamic rSB pass existed.  S_U
        # and H_U are never mutated, so r(U,W) is identical for all of a
        # signer's bundles.
        score_cache: dict[str, float] = {}
        for bobj in bundle_list:
            bd = build_bundle(bobj)
            label = sim.assign_queue(bd)
            if bd.signer not in score_cache:
                score_cache[bd.signer] = sim.calc_rep(
                    sim.get_searcher(bd.signer))
            sim.results.append((bd.bundle_id, bd.signer, label,
                                score_cache[bd.signer]))
    else:
        # Dynamic: chronological, block-by-block replay.
        bundles_by_block: dict[int, list[Bundle]] = {}
        for bobj in bundle_list:
            bundles_by_block.setdefault(
                int(bobj["block_number"]), []).append(build_bundle(bobj))

        landing_enabled = not args.no_landing
        # Deferred landings: (scheduled_block, bundle).
        pending: list[tuple[int, Bundle]] = []

        all_blocks = sorted(bundles_by_block)
        block_range = range(all_blocks[0], all_blocks[-1] + 1)
        for block in block_range:
            block_bundles = bundles_by_block.get(block, [])
            sim.current_block = block                      # slide window W

            # Release deferred landings due at or before this block.
            if landing_enabled and pending:
                due = [pb for pb in pending if pb[0] <= block]
                pending = [pb for pb in pending if pb[0] > block]
                for _, bd in due:
                    sim.simulate_landing(bd.signer, bd.txs)

            # Release due LOW bundles from L-Buffer (block-based).
            sim.release_low()

            if not block_bundles:
                continue

            # Score this block's bundles.
            n_before = len(sim.results)
            for bd in block_bundles:                       # submit: S_U grows
                sim.rep_send_bundle(bd)
            sim.flush_buffer()                             # score + drain HIGH

            # Determine H/L label for each bundle just scored.
            new_results = sim.results[n_before:]
            labels = {r[0]: r[2] for r in new_results}    # bundle_id -> label

            # Schedule landings: HIGH lands sooner, LOW lands later.
            # With --max-land N, only N bundles per signer per block land.
            if landing_enabled:
                land_count: dict[str, int] = {}  # signer -> count this block
                for bd in block_bundles:
                    label = labels.get(bd.bundle_id, "L")
                    if label == "H":
                        delay = args.landing_delay
                    else:
                        delay = args.low_delay + args.landing_delay

                    # Check per-signer per-block landing limit
                    if args.max_land > 0:
                        n = land_count.get(bd.signer, 0)
                        if n >= args.max_land:
                            continue  # S_U already has it, skip H_U
                        land_count[bd.signer] = n + 1

                    if delay == 0:
                        sim.simulate_landing(bd.signer, bd.txs)
                    else:
                        pending.append((block + delay, bd))

        # Land everything still pending after the final block.
        for _, bd in pending:
            sim.simulate_landing(bd.signer, bd.txs)

        # Submit any LOW bundles still in the L-Buffer.
        sim.flush_low()

    # 5. Write result.csv
    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(("bundle_id", "signer", "label", "score", "fee_wei"))
        for bundle_id, signer, label, score, fee in sim.results:
            writer.writerow((bundle_id, signer, label, f"{score:.6f}", fee))

    # 6. Summary.
    high = sum(1 for r in sim.results if r[2] == "H")
    low = sum(1 for r in sim.results if r[2] == "L")
    total_fee = sum(r[4] for r in sim.results)
    print()
    print(f"repSimulator: scored {len(sim.results):,} bundles -> {args.output}")
    print(f"  HIGH (H): {high:,}  (submitted immediately)")
    print(f"  LOW  (L): {low:,}  (submitted after {args.low_delay} block(s) in L-Buffer)")
    if total_fee > 0:
        print(f"  total fee: {total_fee:,} wei ({total_fee/1e18:.6f} ETH)")
    top = sorted(sim.results, key=lambda r: r[3], reverse=True)[:10]
    print(f"  top {len(top)} bundles by score:")
    for rank, (bundle_id, signer, label, score, fee) in enumerate(top, 1):
        fee_str = f"  fee={fee/1e18:.6f}ETH" if fee > 0 else ""
        print(f"    {rank:2}. {bundle_id}  {signer}  "
              f"score={score:.6f}  [{label}]{fee_str}")


if __name__ == "__main__":
    main()