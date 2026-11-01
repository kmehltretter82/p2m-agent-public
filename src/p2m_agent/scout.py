"""M5: choosing a target, and collecting what the platform already knows about it.

Everything fetched here is other people's text. The worker receives it inside a <dossier>
block and is told to treat it as information, never as instructions.
"""

from __future__ import annotations

from .platform import PlatformClient

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
        comments = client.mission_comments(mission_id)
        rows = comments.get("comments", []) if isinstance(comments, dict) else comments
        if rows:
            parts.append("Mission discussion, as returned by the platform:")
            parts += [f"- {_clip(c.get('content') or c.get('body'))}" for c in rows[:MAX_ITEMS]]
    return "\n".join(parts)
