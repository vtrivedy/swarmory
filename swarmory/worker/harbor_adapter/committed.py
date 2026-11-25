"""The one Harbor agent that runs a harness: a folder committed to a run's repo that holds an executable `run`.

swarmory/harbor.py runs Harbor with this folder alone as PYTHONPATH and `-a committed:Committed --ak dir=FOLDER`, so
Harbor imports this file and nothing else of swarmory's, and no committed code runs in Harbor's process. In each
trial's sandbox it puts the folder at /installed-agent/harness, writes the task's MCP servers to
/installed-agent/mcp.json, and runs `run` there as the task's agent user, with:

    HARBOR_INSTRUCTION   the task's instruction
    MODEL                the job's --model
    MCP_CONFIG           /installed-agent/mcp.json: {"mcpServers": {NAME: {"transport", "command", "args" | "url"}}}

and the model gateway Harbor passes every agent (ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY, OPENAI_BASE_URL/OPENAI_API_KEY).
Its output goes to /logs/agent/run.log. A non-zero exit fails the trial."""
import json
import tempfile
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent

HOME = "/installed-agent/harness"
MCP = "/installed-agent/mcp.json"


class Committed(BaseInstalledAgent):
    def __init__(self, *args, dir: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.dir = Path(dir)

    @staticmethod
    def name() -> str:
        return "committed"

    async def install(self, environment) -> None:
        await environment.upload_dir(self.dir, HOME)
        servers = {s.name: s.model_dump(mode="json", exclude={"name"}, exclude_none=True) for s in self.mcp_servers}
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "mcp.json"
            config.write_text(json.dumps({"mcpServers": servers}, indent=2))
            await environment.upload_file(config, MCP)
        # the agent user reads all of it and runs `run`, whatever modes the upload left
        await self.exec_as_root(environment, f"chmod -R a+rX {HOME} && chmod a+rx {HOME}/run && chmod a+r {MCP}")

    async def run(self, instruction: str, environment, context) -> None:
        await self.exec_as_agent(environment, f"{HOME}/run 2>&1 | tee /logs/agent/run.log",
                                 env={"HARBOR_INSTRUCTION": instruction, "MODEL": self.model_name or "",
                                      "MCP_CONFIG": MCP})
