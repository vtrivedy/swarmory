"""When an agent's next turn starts."""
from swarmory.control_plane import team as T  # SETTLE and IDLE stay there in the prototype, where tests set them


def start_at(team, t):
    """t: who, now, since (when it began to wait, or None), mail (undelivered, for it), heard (when this held call
    first saw mail, or None), end (when the held call ends). A time, or None for not until something changes."""
    if t.since is None:
        return t.now  # an agent that did not wait starts its next turn at once
    idle = t.since + T.IDLE
    return min(t.heard + T.SETTLE, t.end, idle) if t.mail else idle
