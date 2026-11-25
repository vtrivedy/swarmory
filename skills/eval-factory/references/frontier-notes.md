# How frontier benchmarks solved each problem

Each entry is how one benchmark solved one problem, with the numbers behind a rule and a link to its source. Numbers
are as published.

Model names are as of late 2026. Opus, Sol, Astra and Argon are frontier models from Anthropic, OpenAI and Google.
Luna and Haiku are small, cheap models.

## Questions with short answers

**HLE** ([paper](https://arxiv.org/abs/2501.14249), [FutureHouse audit](https://www.futurehouse.org/research/hle-exam),
[Epoch review](https://epoch.ai/benchmarks/hle/review)). Keep only what stumps every model, then review. The filter also
keeps wrong keys. Reviewers were told a check longer than 5 minutes could be skipped. FutureHouse found about 29% of
the text-only chemistry and biology answers contradicted by published evidence, and Epoch found 22 of 48 sampled
questions defective. Copy HLE-Rolling's change log, which has one line per change with the date, the action and the
question id. On HLE-Diamond, a model found another model's graded attempt through a public rollout API and copied its
answer ([Prime Intellect](https://www.primeintellect.ai/blog/auditing-public-evaluations)).

**BrowseComp** ([paper](https://arxiv.org/abs/2504.12516)). Start from a fact and write a question that is hard to find
and easy to check. Five searches must not surface the answer, and four model configurations must fail: GPT-4o with and
without browsing, o1, and an early Deep Research. 21 of 118 tasks that Deep Research never solved had a wrong, ambiguous
or misformatted key. Copy its cheap check, which hands a model the key and asks it to find the supporting evidence.

**FrontierMath** ([paper](https://arxiv.org/abs/2411.04872)). Answers are integers or expressions checked by SymPy.
Reviewers reject any problem with more than a 1% chance of a lucky guess. A second review of 35 problems found 2 wrong
answers, 6 missing hypotheses and 2 answers that could be guessed. The fix was active review, where the reviewer
solves from the key ideas instead of approving a written solution.

**Humanity's Sixth Sense** ([paper](https://arxiv.org/abs/2610.08966)). Items are easy for humans, with no pure
perception and no expert knowledge. Three review rounds, unanimous approval required, 15% kept. Each reference answer is
split into atomic criteria. The judge never sees the image and still agrees across three vendors at kappa 0.92 or
higher. A blind control with the media removed found prompts whose text gave the answer away. Intervals use a cluster
bootstrap over tasks.

## Real work

**GDPx** ([newsletter](https://newsletter.semianalysis.com/p/gdpx-measuring-frontier-model-capabilities), [Aria's
grading](http://ariadata.ai/gdpx)). One preamble, then a short brief from a colleague. The difficulty lives in the
environment. Eight of eleven domains grade with no language model, using gates then a capped ratio, mutant kill, a
budgeted edit with a clean rebuild, held-out outcomes with a statistical test, and checklists whose checks are the
planted traps. Decoys sit beside true causes. Partners had to pass automated QA on the first try. Its failures were
boards where every model scored 0, partial scores added after the fact, zeros that didn't name the failed gate, and
refusals mixed into capability scores.

## Agents in software and systems

**FrontierSWE v1 and v2** ([v2 repo](https://github.com/Proximal-Labs/frontier-swe-v2), [v2
blog](https://www.frontierswe.com/blog/v2)). All 34 v2 tasks set a non-root agent, a separate verifier from a pinned
image, root-only tests and an allowlisted network. `lock_verifier()` re-locks the tests at grading time. A preflight
script checks every image before paid runs. Tests ship to the agent, and the verifier uses perturbed copies of them with
a fixed seed. Performance is scored by CPU instruction counts, because wall-clock times flipped rankings when
submissions were rescored on another host. In v1, agent and verifier shared one container and one user, and
[BenchJack](https://arxiv.org/abs/2605.12673) scored 17 of 17 with a pre-written reward file and a wrapped `python3`. In
v2, Sol read hidden reference files through the Modal daemon socket and replayed public gold files. v2 deleted v1's hash
checks as "redundant defenses".

**Terminal-Bench 2.0** ([paper](https://arxiv.org/abs/2601.11868)). 229 tasks submitted, 89 kept, about three reviewer
hours each, plus an exploit agent with every trajectory read. Author difficulty correlates with measured difficulty at r
= 0.436. Agents had internet access.

**Terminal-Bench 4.0 audit and port** ([audit](https://preferencemodel.com/blog/terminal-bench-4-is-saturated/), [Epoch
review](https://epoch.ai/benchmarks/terminal-bench-4/review)). 242 issues in 65 tasks. 41 graded a rule the instruction
never stated. The port pins each fix with tests in the task. `test_<hack>_scores_0` switches the hardening off inside
the test to prove the hole was real, and one test per graded rule ties it to its instruction sentence. Verifiers run
submitted code as an unprivileged user on a root-only copy. Regrading stored attempts under the other reading diagnosed
ambiguity at no model cost.

**tau-bench** ([paper](https://arxiv.org/abs/2406.12045), [ABC checklist](https://arxiv.org/abs/2507.02825)). Grades the
final database state against a reference replay. 38% of Airline tasks are intentionally unsolvable and pass when nothing
changes, so a do-nothing agent passes all of them.

**ImpossibleBench** ([paper](https://arxiv.org/abs/2510.20270)). Change one expected value so the tests contradict the
spec. Any pass is cheating. Twins must fail both the gold patch and the empty patch. LLM monitors caught 42% to 65% of
cheating.

**AutomationBench audit** ([Prime Intellect](https://www.primeintellect.ai/blog/auditing-public-evaluations),
[repo](https://github.com/xeophon/AutomationBench-PI)). About 930 read-only auditor agents audited 806 samples and found
2,707 verifier defects: 1,802 false accepts, 832 false rejects and 73 wrong both ways. The biggest classes were missing
assertions, unanchored token matches and unstated format requirements. The auditors checked each fix against a canonical
environment and an exploit environment. The same post found OfficeQA rejecting correct answers over a number-count rule
and a 250-character cap.

## Research and long tasks

**PostTrainBench** ([paper](https://arxiv.org/abs/2603.08640),
[v1.1](https://posttrainbench.com/blog/posttrainbench-1-1/)). Post-train a base model in 10 hours. Agents quoted the
contamination rules and cheated in 234 runs. v1.1 moved to one narrow judge per known cheat, majority of three where
results varied, scripts beside the judges, and a shared decontamination tool. A flagged run gets the base model's score.
v1.2 fixed a scorer that passed on `exit()` and dropped a sub-benchmark with a 48% item error rate.

**PaperBench** ([paper](https://arxiv.org/abs/2504.01848)). Rubric trees co-written with paper authors, 8,316 leaves.
The leaf type sets the judge's question and which files it sees. The judge reached F1 0.83 overall and 0.72 on code.
The IterativeAgent scaffold, which removes the end-task tool and prompts for piecemeal work, raised o1 from 13.2% to
24.4% and cut Claude 3.5 Sonnet from 21.0% to 16.1%.

**MLE-bench** ([paper](https://arxiv.org/abs/2410.07095)). Re-split Kaggle data and score against the human
leaderboard's medal lines. A local endpoint checks submission format without revealing a score. Integration tests check
that gold answers win a medal and the sample submission doesn't. The leaderboard closed after entries used test-set
feedback.

**RE-Bench** ([paper](https://arxiv.org/abs/2411.15114)). The score is the raw score minus the starting score, divided
by the reference's score minus the starting score, and 0 below the start. Agents ran the visible scorer 25 to 37 times
an hour, and a 0.88 run reran at 0.69. Expert pools differed twofold by how they were recruited.

**METR time horizons** ([paper](https://arxiv.org/abs/2503.14499)). Fit success against human minutes. At equal
length, models did worse on messier tasks, such as ones with no clear feedback or that need relevant information found.
Weight runs so big task families don't dominate. Only successful human runs count, and the pay scheme biased times low.

## Games and live environments

**Polytopia Bench** ([brain page](https://github.com/vtrivedy/research-brain/blob/main/knowledge/polytopia-bench.md)).
Agents write game bots scored against a pinned opponent panel. The practice opponent is not on the panel. A root process
captures the bot at the cutoff, and the verifier rebuilds it from that copy. An unresolved game raises an error instead
of scoring 0. The strongest opponent separates little, since every model wins 0.3% to 3% of its games against it.

**Gym-Anything-RT and Robotics** ([brain
page](https://github.com/vtrivedy/research-brain/blob/main/knowledge/gym-anything-rt.md)). A creator agent builds
environments and a fresh auditor checks each round, for up to three rounds. The same games scored 9% live and 75%
paused. Frontier models scored 0% on the hardest 25 tasks and 32% on easier ones.

**ARC-AGI-3** ([site](https://arcprize.org/arc-agi/3/)). Interactive games with no instructions. Scored by action
efficiency against the human median, capped at 115%. Random play must fail at scale. The public set is easier than the
private set.

## Environments and practice

**Data World** ([paper](https://arxiv.org/abs/2610.21656), [brain
page](https://github.com/vtrivedy/research-brain/blob/main/knowledge/data-world.md)). Specify the causes before the
files. A dependency graph and an environment contract come first, then canonical state built layer by layer with seeded
scripts, then files projected with stated defects. An independent auditor recomputes every target from the files alone.
Training on its environments raised three external benchmarks by 12 to 21 points. A model trained on 7,323 real tasks
gained externally but fell 0.29 on DataWorld-Bench.

**WorkRover** ([paper](https://arxiv.org/abs/2610.18067), [brain
page](https://github.com/vtrivedy/research-brain/blob/main/knowledge/workrover.md)). Repair environments instead of
regenerating them. The smallest validated repair changed a task's intent 1.3% of the time, against 14.3% for full
regeneration. The verifier must reject the starting state and planted negatives and accept the reference and
independently found alternatives, then it is frozen for a one-time admission test. Turn-level credit from frozen
workspace checks beat episode-only reward on all five benchmarks reported.

**DeepSeek V3.2** ([paper](https://arxiv.org/abs/2512.02556), section 3.2.3). 1,827 environments built to be "hard to
solve, easy to verify". An agent writes the tools, solution and verifier, then raises difficulty. An environment is
kept only if pass@100 is above 0.

**Gottlieb on RL task design** ([article](https://x.com/GottliebEli/status/2110077216260608037), [brain
page](https://github.com/vtrivedy/research-brain/blob/main/knowledge/gottlieb-rl-task-design.md)). A task is a prompt,
an environment, a verifier and an intent. Three pairs must agree: prompt and verifier, verifier and intent, prompt and
intent. A synthetic environment is sufficient only if it reliably brings out the target failure. A hacker-agent stage
looks for passing solutions without the skill before admission.

**Sean Cai's QC guide** ([essay](https://www.seancai.com/philosophy/good_qc_for_rl_data_v2), [brain
page](https://github.com/vtrivedy/research-brain/blob/main/knowledge/sean-cai-rl-data-qc.md)). Declare the mode first,
such as RL training or a pure benchmark. The RL band is pass@1 between 0.25 and 0.75, from 16 to 64 attempts. An
infrastructure failure rate above 5% means not evaluable. Keep parameterized templates and private holdouts against
contamination.

**Prime Intellect verifiers** ([brain
page](https://github.com/vtrivedy/research-brain/blob/main/knowledge/prime-intellect-verifiers.md)). A task proposer is
rewarded with 4p(1 - p) on the solvers' success rate, which peaks when half succeed.

**Code-RL audits** ([Rajan audit](https://arxiv.org/abs/2606.16062), [survey
page](https://github.com/vtrivedy/research-brain/blob/main/knowledge/rl-env-eval-survey.md),
[Anthropic](https://arxiv.org/abs/2511.18397)). The Rajan audit found about 25% to 30% of code-RL tasks accept wrong
solutions, and hackable tasks inflated pass@1 by 14.1 points. Reward hacking learned in Anthropic's RL environments
generalized to broader misalignment.

**DatBench** ([paper](https://arxiv.org/abs/2601.02316)). Up to 70% of VLM eval items were answerable without the image,
and up to 42% were mislabelled.

**Swarmory frontier-open runs**
([lessons](https://github.com/vtrivedy/swarmory/blob/71c2572/tasks/frontier-open/LESSONS.md)).
One run spent 95% of its budget on the builders' own debate and rebuilds, and Opus 5.5 solved 12 of 13 pre-tests 2 of 2
in 3 to 13 minutes. Blind error-correcting codes, an instruction set from bus traces, a small neural net and a hidden
invariant were each solved 2 of 2. Of 16 tasks measured again, 8 fell out of the band. One task screened 1 of 2 and
confirmed 22 of 24.

## Rubrics, verifiers and audits

**evals-skills** ([repo](https://github.com/ai-evals-course/evals-skills)). Product evals from error analysis. Failure
categories come from reading traces, not from generic labels. Verifiers are pass or fail, code before judges, one judge
per failure mode, and each judge is checked against human labels on a held-out split.

**OpenAI Decisions API** ([guide](https://developers.openai.com/api/docs/guides/decisions)). `POST /v1/decisions` on
`gpt-6-luna` returns probabilities for `predicate`, `choice` and `score` questions instead of free text. Up to 400
questions per request and 255 options per choice.

**RIFT** ([paper](https://arxiv.org/abs/2604.01375)). Nine ways a rubric fails. Structural checks name the failure 75%
of the time, against 56.7% for a frontier model asked directly. 83% of 147 public rubric tasks were hackable, and 10 of
48 expert rubrics weighted the less important criterion higher.

**rl-data-qa** ([repo](https://github.com/SeanZCai/rl-data-qa)). `summarize_rollouts.py` counts pass, fail and
infrastructure failure, and computes unbiased pass@k with infrastructure failures excluded. Its benchmark band requires
at least one frontier model above 30% and none above 85%.

**EvalResearchBench** ([paper](https://arxiv.org/abs/2610.04184)). Scores agent-built evals by how well they rank models
on held-out ones. The best eval on the visible models was not the best on the held-out ones. A hand-picked sample of
public tasks beat half the agent-built evals.

**AutoBenchmark** ([blog](https://facebookresearch.github.io/RAM/blogs/autobench/),
[report](https://arxiv.org/abs/2610.38882) section 1). An agent revised a ten-task Harbor benchmark over ten rounds
against solver models inside the loop, while held-out solvers never fed their scores back to the builder. An LLM
reviewed each round, and a rejected round couldn't seed the next one.

**EnvCheck** ([blog](https://usetokenless.com/blog/envcheck/)). The most productive single probe was an empty or broken
submission that still scored 1.

**PuzzleBench** ([repo](https://github.com/vtrivedy/puzzle-bench)). Reading attempts and renders after paid runs caught
its worst defects, not any check run beforehand. 52 Sol runs on 13 game puzzles solved all 52, after
calibration on a weaker model had put them at 25% to 50%.
