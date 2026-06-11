BUNDLES ?= Bundle-sample.json
WINDOW  ?= 1000
CUTOFF  ?= 5000000000
LOW_DELAY ?= 1
LANDING_DELAY ?= 2
MAX_LAND ?= 0

CUTOFF_GWEI = $(shell python3 -c "print(int($(CUTOFF)/1e9))")

SIM = python3 repSimulator.py --mode dynamic \
	--window $(WINDOW) --cutoff $(CUTOFF) \
	--low-delay $(LOW_DELAY) --landing-delay $(LANDING_DELAY) \
	--max-land $(MAX_LAND)

# 500-bundle evaluation (gen1/gen2 flow)
all: result.csv

H.csv S.csv bundle.json: $(BUNDLES)
	python3 gen1.py $(BUNDLES)
	python3 gen2.py $(BUNDLES)

result.csv: H.csv S.csv bundle.json repSimulator.py
	$(SIM)

# Baseline
exp1:
	python3 prep_experiment.py baseline --cutoff $(CUTOFF)
	$(SIM) --output result_exp1.csv

# ex5 setting 1: attack only, 10 signers
exp5setting1:
	python3 prep_experiment.py ex5 --setting multi --cutoff $(CUTOFF)
	$(SIM) --output result_exp5setting1_cutoff$(CUTOFF_GWEI)gwei.csv

# ex5 setting 2: attack only, 1 signer, partial landing
exp5setting2:
	python3 prep_experiment.py ex5 --setting single --cutoff $(CUTOFF)
	$(SIM) --max-land 1 --output result_exp5setting2_cutoff$(CUTOFF_GWEI)gwei.csv

# ex6 setting 1: bootstrap + attack, 10 signers
exp6setting1:
	python3 prep_experiment.py ex6 --setting multi --cutoff $(CUTOFF)
	$(SIM) --output result_exp6setting1_cutoff$(CUTOFF_GWEI)gwei.csv

# ex6 setting 2: bootstrap + attack, 1 signer, partial landing
exp6setting2:
	python3 prep_experiment.py ex6 --setting single --cutoff $(CUTOFF)
	$(SIM) --max-land 1 --output result_exp6setting2_cutoff$(CUTOFF_GWEI)gwei.csv

# Sweep: C vs total bootstrapping fee
sweep:
	python3 sweep.py

clean:
	rm -f H.csv S.csv bundle.json result*.csv
	rm -rf sweep_detail

.PHONY: all clean exp1 exp5setting1 exp5setting2 exp6setting1 exp6setting2 sweep