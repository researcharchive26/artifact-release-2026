# Standalone Reputation Simulator

## Run

```bash
cd standalone-simulator
make clean && make     # -> result.csv
```


## Parameters

| Makefile var | repSimulator flag | Default | Meaning |
| --- | --- | --- | --- |
| `WINDOW` | `--window` | 1000 | Reputation window W (blocks) |
| `CUTOFF` | `--cutoff` | 5.0 | HIGH/LOW threshold on r(U,W) |
| `LOW_DELAY` | `--low-delay` | 1 | LOW bundle waits N blocks in L-Buffer before submission |
| `LANDING_DELAY` | `--landing-delay` | 2 | Bundle lands N blocks after submission (dummy builder delay) |

HIGH bundles are submitted immediately and land after `LANDING_DELAY` blocks.
LOW bundles wait `LOW_DELAY` blocks, then are submitted, then land after
`LANDING_DELAY` more blocks.

## Data flow

```
Bundle-sample.json  -->  gen1.py  -->  H.csv  (pre-loaded history, empty by default)
                                       bundle.json  (all bundles to score)
                    -->  gen2.py  -->  S.csv  (pre-loaded submitted, empty by default)

H.csv + S.csv + bundle.json  -->  repSimulator.py --mode dynamic  -->  result.csv
```

`gen1.py`/`gen2.py` accept `--split N` to reserve the last N blocks as
incoming bundles and put earlier data into H.csv/S.csv as pre-loaded history.
Default is `--split 0` (no split: all bundles scored, history builds from zero).

## Output

`result.csv`: one row per bundle with `bundle_id, signer, label, score`.
Terminal summary shows total HIGH/LOW counts.
