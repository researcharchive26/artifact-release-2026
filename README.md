
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




