#!/usr/bin/env python3
"""gen2.py -- build S.csv (submitted bundle history) from bundles JSON.

S_U is the set of bundles a signer U *submitted*.  bundles.json contains only
on-chain (landed) bundles, so for this standalone simulation every submitted
bundle also landed and S_U == H_U: S.csv carries the SAME rows as H.csv.

Like gen1.py, this script reserves the LAST 1000 BLOCKS of the dataset as
incoming bundles and EXCLUDES them from S.csv:

    split_block = max(block_number) - 1000

    block_number <= split_block  -> S.csv
    block_number  > split_block  -> excluded (gen1.py writes them to bundle.json)

gen2.py is intentionally a separate script -- rather than copying H.csv -- so a
future version can parse extra sources and inject synthetic submitted-but-not-
landed bundles, making S_U a strict superset of H_U.  bundle.json is owned by
gen1.py; gen2.py only writes S.csv.

Usage:  python3 gen2.py [file ...]      # default: bundles.json

Multiple JSON files may be passed (e.g. bundles.json bundles2.json
bundles3.json); they are streamed in order and their transactions are pooled
before the split.  Each input is never loaded whole: it is streamed in 4 MiB
windows and decoded one bundle object at a time (see iter_bundles).
"""

import csv
import json
import sys

DEFAULT_INPUT = "bundles.json"
CHUNK_SIZE = 4 * 1024 * 1024   # streaming read window (characters)
LAST_BLOCKS = 1000             # blocks reserved as incoming bundles
PROGRESS_EVERY = 50_000        # progress print cadence (bundle entries)

S_HEADER = ("signer", "block_number", "gas_used",
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
    split_n = 0
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
    print(f"gen2.py: streaming {len(sources)} file(s): {sources}")

    rows, max_block = extract_rows(sources)
    if not rows:
        sys.exit("gen2.py: no transactions found -- aborting")

    total_tx = len(rows)
    unique_signers = len({r[1] for r in rows})
    split_block = max_block - split_n if split_n > 0 else min(r[0] for r in rows) - 1
    print(f"gen2.py: max block {max_block:,}; split block {split_block:,} "
          f"(blocks > {split_block:,} excluded -- they belong to bundle.json)")

    # S_U == H_U here: keep only history (everything up to the split block).
    history = [r for r in rows if r[0] <= split_block]
    del rows

    # S.csv: sorted by block_number ascending, then by signer (same as H.csv).
    history.sort(key=lambda r: (r[0], r[1]))

    write_csv("S.csv", S_HEADER,
              ((sg, bn, gu, gp, cb, ta)
               for bn, sg, gu, gp, cb, ta, ti, bi in history))

    print("gen2.py: done")
    print(f"  total transactions : {total_tx:,}")
    print(f"  unique signers     : {unique_signers:,}")
    print(f"  S.csv              : {len(history):,} txs "
          f"(blocks <= {split_block:,})")


if __name__ == "__main__":
    main()