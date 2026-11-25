# Plan: rewrite the eval-factory skill

This plan sets out what the skill says, where each part lives, and the evidence behind each rule. It draws on three
Fable reads: Eugene Yan's "Lessons from 12 Evals", Hugging Face's "How to build and maintain an agentic evaluation",
and LangChain's `langchain-eval-engineering` skill, which carries less weight. It also draws on the current skill's
files.

## 1. Requirements

| Requirement | What it means for the skill |
|---|---|
| Succinct and dense | Each rule appears once, in one file. Each sentence carries a fact, a number, or an instruction. |
| Explicit | Each phase names its output and its gate. Each rule is an imperative sentence. |
| Self-contained | An agent finishes the work with this folder alone. No step depends on outside context. |
| Clear goal and usage | The first 15 lines state the goal, the five phases, and how to find any file. |
| Files called out and linked | SKILL.md lists every file with what it holds and when to open it. Each step links the file again where the agent uses it. |
| General | No swarmory. The skill teaches roles and phases that apply to any team or a single agent. |
| Autonomous | No approval gate. Spend stays within any budget the user set. |
| Evidence-based | Each rule traces to a number in `references/benchmarks.md`. Rules with no evidence are cut. |

## 2. Rules and their evidence

The skill teaches these rules. Each one appears once, in the phase listed. Y is Yan, HF is Hugging Face, and LC is
LangChain.

| # | Rule | Evidence | Phase |
|---|---|---|---|
| 1 | Write a narrow claim, and name what it leaves untested. | HF limits raman-lab's claim to the fit, the ratio, and the saved file, and names the full lab report as out of scope. HF cites 445 benchmarks reviewed for construct validity. | Brainstorm |
| 2 | Start from an existing open task, then check each part before you trust it. | HF reused raman-fitting, and every component except the harness needed rework. LC's benchmark index points to 13 benchmarks with designs to study. | Brainstorm |
| 3 | Make the judgment the task needs land in observable state. | AutomationBench's criterion that reasoning must commit, for example to a status field (Y) | Brainstorm |
| 4 | Harden tasks with distractors, near-duplicate records, and conflicting policies. Never harden by adding size. | AutomationBench's hardened tasks hold the top model to 9.9% (Y). LC lists legitimate and illegitimate knobs per domain. | Brainstorm, verify |
| 5 | Build data from known parameters with a seeded script, and review it. | HF's eight raman-lab spectra. LC: structured truth in code first, prose generated from fact IDs. | Build |
| 6 | State every checked fact in the instruction, and check nothing else. | TB4: 41 of 65 tasks graded unstated requirements. Fixing them and closing shortcuts moved one model from 63% to 89% (Y). TB 2.1's tests expected Spark SQL while the instruction asked for PostgreSQL (HF). | Build |
| 7 | State the outcome, never the method. | Agents' Last Exam: 83 of 122 instructions say "exact" or "exactly", and one lists all 19 bone names (Y) | Build |
| 8 | Grade the saved final state, not the agent's claim to be done. | 11 of 19 raman-lab rollouts that declared completion first had no results file (HF) | Build |
| 9 | Accept every valid answer. Parse and normalize values before comparing them. | DeepSWE: hidden tests broke valid solutions in 18 of 113 tasks. AutomationBench treated "150,000" and "150000.0" as different values. TB Science failed correct work because of row order. (Y) | Build |
| 10 | Check each value in its role, never as a substring. | 55 to 57% of about 9,700 AutomationBench assertions are contains checks. 94 tasks check a one- or two-digit number (Y). | Build |
| 11 | Use code first. Use a judge only for narrow yes or no questions, and give it the evidence. | ALE is 93% deterministic. Harvey LAB's judge never sees the matter files, and more than 60% of all-pass deliverables had material hallucinations (Y). | Build |
| 12 | Add negative checks for collateral changes. | AutomationBench fails an agent that emails the whole company instead of the named recipient (Y). LC adds a `scope` row to every criteria table. | Build |
| 13 | Run hidden tests on a clean copy, so they can't collide with the solver's own tests. | DeepSWE's collisions caused the 18 of 113 false failures (Y) | Build |
| 14 | Before any model runs, the oracle passes, the empty agent fails, a written shortcut fails, a plausible wrong answer fails, and an alternative valid answer passes. | HF step 5. The raman shortcut matched the ratio on all 8 tasks. Five real rollouts got the ratio right with a bad peak fit, so a ratio-only grader would have passed them (HF figure data). TB checks solvability this way. LC requires six fixtures. | Verify |
| 15 | Run the verifier more than once to catch flakiness. | DeepSWE runs every grader three times (Y) | Verify |
| 16 | Trace every checked fact to a file in the environment, and check that the key is correct and unique. | Harvey LAB removed 17 unsolvable criteria, one of them a date that existed only in the rubric. BrowseComp solvers disagreed with 50 of 367 keys (Y). | Verify |
| 17 | Have an adversarial agent try to pass without doing the work, and repeat it after every fix. | TB runs an adversarial pass, yet reviewers still found 44 of 65 tasks passable by cheating (Y) | Verify |
| 18 | Run several models. A failure they share points to the task, and a failure unique to one model points to that model. | HF: only gpt-oss-120b declared done first, in 22 of 24 runs. Y: audit the check first where several frontier models fail it. | Verify |
| 19 | Read passing and failing traces, and name the cause of each failure. | HF: only 2 of 13 failures were bad fits. The rest were early stops or missing packages. LC lists eight failure causes, each with an action. | Verify |
| 20 | Check that the environment reached the state it recorded. | OSWorld 2.0: 31 to 43% of trajectories were flagged for a state mismatch (Y) | Verify |
| 21 | Fix a fault in its own layer, and never make a task harder to hide a defect. | LC's "revise the correct layer" table and calibration rules | Repair |
| 22 | Rerun every check after any change. | HF step 6 | Repair |
| 23 | Version the task, and log each error with the affected versions. | TB 2.1 fixed 28 of 89 tasks. TB Science had 128 fix issues across 57 of 70 tasks. ALE v1.1 retired 30 tasks and revised 57. (Y, HF) | Repair |
| 24 | Report pass@k and pass^k together. | raman-lab: pass@3 was 7 of 8 and pass^3 was 1 of 8 (HF). Two agents at 70% fall to 70% and 17% over five runs (Y). | Measure |
| 25 | Cluster the standard error by task. | raman-lab: mean 0.46 with an SE of about 0.11. A repeat run scored 10 of 24 against 11 of 24 (HF). | Measure |
| 26 | Report partial credit beside all-pass. | 99 of 100 stars right scored 0. OSWorld 2.0 scored 20.6% all-pass and 54.8% partial (Y). | Measure |
| 27 | Name the harness and provider with every score. | Harness changes alone moved TB scores by up to 29.9 points (HF). TB 2.0 shows 57.8% against 52.1%, and 32.6% against 19.6% (Y). | Measure |
| 28 | Report early stops and false success claims next to cost. | ALE: 17% of failures gave up early. FrontierSWE: 169 of 170 trials stopped within 8 of 20 hours. AutomationBench: 72 to 91% false success claims (Y). | Measure |
| 29 | Measure on runs you did not tune against. | Kept from the current skill (AutomationBench and EvalResearchBench in the old sources) | Measure |

