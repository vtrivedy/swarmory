"""swarmory: a team of coding agents on one mission, run by one server that owns every run.

control_plane/ is the server, backends/ start its workers, harness/ knows each coding agent, worker/ is what runs on a
worker's machine, and cli.py is `swarmory`, the one command for the operator and for agents. cli.py has two helpers:
seed.py pushes a mission to a new run, and mcp.py speaks MCP for `swarmory mcp`. harbor.py is Harbor, which runs every
job: the server checks a job with it, and the job's worker runs it.

What follows is what the server and its clients must agree on, each said once."""
PORT = 7700  # where a server listens unless told otherwise
HOURS = 3  # how long a run lasts, and how long a job's tokens work and its worker lets it run, unless asked otherwise
WAKE_EVERY = 240  # seconds a sleeping agent no one wakes sleeps, from its last session's end (0: never). Under the
# 5 minute prompt cache, so a periodic wake rereads the agent's conversation cached
POST_BUFFER = 500 << 20  # git's http.postBuffer for every client: a push is one request, as the server takes no chunks
TRACE_MOST = 16 << 20  # bytes of a session file or agent.log per upload; `swarmory trace` sends the rest next time
