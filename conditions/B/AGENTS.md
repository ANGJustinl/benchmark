Implement the supplied public requirements. Keep existing documented behavior.
You may choose and change internal structure. Do not modify the evaluation protocol.
Add and run relevant public tests. Report only checks actually executed.
Use the task-declared language and toolchain; standard libraries only for this pilot.
Do not assume access to future requirements, evaluator files, or external services.

# Design judgment
Keep related behavior cohesive and independently changing responsibilities separable.
Add a boundary when it solves a current correctness, testing, or change-isolation problem.
Do not add layers, interfaces, or dependencies solely for hypothetical future flexibility.
Consider a simpler direct implementation, but do not trade away required invariants.
Keep unrelated behavior stable. Internal refactoring is allowed when it helps the change.
No required architecture, layer count, file count, or design document.