Kept from the current skill, with its own evidence:
- Isolation settings and the exploit table. FrontierSWE v1 was cheated 17 of 17 times through a shared container (BenchJack).
- Intervals for small run counts.
- The decision-call code.
- The known Harbor traps.

## 3. File structure

Every file is listed in SKILL.md's file table and linked again at the step that uses it. Line budgets are upper limits.

| File | Holds | Linked from | Budget |
|---|---|---|---|
| `SKILL.md` | Goal, how to use, file table, roles, five phases with outputs and gates, refund example, finish checklist | | 220 lines |
| `references/verifiers.md` | Verifier code to copy: parsed values, state, hidden tests, properties, path diagnostics, decision calls, judges, judge calibration, error handling, grade record | Build: verifier | 230 |
| `references/hardening.md` | `task.toml` isolation, running submitted code, preflight, perturbation, leaks, exploit and defense table | Build: environment. Verify: hacker. | 95 |
| `references/harbor.md` | Commands: free checks, model runs, reruns, regrades, counting, cleanup, known traps | Verify, Measure | 130 |
| `references/benchmarks.md` | Replaces `sources.md`. One row per benchmark: link, design worth studying, flaw found, number, and the skill rule it backs. LC's design index merges into it. | Brainstorm: find a close task. Every rule's evidence. | 90 |
| `templates/task/` | A complete Harbor task to copy, with stored cases for all six fixtures | Build | Existing files |
| `templates/idea-card.md` | Claim, untested scope, evidence, closest task, shortcut, knob, version log | Brainstorm | 40 |
| `templates/critic-brief.md` | Critic's prompt | Brainstorm gate | 27 |
| `templates/reader-brief.md` | Reader's prompt, with a mode that covers only the instruction | Build gate, Verify | 32 |
| `templates/auditor-brief.md` | Auditor's blind-solve prompt | Verify | 21 |
| `templates/hacker-brief.md` | Hacker's prompt | Verify | 35 |
| `scripts/verify_task.py` | Runs `doctor`, `free` (all model-free checks on Modal), and `cleanup` | Verify | Existing |
| `scripts/lint_task.py` | Checks isolation settings and the reference case without building | Build gate | Existing |
| `scripts/summarize_runs.py` | Counts each run as pass, fail, refusal, or infrastructure failure | Measure | Existing |
| `scripts/probe_odds.py` | Wilson intervals, keep-rule odds, and paired differences | Measure | Existing |
| `scripts/preflight_agent.py` | The Harbor agent that `verify_task.py` uses to run the preflight | Not linked. `verify_task.py` calls it. | Existing |

