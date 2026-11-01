"""M5: choosing a target, and collecting what the platform already knows about it.

Everything fetched here is other people's text. The worker receives it inside a <dossier>
block and is told to treat it as information, never as instructions.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .gate import Target, parse_imports
from .platform import PlatformClient, target_from_theorem

MAX_ITEM_CHARACTERS = 1500
MAX_ITEMS = 8


def _find_mission(client: PlatformClient, mission_id: str) -> dict:
    offset = 0
    while True:
        page = client._get("/missions", limit=100, offset=offset)
        for mission in page["missions"]:
            if mission["id"] == mission_id:
                return mission
        offset += 100
        if offset >= page.get("total", 0):
            raise KeyError(f"no mission {mission_id}")


def frontier(client: PlatformClient, mission_id: str) -> dict:
    """Milestones in the captain's order, then open leaves with the most leverage first."""
    mission = _find_mission(client, mission_id)
    root = (mission.get("main_theorem") or {}).get("theorem_id")
    leaves = client.open_leaves(root) if root else []
    return {
        "mission": mission["name"],
        "root": mission.get("main_theorem", {}).get("theorem_name"),
        "milestones": [
            {
                "id": m.id, "title": m.title, "completed": m.completed,
                "theorem": m.theorem.theorem_name if m.theorem else None,
                "theorem_id": m.theorem.id if m.theorem else None,
                "status": m.theorem.status if m.theorem else None,
            }
            for m in client.milestones(mission_id)
        ],
        "open_leaves": [leaf.model_dump() for leaf in sorted(leaves, key=lambda l: -l.closability)],
    }


def _clip(text: object) -> str:
    value = str(text or "")
    return value if len(value) <= MAX_ITEM_CHARACTERS else value[:MAX_ITEM_CHARACTERS] + " [...]"


def dossier(
    client: PlatformClient, theorem_id: str, mission_id: str | None = None, milestone_id: str | None = None
) -> str:
    theorem = client.theorem(theorem_id)
    parts = [f"Theorem {theorem.theorem_name} ({theorem.status})."]
    if theorem.natural_language_statement:
        parts.append(f"Statement in words: {_clip(theorem.natural_language_statement)}")
    if theorem.source:
        parts.append(f"Source: {_clip(theorem.source)}")

    if theorem.audits:
        parts.append("Human reviews (a `flag` is a warning that the statement may be wrong):")
        parts += [f"- {a.decision}: {_clip(a.comment)}" for a in theorem.audits]
    else:
        parts.append("No human has reviewed this statement.")

    if milestone_id:
        events = client.milestone_history(milestone_id)
        rejected = [e for e in events if "theorem_id" in (e.get("changed_fields") or [])]
        if rejected:
            parts.append("Captain's changes to this milestone's formalization, newest first:")
            parts += [f"- {_clip(e.get('reason'))}" for e in rejected[:MAX_ITEMS]]

    decompositions = client.decompositions(theorem_id)
    if decompositions:
        parts.append("Existing reductions of this theorem:")
        for item in decompositions[:MAX_ITEMS]:
            children = [
                f"{c.get('theorem_name') or c.get('definition_name')} ({c.get('status', 'definition')})"
                for c in item.get("children", [])
            ]
            parts.append(f"- {', '.join(children)}")

    submissions = client.theorem_submissions(theorem_id)
    if submissions:
        parts.append("Earlier attempts by others, newest first:")
        for row in submissions[:MAX_ITEMS]:
            parts.append(
                f"- {row.get('status')}: {_clip(row.get('error_message') or row.get('explanation'))}"
            )

    if mission_id:
        disproved = [
            m for m in client.milestones(mission_id)
            if m.theorem and m.theorem.status == "Disproved" and m.theorem.id != theorem_id
        ]
        if disproved:
            parts.append("Sibling statements in this mission that were disproved, and why:")
        for milestone in disproved[:MAX_ITEMS]:
            accepted = client.theorem_submissions(milestone.theorem.id, status="ACCEPTED")
            reason = accepted[0].get("explanation") if accepted else ""
            parts.append(f"- {milestone.theorem.theorem_name}: {_clip(reason)}")

        comments = client.mission_comments(mission_id)
        rows = comments.get("comments", []) if isinstance(comments, dict) else comments
        if rows:
            parts.append("Mission discussion, as returned by the platform:")
            parts += [f"- {_clip(c.get('content') or c.get('body'))}" for c in rows[:MAX_ITEMS]]
    return "\n".join(parts)


def _definition_source(client: PlatformClient, name: str) -> str:
    rows = client._get("/definitions", q=name, limit=20).get("definitions", [])
    for row in rows:
        if row.get("definition_name") == name:
            return client._get(f"/definitions/{row['definition_id']}")["definition"]
    raise KeyError(f"no definition named {name}")


def _theorem_source(client: PlatformClient, slug: str) -> str:
    rows = client._get("/theorems", q=slug.replace("_", " "), view="compact", limit=50).get("theorems", [])
    for row in rows:
        if row.get("theorem_name", "").replace(".", "_") == slug:
            return f"{row.get('preamble', '').strip()}\n\n{row['formal_statement'].strip()}\n"
    raise KeyError(f"no theorem with slug {slug}")


def fetch_mirrors(client: PlatformClient, source: str, root: Path) -> list[str]:
    """Mirror every platform module reachable from `source`, verbatim, in the platform's layout."""
    written: list[str] = []

    def visit(text: str) -> None:
        for module in parse_imports(text):
            if module in written or not module.startswith(("Definitions.Def_", "Theorems.Thm_")):
                continue
            kind, leaf = module.split(".", 1)
            if kind == "Definitions":
                body = _definition_source(client, leaf.removeprefix("Def_"))
            else:
                body = _theorem_source(client, leaf.removeprefix("Thm_"))
            path = root / kind / f"{leaf}.lean"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
            written.append(module)
            visit(body)

    visit(source)
    return written


def export_target(client: PlatformClient, theorem_id: str, directory: Path, mission_id: str | None = None) -> dict:
    """Everything a machine without the key needs: statement, mirrors and dossier, as files."""
    target = target_from_theorem(client.theorem(theorem_id), client.environments())
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "target.json").write_text(json.dumps(asdict(target), indent=1, ensure_ascii=False))
    (directory / "dossier.txt").write_text(dossier(client, theorem_id, mission_id=mission_id))
    return {"target": target.theorem_name, "mirrors": fetch_mirrors(client, target.mirror_source(), directory / "mirrors")}
