# Benchmarks worth copying

Search this table for a task close to your claim. Each row names the design to copy.

| Benchmark | Design worth copying |
|---|---|
| [Terminal-Bench](https://github.com/laude-institute/terminal-bench) | Per-task review for specificity, solvability, and integrity, and canary strings in task files. Version 2.x uses Harbor's task format. |
| [SWE-bench](https://github.com/SWE-bench/SWE-bench) and [SWE-smith](https://github.com/SWE-bench/SWE-smith) | Behavior tests on a pinned commit. SWE-smith generates bug tasks at scale. |
| [tau-bench](https://github.com/sierra-research/tau-bench) and [tau2-bench](https://github.com/sierra-research/tau2-bench) | A simulated user and a policy document. Grades the final database state. |
| [AutomationBench](https://github.com/zapier/AutomationBench) | Simulated SaaS tools. Tasks hardened with distractors, near-duplicate records, and conflicting policies. Negative checks for collateral changes. |
| [AppWorld](https://github.com/StonyBrookNLP/appworld) | Stateful apps graded by database-state tests, including checks for collateral damage. |
| [ToolSandbox](https://github.com/apple/ToolSandbox) | Stateful tools with implicit dependencies between tool calls. |
| [TheAgentCompany](https://github.com/TheAgentCompany/TheAgentCompany) | A simulated software company with self-hosted services, graded by results and checkpoints. |
| [WebArena](https://github.com/web-arena-x/webarena) | Self-hostable web apps, reset to the initial state after a full evaluation pass. |
| [WorkArena](https://github.com/ServiceNow/WorkArena) | Enterprise workflow tasks on a ServiceNow instance. |
| [OSWorld](https://github.com/xlang-ai/OSWorld) | A desktop VM with snapshots and per-task state checks. |
| [CRMArena-Pro](https://github.com/SalesforceAIResearch/CRMArena) | CRM tasks over two Salesforce organizations, B2B and B2C, with roles and permissions. |
| [FrontierSWE v2](https://www.frontierswe.com/blog/v2) | A non-root agent, a separate verifier image, perturbed public tests, and performance scored by weighted instruction count. |
| [MLE-bench](https://arxiv.org/abs/2410.07095) | A local endpoint that checks the submission format without revealing a score. |
