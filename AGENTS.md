# Working on swarmory

This file is for changing swarmory. To use it, to set up or run a swarm, follow
[skills/swarmory/SKILL.md](skills/swarmory/SKILL.md).

## What's where

| Path | What |
|---|---|
| `swarmory/` | The one Python package: the CLI (`cli.py`), the server (`control_plane/`), where agents run (`backends/`: local and Modal), the coding agents (`harness/`), what runs each agent or job (`worker/`), and Harbor jobs (`harbor.py`). |
| `skills/` | Skills for coding agents, which `npx skills add vtrivedy/swarmory` installs: `swarmory` (run swarms), `eval-factory` (build Harbor eval tasks), `running-harbor-jobs` (for agents in a `--jobs` run). They ship from this folder, not with the package. |
| `examples/` | Missions to start from. |
| `tools/` | Scripts for reading a run's records: `compare.py`, `report.py`. |
| `tests/` | Unit tests, and `e2e.sh`, which runs a real mission. |

## Test a change

```sh
uvx --python 3.12 --with-editable . pytest tests -q -p no:cacheprovider
bash tests/e2e.sh    # real model calls: it spends
```

`tests/model/Delivery.tla` is a TLA+ model of how messages reach an agent. Check it with the pstack `statechart` skill
after a change to `Team.inbox`, `Team.next` or `Team.mark_read`.

A change to how someone installs or starts swarmory also changes README.md and `skills/swarmory/SKILL.md`. Say each
fact in one of them, and link from the other.
