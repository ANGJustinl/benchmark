---
name: design-check
description: Review design tradeoffs when a change alters ownership across responsibilities, introduces a hard-to-reverse contract, or changes a correctness boundary. Not needed for ordinary local edits.
---

# Design judgment
Keep related behavior cohesive and independently changing responsibilities separable.
Add a boundary when it solves a current correctness, testing, or change-isolation problem.
Do not add layers, interfaces, or dependencies solely for hypothetical future flexibility.
Consider a simpler direct implementation, but do not trade away required invariants.
Keep unrelated behavior stable. Internal refactoring is allowed when it helps the change.
No required architecture, layer count, file count, or design document.
