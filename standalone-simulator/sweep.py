#!/usr/bin/env python3
"""sweep.py -- run exp6setting1 at multiple cutoff values.

For each cutoff C, runs the experiment and sums all adv2 fees → F.
Outputs result_sweep.csv with X=C, Y=F for plotting.

Usage:
    python3 sweep.py
    python3 sweep.py --cutoffs 1e9,5e9,20e9
"""

import argparse
import csv
import os
import subprocess
import sys


def run_one(cutoff):
    cutoff_gwei = int(cutoff / 1e9)

    subprocess.run([
        sys.executable, "prep_experiment.py", "ex6",
        "--setting", "multi", "--cutoff", str(cutoff),
    ], capture_output=True)

    detail_dir = "sweep_detail"
    os.makedirs(detail_dir, exist_ok=True)
    out_csv = os.path.join(detail_dir,
                           f"result_exp6setting1_cutoff{cutoff_gwei}gwei.csv")

    subprocess.run([
        sys.executable, "repSimulator.py",
        "--mode", "dynamic",
        "--cutoff", str(cutoff),
        "--low-delay", "1",
        "--landing-delay", "2",
        "--output", out_csv,
    ], capture_output=True)

    total_fee = 0
    with open(out_csv, newline="") as f:
        for r in csv.DictReader(f):
            if "adv2" in r["bundle_id"]:
                total_fee += int(r["fee_wei"])

    return cutoff_gwei, total_fee


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoffs",
                    default="1e9,5e9,10e9,20e9,50e9,100e9,200e9,500e9,800e9,1000e9")
    args = ap.parse_args()

    cutoffs = [float(c) for c in args.cutoffs.split(",")]

    print(f"Sweep: {len(cutoffs)} cutoff values")
    rows = []
    for c in cutoffs:
        cg, fee = run_one(c)
        rows.append((cg, fee))
        print(f"  C={cg:>6} gwei  F={fee:>20,} wei  ({fee/1e18:.6f} ETH)")

    out = "result_sweep.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["X", "Y"])
        for cg, fee in rows:
            w.writerow([cg, fee])

    print(f"\nOutput: {out}")


if __name__ == "__main__":
    main()