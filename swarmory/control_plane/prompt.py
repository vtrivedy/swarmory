"""An agent's starting prompt: worker/prompt.md filled in from the run's config, the mission as main has it (BRIEF.md
and its skills in .agents/skills/), and where the agent runs. The supervisor writes it to members/ID/prompt.md before each
start of the agent's worker, which gives it to every fresh conversation."""
import string
import time
from pathlib import Path

from swarmory import harness

from .jobs import git
from .team import IDLE

SKILLS = ".agents/skills"  # a mission's skills, where `npx skills add` puts them
TEMPLATE = Path(__file__).resolve().parents[1] / "worker" / "prompt.md"
BLOCK = ("|", ">", "|-", ">-", "|+", ">+")  # YAML's markers of a value on the indented lines below its key


def frontmatter(text: str) -> dict:
    """The top-level keys of a SKILL.md's YAML frontmatter, each as one line of text. A value that goes on over
    indented lines, plain, folded or literal, is joined with spaces."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta, key = {}, None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if key and line[:1] in (" ", "\t"):
            meta[key].append(line.strip())
        elif ":" in line:
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            meta[key] = [] if value in BLOCK else [value]
    out = {}
    for k, parts in meta.items():
        v = " ".join(p for p in parts if p)
        out[k] = v[1:-1] if len(v) > 1 and v[0] == v[-1] and v[0] in "'\"" else v
    return out


def mission(repo: Path) -> tuple[str, list]:
    """BRIEF.md on main, and the folder, name and description of each skill, .agents/skills/FOLDER/SKILL.md,
    on main. A file
    the team removed reads as empty, so the agent still starts."""
    show = lambda path: (git(repo, "show", f"main:{path}", check=False) or b"").decode(errors="replace")  # noqa: E731
    tree = git(repo, "ls-tree", "-r", "--name-only", "main", "--", SKILLS).decode().splitlines()
    skills = []
    for path in tree:
        parts = path.split("/")
        if len(parts) == 4 and parts[3] == "SKILL.md":
            meta = frontmatter(show(path))
            skills.append((parts[2], meta.get("name") or parts[2], meta.get("description", "")))
    return show("BRIEF.md"), skills


def network(backend: str, hosts: tuple) -> str:
    if backend == "modal":
        return ("You run in a Modal sandbox of your own. It reaches only these hosts: " + ", ".join(hosts) + ". A "
                "connection to any other host fails, GitHub and package indexes included"
                + (", so install only from those hosts" if len(hosts) > 1 else "") + ". If the work needs another "
                "host, say so on its issue and find a way without it.")
    return ("You run on the operator's machine, in your own folder, beside your teammates in theirs. The network is "
            "open. Work in your clone, install tools under your HOME, and leave other folders alone.")


def skill_list(skills: list) -> str:
    if not skills:
        return ""
    return ("## Use the mission's skills\n\nThe mission comes with these skills, each a folder in .agents/skills/ "
            "of your clone. Your coding agent has them as its own skills. When a task matches a skill's description, use "
            "that skill: read its SKILL.md before you start and follow it.\n\n"
            + "\n".join(f"- {name} ({SKILLS}/{folder}/SKILL.md)" + (f": {about}" if about else "")
                        for folder, name, about in skills) + "\n\n")


def prompt(config: dict, member: dict, brief: str, skills: list, backend: str, hosts: tuple, jobs: str) -> str:
    """One agent's starting prompt. `backend` is where workers run (local or modal), `hosts` what an agent on Modal
    reaches, and `jobs` the run's section on jobs (Jobs.guide), or ""."""
    agents = [m for m in config["members"] if m["kind"] == "agent"]
    roster = [f"- {a['name']} ({a['id']}), {a['harness']}" + (f". Role: {a['role']}" if a.get("role") else "")
              for a in agents]
    op = config["operator"]
    roster.append(f"- {op.title()} ({op}), the operator, a person and not an agent")
    return string.Template(TEMPLATE.read_text()).substitute(
        name=member["name"], id=member["id"], n=len(agents), roster="\n".join(roster), operator=op.title(),
        role=f"Your role: {member['role']}\n\n" if member.get("role") else "",
        deadline=time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(config["deadline"])),
        network=network(backend, hosts), idle=f"{IDLE // 60}",
        hooks=" Messages for you also arrive between your tool calls, marked [swarmory]."
        if harness.of(member).hooks else "",
        skills=skill_list(skills), jobs=jobs, brief=brief.rstrip("\n"),
        nojobs="" if config.get("jobs") else " This run has no jobs, so the server refuses `swarmory job`.")
