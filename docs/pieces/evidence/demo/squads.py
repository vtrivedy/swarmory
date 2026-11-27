"""Squads that work apart and sync on a schedule. Agents carry a squad in their tags (team.toml: tags = ["red"]).
Between syncs a message reaches only the author's squad; what it would have reached elsewhere is held, and each sync
gives every member one digest of what was held for it."""
from swarmory.control_plane.pieces import Route
from swarmory.control_plane.team import SYSTEM
from swarmory.pieces import mail

EVERY, OPEN = 6 * 3600, 20 * 60


def squad(team, x):
    return next((t for t in team.members[x].get("tags", ()) if t.startswith("squad:")), None)


def state(team):
    return team.state.setdefault("squads", {"open": None, "last": None, "held": {}})


def apply(team, ev):
    s = state(team)
    if ev["type"] == "squads/open":
        s.update(open=ev["until"], last=ev["at"], held={})
    elif ev["type"] == "squads/close":
        s["open"] = None
    elif ev["type"] == "message" and ev.get("held"):
        for x in ev["held"]:
            s["held"].setdefault(x, []).append(ev["id"])


def route(team, d) -> Route:
    base = mail.route(team, d)
    mine = squad(team, d.author)
    if state(team)["open"] or not mine:
        return base
    near = [x for x in team.members if squad(team, x) in (mine, None)]  # the squad, and whoever has none
    return Route([x for x in base.to if x in near], audience=near, held=[x for x in base.to if x not in near])


def tick(run, now):
    team = run.team
    with team.lock:
        s = state(team)
        if s["open"] and now >= s["open"]:
            team.append({"type": "squads/close"})
        elif not s["open"] and now >= (s["last"] or run.config["created"]) + EVERY:
            held = {x: list(ids) for x, ids in s["held"].items()}
            team.append({"type": "squads/open", "until": now + OPEN})
            for x, ids in held.items():
                lines = [f"[{i} {team.messages[i]['conv']}] {team.messages[i]['author']}: {team.messages[i]['text']}"
                         for i in ids]
                team.post("general", SYSTEM, "Sync is open. Held for you since the last one:\n" + "\n".join(lines),
                          None, None, route=Route([x], audience=[x]))
