#!/usr/bin/env python3
"""gen1.py -- build H.csv (landed history) and bundle.json from bundles JSON.

bundles.json holds only on-chain (landed) Flashbots bundles, so every bundle
belongs to the landed history H.  This script reserves the LAST 1000 BLOCKS of
the dataset as the "incoming" bundles to be scored later by repSimulator.py:

    split_block = max(block_number) - 1000

    block_number <= split_block  -> history   -> H.csv       (this script)
                                              -> S.csv       (gen2.py)
    block_number  > split_block  -> incoming  -> bundle.json  (this script)

So H.csv / S.csv EXCLUDE the last 1000 blocks, and bundle.json contains only
them.  H.csv is flat CSV (one row per transaction); bundle.json is a JSON
array of bundle objects, with the incoming transactions regrouped into their
bundles by (block_number, bundle_index).  gen1.py writes H.csv and
bundle.json; gen2.py writes S.csv.

Usage:  python3 gen1.py [file ...]      # default: bundles.json

Multiple JSON files may be passed (e.g. bundles.json bundles2.json
bundles3.json); they are streamed in order and their transactions are pooled
before the split.  Each input is never loaded whole: it is streamed in 4 MiB
windows and decoded one bundle object at a time (see iter_bundles).
"""

import csv
import json
import sys
from itertools import groupby

DEFAULT_INPUT = "bundles.json"
CHUNK_SIZE = 4 * 1024 * 1024   # streaming read window (characters)
LAST_BLOCKS = 1000             # blocks reserved as incoming bundles
PROGRESS_EVERY = 50_000        # progress print cadence (bundle entries)

H_HEADER = ("signer", "block_number", "gas_used",
            "gas_price", "coinbase_transfer", "to_address")


def to_int(value, default=0):
    """JSON value (int, decimal str, None, or missing) -> int."""
    if value is None or value == "":
        return default
    return int(value)


def iter_bundles(path):
    """Yield bundle objects from a file of comma-separated, CRLF-delimited
    JSON objects without loading the file into memory.

    A CHUNK_SIZE window is refilled on demand and json.JSONDecoder.raw_decode
    pulls one object at a time, so peak memory stays near 2 * CHUNK_SIZE.
    """
    decoder = json.JSONDecoder()
    buf = ""
    idx = 0
    eof = False
    with open(path, "r", encoding="utf-8") as fh:
        while True:
            start = buf.find("{", idx)
            if start == -1:                  # no object start in the window
                if eof:
                    return
                buf = buf[idx:]
                idx = 0
                chunk = fh.read(CHUNK_SIZE)
                if chunk:
                    buf += chunk
                else:
                    eof = True
                continue
            try:
                obj, end = decoder.raw_decode(buf, start)
            except json.JSONDecodeError:     # object straddles the window end
                if eof:
                    return
                buf = buf[start:]
                idx = 0
                chunk = fh.read(CHUNK_SIZE)
                if chunk:
                    buf += chunk
                else:
                    eof = True
                continue
            yield obj
            idx = end
            if idx > CHUNK_SIZE:             # drop the consumed prefix
                buf = buf[idx:]
                idx = 0


def extract_rows(paths):
    """Stream each bundles JSON file in `paths` once.  Return (rows, max_block).

    All files' transactions are pooled into one `rows` list, and max_block is
    tracked globally across every file so the split point is the same whether
    one or several files are processed.

    rows item: (block_number, signer, gas_used, gas_price,
                coinbase_transfer, to_address, tx_index, bundle_index)
    """
    rows = []
    max_block = 0
    entries = 0
    skipped = 0
    for path in paths:
        print(f"  parsing {path} ...")
        file_start = entries
        for obj in iter_bundles(path):
            entries += 1
            if entries % PROGRESS_EVERY == 0:
                print(f"  ...{entries:,} bundle entries parsed")
            fallback_block = obj.get("block_number")
            for tx in obj.get("transactions", ()):
                signer = tx.get("eoa_address")
                if not signer:               # cannot attribute to a searcher
                    skipped += 1
                    continue
                block = to_int(tx.get("block_number", fallback_block))
                rows.append((
                    block,
                    sys.intern(signer),
                    to_int(tx.get("gas_used")),
                    to_int(tx.get("gas_price")),          # string in JSON
                    to_int(tx.get("coinbase_transfer")),  # string in JSON
                    tx.get("to_address") or "",
                    to_int(tx.get("tx_index")),
                    to_int(tx.get("bundle_index", 0)),
                ))
                if block > max_block:
                    max_block = block
        print(f"  {path}: {entries - file_start:,} bundle entries "
              f"({entries:,} total so far)")
    print(f"  parsing complete: {entries:,} bundle entries, "
          f"{len(rows):,} transactions"
          + (f", {skipped:,} skipped (no eoa_address)" if skipped else ""))
    return rows, max_block


def write_csv(path, header, records):
    """Write a header row followed by an iterable of record tuples."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(records)


def main():
    sources = []
    split_n = 0  # default: no split, all bundles -> bundle.json
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--split" and i + 1 < len(sys.argv):
            split_n = int(sys.argv[i + 1])
            i += 2
        else:
            sources.append(sys.argv[i])
            i += 1
    if not sources:
        sources = [DEFAULT_INPUT]
    print(f"gen1.py: streaming {len(sources)} file(s): {sources}")

    rows, max_block = extract_rows(sources)
    if not rows:
        sys.exit("gen1.py: no transactions found -- aborting")

    unique_signers = len({r[1] for r in rows})
    split_block = max_block - split_n if split_n > 0 else min(r[0] for r in rows) - 1
    print(f"gen1.py: max block {max_block:,}; split block {split_block:,} "
          f"(blocks > {split_block:,} -> bundle.json)")

    history, incoming = [], []
    for r in rows:
        (history if r[0] <= split_block else incoming).append(r)
    del rows

    # H.csv: flat CSV, one row per tx, sorted by block_number then signer.
    history.sort(key=lambda r: (r[0], r[1]))
    write_csv("H.csv", H_HEADER,
              ((sg, bn, gu, gp, cb, ta)
               for bn, sg, gu, gp, cb, ta, ti, bi in history))

    # bundle.json: incoming txs regrouped into their bundles.  Each bundle is
    # one group of rows sharing (block_number, bundle_index); the array is
    # sorted by (block_number, bundle_index) and txs within a bundle by
    # tx_index.
    incoming.sort(key=lambda r: (r[0], r[7], r[6]))
    bundle_list = []
    for (block, bundle_index), group in groupby(
            incoming, key=lambda r: (r[0], r[7])):
        txs = [
            {
                "eoa_address": sg,
                "to_address": ta,
                "gas_used": gu,
                "gas_price": gp,
                "coinbase_transfer": cb,
            }
            for _, sg, gu, gp, cb, ta, _, _ in group
        ]
        bundle_list.append({
            "bundle_id": f"bd_{block}_{bundle_index}",
            "bundle_index": bundle_index,
            "block_number": block,
            "txs": txs,
        })
    with open("bundle.json", "w", encoding="utf-8") as fh:
        json.dump(bundle_list, fh, indent=2)
        fh.write("\n")

    print("gen1.py: done")
    print(f"  total transactions : {len(history) + len(incoming):,}")
    print(f"  unique signers     : {unique_signers:,}")
    print(f"  H.csv              : {len(history):,} txs "
          f"(blocks <= {split_block:,})")
    print(f"  bundle.json        : {len(bundle_list):,} bundles")


if __name__ == "__main__":
    main()