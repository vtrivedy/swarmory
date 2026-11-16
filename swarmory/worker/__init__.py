"""What runs on a worker's machine: an agent's life (agent.py) and the job worker (job.py), which runs Harbor.
Standard library only."""
# Seconds whatever a worker started gets to stop, on SIGTERM, before the worker kills its process group and so itself.
# Less than backends/local.py's STOP, the wait before that backend kills the group, so the worker ends itself first.
LAST = 5
