"""Whom a new message is for."""
from swarmory.control_plane.pieces import Route


def route(team, d) -> Route:
    """d: conv, author, mentions, reply_to, text."""
    if d.conv.startswith("dm:"):  # a mention in a DM doesn't show it to a third member
        return Route([x for x in d.conv.split(":")[1:] if x != d.author])
    issue = team.board.get(d.conv)
    here = team.channels[d.conv]["members"] if "channel" in d.mentions and d.conv in team.channels else ()
    replied = team.messages[d.reply_to]["author"] if d.reply_to else None
    return Route([x for x, m in team.members.items() if x != d.author and not m.get("removed") and (
        x in d.mentions or "all" in d.mentions or x in here or x == replied
        or issue and x in issue["subscribers"])])