Changes from the current layout:
- `sources.md` becomes `benchmarks.md`. It doubles as the list of tasks to study.
- `templates/run-spec.md` is deleted. It existed for the approval gate, which is gone.
- `templates/task/tests/cases/` gains `alternative` (another valid answer, scores 1) and `side-effect` (a correct answer plus a collateral change, scores 0), so the template covers all six fixtures.
- `templates/idea-card.md` gains an "Untested" field (rule 1).

## 4. SKILL.md outline

```
Frontmatter: name, description (what it builds; when to use it)
# Eval factory
Goal: 3 lines. A pass means the capability, a fail means its absence. TB4's 41 of 65 shows why.
How to use: 5 bullets. Phases in order. Repair loops to verify. Start mid-way for an existing task.
  The refund task is the worked example. Model runs need no approval.
Files: table of every file. Columns: file, what it holds, when you open it.
Task anatomy: table of task.toml, instruction.md, environment/, solution/, tests/.
Roles: table of builder, critic, reader, auditor, hacker. Columns: phase, job, brief link, verdict.
  Two rules: the builder never reviews its own task, and each reviewer gets a fresh context and only its brief.
## 1. Brainstorm   rules 1-4. Output: idea card. Gate: critic BUILD.
## 2. Build        rules 5-13. Output: task folder with stored cases. Gate: lint passes, reader passes the instruction section.
## 3. Verify       rules 14-20. Output: free-check evidence, read runs, reviewer verdicts. Gate: listed.
## 4. Repair       rules 21-23. Output: fixed task, new stored case per finding. Gate: back to Verify.
## 5. Measure      rules 24-29. Output: report. Gate: every report field present.
Done when: one checklist that points back to the gates, without restating them.
```

Each phase follows one format:
- One sentence that says what the phase produces.
- The rules as imperative bullets. Each rule appears once, with its number inline only when the number changes a decision.
- A refund-task block that shows the phase's actual output.
- A gate line.

## 5. Worked example

Keep the refund task as the threaded example. It is invented, and the skill says so. It covers a stateful service,
side effects, a decision call, a knob, and an exploit in one task. Each phase shows one artifact:

| Phase | Artifact |
|---|---|
| Brainstorm | The filled idea card |
| Build | Two-folder layout, the instruction, and the sentence-to-check table |
| Verify | Stored-case table, one quoted failing step, the knob versions, and reviewer findings |
| Repair | The fix for the delete-charge exploit and its stored case |
| Measure | A report with intervals, pass^k, and partial score |

Real cases appear once each, at the rule they support:
- The raman-lab ratio shortcut, at rule 14.
- AutomationBench's swapped counts, at rule 10.
- TB4's unstated rules, in the goal.

Raman-lab can't be the threaded example. Its tolerances are not published, and it has no side effects.

## 6. Writing rules

- Apply pstack technical-writing and unslop. Write imperatives, use one name per thing, and write no em dashes,
  semicolons, or metaphors.
- State each rule once. LC restates its six fixtures in four files, and the copies drift apart. A file links to a rule
  instead of restating it.
- Every link says what the file holds and when to open it. LC's best table has a "Required output" column. Copy it.
- Use tables for parallel cases and numbered lists for sequences. Write short cells.
- Put numbers next to rules only when they change a decision. All other evidence goes in `benchmarks.md`.
- A file over 130 lines opens with a contents list.

