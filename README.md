# DoTBS-rep-simulator

Experimental designs
---

- Given C, run experiment, obtain total fees of adv2 txs, denotedby F.
- do multiple experiments, with different C that derive different F
- Plot figure with X=C, Y=F

README
---

```
cd standalone-simulator/
python3 repSimulator.py
```

API and architecture
---


```
+--------+   bundle    +------------+  bundle(H)  +---------+   block   
| Client |-----------> | Reputation |-----------> | Builder |---------->
|        |             |            |             | Flashbot|          
+--------+             +------------+             +---------+          
```

- Task 1: Dummy reputation integrated with Client-side sender and Flashbot-builder/rust
- Task 2: Impl reputation simulation.


- U_python.eth_sendBundle → repSim → HIGH → builder_rust
- U_python.eth_sendBundle → repSim → LOW → LightLoad → builder_rust
- U_python.eth_testRep → repSim → HIGH/LOW

- repSim.eth_testRep 
- repSim.eth_sendBundle

Sender-side code: send_bundle() ⇒ if (repSim.testRep() == HIGH) send_bundle()

Integration task
---

- implement reputation-sim as a coprocessor to mempool
    - (sim is periodically triggered; per bundle or per block)
    - sim scans unassigned bundles in mempool
    - for each of them, assign label
    - it stores the bundle-label mapping
- change block-builder behanvior
    - by default, it only admit HIGH bundles into block-building
    - when not enough HIGH, admit LOW bundles to close the gap

Internal simulator task (standalone sim)
---

- Structure: Searcher/signer U ⇒ H_{U,W}, S_{U,W} ⇒ r(U, W), cutoff ⇒ HIGH, LOW 



Undefined behavior
- $g_{tx'}$: How to count for GasUsed in multi-round execution framework?
    1. GasUsed only in the last round
    2. GasUsed so far???
- Does it cover unbundled txs? Does $H_U$?$S_U$ cover unbundled txs?
    - Not cover
- Does it cover tx senders?
    - The signer is HIGH and at least one sender is HIGH ⇒ Bundle HIGH
- How to assign initial score and label (When S_U is empty)?
    - Initial ⇒ LOW
- Does S_U contain unconfirmed bundle in the mempool?
    - Does not contain


