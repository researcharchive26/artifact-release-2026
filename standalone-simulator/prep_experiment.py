#!/usr/bin/env python3
"""prep_experiment.py -- prepare data for reputation evasion experiments.

Experiments (matching rbuilder-integration README):
  ex5: benign + adv1 (attack only, no bootstrapping)
  ex6: benign + adv2 (bootstrapping) + adv1 (attack after bootstrapping)

Settings:
  multi:  each attack bundle from a different signer
  single: all attack bundles from 1 signer, grouped into 3 blocks
"""

import argparse
import json
import math

H_HEADER = "signer,block_number,gas_used,gas_price,coinbase_transfer,to_address"
GROUP_GAP = 10   # blocks between groups in single-signer mode
NUM_GROUPS = 3   # number of block groups in single-signer mode


def load_benign(path):
    with open(path) as f:
        raw = json.load(f)
    bundles = []
    for bd in raw:
        txs = []
        for t in bd["txs"]:
            txs.append({
                "eoa_address": t["from"],
                "to_address": t.get("to", ""),
                "gas_used": str(t["gas"]),
                "gas_price": str(t.get("gasPrice", "0")),
                "coinbase_transfer": str(t.get("coinbaseTransfer", "0")),
                "onchain": "1",
            })
        bundles.append({"signer": txs[0]["eoa_address"], "txs": txs, "tag": "benign"})
    return bundles


def load_adv1(path):
    with open(path) as f:
        raw = json.load(f)

    # Build gasLimit→gasUsed lookup from bundles that have gasUsed
    gas_lookup = {}
    for bd in raw:
        for t in bd["txs"]:
            if "gasUsed" in t:
                gas_lookup[int(t["gasLimit"])] = int(t["gasUsed"])

    bundles = []
    for bd in raw:
        signer = bd["bundle_signer"]
        txs_out = []
        for t in bd["txs"]:
            gas_limit = int(t.get("gasLimit", t.get("gas", 0)))
            gas_used = int(t["gasUsed"]) if "gasUsed" in t else gas_lookup.get(gas_limit, gas_limit)
            cb = str(t.get("value", "0")) if gas_limit == 30000000 else "0"
            txs_out.append({
                "eoa_address": t["from"],
                "to_address": t.get("to", ""),
                "gas_used": str(gas_used),
                "gas_price": str(t.get("gasPrice", "0")),
                "coinbase_transfer": cb,
                "onchain": t.get("onchain", "1"),
            })
        bundles.append({"signer": signer, "txs": txs_out, "tag": "adv1"})
    return bundles


def single_signer(bundles):
    if not bundles:
        return bundles
    signer = bundles[0]["signer"]
    out = []
    for bd in bundles:
        txs = [{**tx, "eoa_address": signer} for tx in bd["txs"]]
        out.append({"signer": signer, "txs": txs, "tag": bd["tag"]})
    print(f"  single-signer: all rewritten to {signer[:14]}...")
    return out


def gen_adv2(adv1_bundles, cutoff, single=False):
    """Generate bootstrapping bundles. Price set to just exceed cutoff.

    multi:  price covers 1 adv2 + 1 adv1 per signer
    single: price covers all adv2 + all adv1 on 1 signer,
            accounting for only NUM_GROUPS landings (max-land=1)
    """
    bootstrap_gas = 21000
    N = len(adv1_bundles)
    attack_gas_per = sum(int(tx["gas_used"]) for tx in adv1_bundles[0]["txs"])

    if not single:
        # Each signer: 1 adv2 + 1 adv1, both land
        total_s = bootstrap_gas + attack_gas_per
        h_value_factor = bootstrap_gas  # 1 adv2 lands
        price = int(cutoff * total_s / h_value_factor) + 1
    else:
        # 1 signer: N adv2 + N adv1, spread across NUM_GROUPS blocks.
        # max-land=1: 1 landing per block. Each block starts with adv2.
        # At the LAST block, only previous blocks' landings are visible
        # (current block's landing hasn't happened yet during scoring).
        total_s = N * bootstrap_gas + N * attack_gas_per
        visible_lands = NUM_GROUPS - 1  # last block can't see its own landing
        h_value_factor = visible_lands * bootstrap_gas
        price = int(cutoff * total_s / h_value_factor) + 1

    cost_eth = bootstrap_gas * price / 1e18
    print(f"  adv2: {N} bundles, price={price/1e9:,.0f} Gwei/gas, "
          f"cost={cost_eth:.4f} ETH each")

    bundles = []
    for bd in adv1_bundles:
        txs = [{
            "eoa_address": bd["signer"],
            "to_address": "0x0000000000000000000000000000000000000000",
            "gas_used": str(bootstrap_gas),
            "gas_price": str(price),
            "coinbase_transfer": "0",
            "onchain": "1",
        }]
        bundles.append({"signer": bd["signer"], "txs": txs, "tag": "adv2"})
    return bundles