## 7. Left out, and why

| Item | Reason |
|---|---|
| Approval gate before model runs | The owner wants autonomous runs |
| Swarmory job commands | The skill is general. The swarmory mission keeps its own `running-harbor-jobs` skill. |
| LC's World Knowledge Skill, LangSmith trace mining, and per-task human approval | Built for one product team. They conflict with autonomous task building. |
| LC's "one target run can be sufficient" | Conflicts with rules 18, 24, and 25 |
| Multi-turn simulated users as a phase | The 39% figure is from Laban et al., not these benchmarks. Keep it as one row in the build domain table. |
| HealthBench length adjustment, GDPval pairwise win rate, `1 - 1/speedup` | Niche scoring. No transfer evidence. |
| Hub publishing, eval eras, ecosystem counts | Not about building a task |
| Refresh cadence and held-out set sizes | Neither source gives data |

## 8. Execution and proof

1. Write `benchmarks.md` first. Every rule in SKILL.md cites from it.
2. Write SKILL.md to the outline and the 220-line budget.
3. Update the references, templates, and task template cases. Delete `run-spec.md` and `sources.md`.
4. Mechanical checks:
   - Every relative link and anchor resolves.
   - No rule appears in two files. Check with grep on key phrases.
   - The template task's tests pass.
   - The existing `tests/test_lint_task.py` and `tests/test_eval_factory.py` pass.
5. Blind review. Neon through agy and one Fable agent each read the skill cold, with the pstack writing skills. Each
   reports what it would misunderstand and what it would cut.
6. End-to-end proof. A fresh agent with only this folder builds one small task through the verify gate. It runs no
   paid models, only the free checks. Pass means it reached each gate without asking where a file is or what a step
   means.

## 9. Decisions for you

1. Delete `templates/run-spec.md`? Recommendation: yes.
2. Merge `sources.md` into `benchmarks.md` as the list of tasks to study? Recommendation: yes.
3. Keep the refund task as the threaded example? Recommendation: yes, with real cases cited at their rules.


## Appendix: evidence behind the rules

Moved out of the skill so agents never load it.


