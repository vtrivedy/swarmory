"""Run a task's preflight/preflight_checks.sh in the solver's container, with no model calls.

It runs as the solver's user, under the network the run will use. It saves the output as agent/preflight.txt.

    PYTHONPATH=<skill folder>/scripts harbor run -p tasks/ -i <task-name> -a preflight_agent:PreflightAgent \\
        --ak script=$PWD/tasks/<task-name>/preflight/preflight_checks.sh --env modal -n 1 -y

Harbor 0.23 passes the task folder only to its own oracle agent, so the script path comes in as an agent argument.
"""
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class PreflightAgent(BaseAgent):
    def __init__(self, logs_dir: Path, script: str, **kwargs):
        super().__init__(logs_dir=logs_dir, **kwargs)
        self._script = Path(script)

    @staticmethod
    def name() -> str:
        return "preflight"

    def version(self) -> str:
        return "1.0.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        return

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        await environment.upload_file(source_path=self._script, target_path="/tmp/preflight_checks.sh")
        result = await environment.exec(command="id; bash /tmp/preflight_checks.sh; echo exit=$?", timeout_sec=120)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "preflight.txt").write_text((result.stdout or "") + (result.stderr or ""))