def write_files(bundles, benign_history, single=False, start_block=1000):
    """Write bundle.json + H.csv/S.csv."""
    bundle_list = []
    block = start_block

    for bd in bundles:
        if bd["tag"] == "benign":
            bundle_list.append({
                "bundle_id": f"{bd['tag']}_{block}",
                "block_number": str(block),
                "txs": bd["txs"],
            })
            block += 1
        else:
            break

    # Non-benign bundles
    non_benign = [bd for bd in bundles if bd["tag"] != "benign"]
    if not single:
        for bd in non_benign:
            bundle_list.append({
                "bundle_id": f"{bd['tag']}_{block}",
                "block_number": str(block),
                "txs": bd["txs"],
            })
            block += 1
    else:
        # Split into NUM_GROUPS blocks, each starting with adv2 (for landing).
        adv2s = [bd for bd in non_benign if bd["tag"] == "adv2"]
        adv1s = [bd for bd in non_benign if bd["tag"] == "adv1"]
        adv1_per_group = math.ceil(len(adv1s) / NUM_GROUPS) if adv1s else 0

        for g in range(NUM_GROUPS):
            group = []
            # 1 adv2 first (if available) — this one lands
            if adv2s:
                group.append(adv2s.pop(0))
            # Fill with adv1
            take = min(adv1_per_group, len(adv1s))
            group.extend(adv1s[:take])
            adv1s = adv1s[take:]
            # Remaining adv2 fill after
            while adv2s and len(group) < adv1_per_group + 1:
                group.append(adv2s.pop(0))

            for bd in group:
                bundle_list.append({
                    "bundle_id": f"{bd['tag']}_{block}",
                    "block_number": str(block),
                    "txs": bd["txs"],
                })
            block += GROUP_GAP

        # Any leftover adv2 go into the last block used
        for bd in adv2s:
            bundle_list.append({
                "bundle_id": f"{bd['tag']}_{block - GROUP_GAP}",
                "block_number": str(block - GROUP_GAP),
                "txs": bd["txs"],
            })

    with open("bundle.json", "w") as f:
        json.dump(bundle_list, f, indent=2)

    # Pre-load benign history
    hist_block = start_block - 10
    with open("H.csv", "w") as fh, open("S.csv", "w") as fs:
        fh.write(H_HEADER + "\n")
        fs.write(H_HEADER + "\n")
        for bd in benign_history:
            for tx in bd["txs"]:
                row = (f"{tx['eoa_address']},{hist_block},"
                       f"{tx['gas_used']},{tx['gas_price']},"
                       f"{tx['coinbase_transfer']},{tx['to_address']}\n")
                fh.write(row)
                fs.write(row)
            hist_block += 1

    return bundle_list


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exp", choices=["baseline", "ex5", "ex6"])
    ap.add_argument("--setting", choices=["multi", "single"], default="multi")
    ap.add_argument("--benign", default="Benign-trace-3.json")
    ap.add_argument("--adv1", default="cloop-attack-trace.json")
    ap.add_argument("--cutoff", type=float, default=5e9)
    args = ap.parse_args()
    is_single = (args.setting == "single")

    benign = load_benign(args.benign)
    print(f"benign: {len(benign)} bundles")

    if args.exp == "baseline":
        all_bundles = benign
    elif args.exp == "ex5":
        adv1 = load_adv1(args.adv1)
        if is_single:
            adv1 = single_signer(adv1)
        print(f"adv1: {len(adv1)} attack bundles")
        all_bundles = benign + adv1
    elif args.exp == "ex6":
        adv1 = load_adv1(args.adv1)
        if is_single:
            adv1 = single_signer(adv1)
        adv2 = gen_adv2(adv1, args.cutoff, single=is_single)
        print(f"adv1: {len(adv1)} attack bundles")
        all_bundles = benign + adv2 + adv1

    bl = write_files(all_bundles, benign, single=is_single)

    tags = {}
    for bd in all_bundles:
        tags[bd["tag"]] = tags.get(bd["tag"], 0) + 1
    print(f"-> bundle.json: {len(bl)} bundles ({', '.join(f'{t}={c}' for t,c in tags.items())})")


if __name__ == "__main__":
    main()