| Benchmark or audit | Finding | Rule it supports |
|---|---|---|
| Terminal-Bench 4.0 (EY) | 41 of 65 tasks graded unstated requirements. 44 of 65 could be passed by cheating, though each task had an adversarial pass. Fixing both moved one model from 63% to 89%. | SKILL.md goal. Trace every check to an instruction sentence. Send the hacker again after every fix. |
| Terminal-Bench 2.1 (HF) | 28 of 89 tasks were fixed. One task's tests expected Spark SQL while its instruction asked for PostgreSQL. Nine tasks broke when their dependencies drifted. | Trace every check to an instruction sentence. Pin seeds now, and pin the rest when you freeze. |
| DeepSWE (EY) | Correct solutions failed in 23 of 113 tasks, 18 of them because hidden tests collided with tests the agent wrote. Graders run three times to catch flakiness. | Accept every valid answer. Run hidden tests on a clean copy, and run the verifier in two fresh processes (verifiers.md). |
| AutomationBench (EY) | 55% to 57% of about 9,700 assertions only check that a string is contained or excluded. 94 tasks check a one- or two-digit number. Checks rejected valid work in 115 tasks, for example "150,000" against "150000.0". Agents claimed success in 72% to 91% of the failures analyzed. | Check each value in its role. Accept every valid answer. Count false success claims. |
| Harvey LAB (EY) | The judge never saw the matter files, and more than 60% of deliverables that passed every criterion had material hallucinations. 17 unsolvable criteria were removed from the held-out set. In the public set, one criterion required a date that existed only in the rubric. | Give a judge the source evidence. Trace every checked fact to a file the solver can read. |
| OSWorld 2.0 (EY) | Checks rejected valid work in 23 of 108 tasks, one of them an exact match to a short name where the site showed a long one. Possible state mismatches were flagged in 31% to 43% of 108 trajectories. | Accept every valid answer. Check that the environment reached the state its logs recorded. |
| Agents' Last Exam (EY) | 83 of 122 public instructions say "exact" or "exactly". One task lists all 19 bone names the output must have. Scoring is 93% deterministic. 17% of failures gave up early. | State the outcome, never the method. Report the early-stop rate. |
| Terminal-Bench Science (EY) | 128 fix issues were filed against 57 of 70 tasks. One verifier failed correct work because of row order. One run got 99 of 100 stars right and scored 0. | Normalize format and order. Report the partial score. Log each error. |
| Finance Agent (EY) | A rubric required peer averages of three ratios when the question only asked which company was most leveraged. The rubric has since been fixed. | Trace every check to an instruction sentence. |
| BrowseComp (EY) | Human solvers disagreed with the key on 50 of 367 solved questions. | The auditor solves blind, and its answer must score 1. |
| HealthBench Professional (EY) | 47% of tasks have exactly one positive criterion. | Write enough rubric criteria to separate outputs (verifiers.md). |
| GDPval (EY) | About half of the rubric criteria in a sample of a dozen tasks check instruction following, such as a page limit, instead of professional judgment. | Each rubric criterion tests the capability (verifiers.md). |
| FrontierSWE (EY) | In v1, wall-clock scoring made rankings unstable across hosts. One model stopped within 8 of 20 hours in 169 of 170 trials. | Score an optimization deterministically (verifiers.md). Report the early-stop rate. |
| Terminal-Bench 2.0 and 2.1 harnesses (EY, HF) | On 2.0, one model scored 57.8% and 52.1% in two harnesses, and another scored 32.6% and 19.6%. On 2.1, swapping only the harness under one model moved accuracy by up to 29.9 points, and a larger per-task time budget closed that gap. | Report the setup with every score. |
| raman-lab (HF) | A shortcut that divided raw peak heights matched the ratio on all 8 tasks. In the six-model comparison, five runs got the ratio right with a bad peak fit (Figure 8 data). In the gpt-oss-120b development run, 11 of the 19 runs that declared completion in their first response had no results file. That run had pass@3 of 7 of 8 tasks and pass^3 of 1 of 8, with a mean of 0.46 and a task-clustered standard error of about 0.11. | Store the cheapest shortcut as an exploit case. Grade the final state. Report pass@k and pass^k. Use `probe_odds.py set`. |
| raman-fitting (HF) | The instruction left parameters and units undefined, and the reference solution converted them silently. NumPy and SciPy were not preinstalled. The task expects the agent to install them, and the missing packages caused setup failures in raman-lab runs. | Make every unit explicit. Install the tools a practitioner would use. |
| SWE-bench Verified (HF) | Material issues in 59.4% of 138 problems examined. Every frontier model tested could reproduce reference patches or problem details for some tasks. | Keep some instances private (hardening.md). |
| CritPt (HF) | Physicists who solved the problems themselves found errors in 21 of the 56 challenges they reviewed. | The auditor solves blind. |
| FrontierSWE v1 ([BenchJack](https://arxiv.org/abs/2605.12673)) | The agent and the verifier ran in one sandbox. A pre-written reward file and a hijacked `python3` on the path passed the tasks. | Isolate the verifier (hardening.md). |
| [ImpossibleBench](https://arxiv.org/abs/2510.20270) | Tests contradict the spec, so any pass is cheating. LLM monitors caught 86% to 89% of cheating on the LiveCodeBench variant, and 42% to 65% on the SWE-bench variant. | Read every pass by the target model. A monitor misses cheats. |
| tau-bench Airline ([ABC checklist](https://arxiv.org/abs/2507.02825)) | 38% of tasks are unsolvable by design and pass when nothing changes, so a do-nothing agent scores 38%. | The empty case scores 0. |
| PostTrainBench ([paper](https://arxiv.org/abs/2603.08640), [v1.1](https://posttrainbench.com/blog/posttrainbench-1-1/)) | Agents knew the contamination rules, and one acknowledged a rule before breaking it. The v1.1 audit flagged 234 runs for test-set contamination. | Fix exploits in the environment, not in the instruction. |
| [WorkRover](https://arxiv.org/abs/2610.18067) | Regenerating a task changed its intent 14.3% of the time, against 1.3% for the smallest validated repair. | Make the smallest fix. |
| [EvalResearchBench](https://arxiv.org/abs/2610.04184) | The evaluator that scored best on development target benchmarks was not best on sealed ones. This is evidence for holding data out of iteration, and by analogy a model. | Keep one strong model out of every iteration run. |
| [Code-RL audit](https://arxiv.org/abs/2606.16062) | 22.4% of 49 SWE-bench Verified tasks accepted a wrong patch, and 57.8% against a stronger attacker. 23 of 100 SWE-bench Pro tasks accepted one. | Send the hacker before any paid run. |
