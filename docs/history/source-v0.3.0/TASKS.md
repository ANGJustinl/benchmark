# Task matrix 0.2

All levels are provisional design labels. They have not been calibrated on real models.

| Level | Family | Languages | Starting point | Main boundary |
|---:|---|---|---|---|
| 1 | settings | Python / TypeScript / Go | greenfield | configuration precedence and validation |
| 1 | invoice | Python / TypeScript / Go | greenfield | exact monetary arithmetic |
| 2 | events | Python / TypeScript / Go | brownfield | aggregation and versioned consumer contracts |
| 2 | routing | Python / TypeScript / Go | brownfield | provider wire contracts and retry policy |
| 3 | ledger | Python / TypeScript / Go | brownfield | idempotent transfers and atomic rollback |
| 3 | scheduler | Python / TypeScript / Go | brownfield | dependency scheduling and failure propagation |
| 4 | store | Python / TypeScript / Go | brownfield | durable versioned storage and legacy migration |
| 4 | outbox | Python / TypeScript / Go | brownfield | durable delivery, leases and stale acknowledgements |

There are 8 independent authored problem families and 24 language ports, not 24 independent families.
Every port has T0 plus local and extension branches; complete public contracts are in `poly_catalog.py`.
Levels 1-3 are component/state-machine tasks; level 4 additionally executes actual files and fresh processes.
Level 4 does NOT test simultaneous OS writers, live databases, power-loss crash consistency, distributed systems,
or large public repositories. Difficulty increases in semantic/state obligations, not merely line count.

The six v0.1 Python tasks remain a separate legacy track and are not pooled invisibly with the new suite.
