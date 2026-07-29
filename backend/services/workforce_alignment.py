from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.workforce import (
    AssignmentStatus,
    ClearanceLevel,
    WorkforceAssignment,
    WorkforcePursuit,
    WorkforceStaff,
)

# Design choice (clearance): hierarchical — a higher clearance satisfies a lower
# requirement (e.g. top_secret satisfies secret). Exact-match-only is the alternative.
CLEARANCE_RANK: dict[ClearanceLevel, int] = {
    ClearanceLevel.NONE: 0,
    ClearanceLevel.PUBLIC_TRUST: 1,
    ClearanceLevel.SECRET: 2,
    ClearanceLevel.TOP_SECRET: 3,
    ClearanceLevel.TS_SCI: 4,
}

UTILIZATION_AVAILABLE_THRESHOLD = 80.0
APPRIO_ENTITY = "Apprio"


def clearance_satisfies(staff_clearance: ClearanceLevel, required: ClearanceLevel | None) -> bool:
    """Return True if staff clearance meets or exceeds the required level."""
    if required is None:
        return True
    return CLEARANCE_RANK.get(staff_clearance, 0) >= CLEARANCE_RANK.get(required, 0)


def _normalize_category(value: str | None) -> str:
    return (value or "").strip().lower()


def staff_matches_category(staff: WorkforceStaff, required_category: str) -> bool:
    return _normalize_category(staff.labor_category) == _normalize_category(required_category)


def _filter_staff_by_entity(
    staff_rows: list[WorkforceStaff],
    include_canaide: bool,
) -> list[WorkforceStaff]:
    """Default Apprio-only; opt in to cross-entity when include_canaide is True."""
    if include_canaide:
        return list(staff_rows)
    return [s for s in staff_rows if s.entity == APPRIO_ENTITY]


async def analyze_pursuit_gaps(
    session: AsyncSession,
    pursuit_id: int,
    include_canaide: bool = False,
) -> dict[str, Any]:
    """Gap analysis for a pursuit.

    Matching rules (current implementation):
    - Labor category: case-insensitive exact match on WorkforceStaff.labor_category
    - Clearance: hierarchical (higher satisfies lower) via CLEARANCE_RANK
    - Availability: utilization_pct < 80
    - Entity: Apprio-only by default (Canaide is divesting); set include_canaide=True
      to include all entities
    """
    pursuit = (
        await session.execute(select(WorkforcePursuit).where(WorkforcePursuit.id == pursuit_id))
    ).scalar_one_or_none()
    if pursuit is None:
        raise ValueError(f"Pursuit {pursuit_id} not found")

    all_staff = list((await session.execute(select(WorkforceStaff))).scalars())
    considered_staff = _filter_staff_by_entity(all_staff, include_canaide)
    required_categories = list(pursuit.required_labor_categories or [])
    required_clearance = pursuit.required_clearance_level

    available_staff = [
        s for s in considered_staff if (s.utilization_pct or 0) < UTILIZATION_AVAILABLE_THRESHOLD
    ]

    staff_evaluations: list[dict[str, Any]] = []
    for staff in considered_staff:
        util = float(staff.utilization_pct or 0)
        available = util < UTILIZATION_AVAILABLE_THRESHOLD
        clearance_ok = clearance_satisfies(staff.clearance_level, required_clearance)
        matching_categories = [
            cat
            for cat in required_categories
            if staff_matches_category(staff, cat)
        ]
        can_fill = available and clearance_ok and bool(matching_categories)
        staff_evaluations.append(
            {
                "staff_id": staff.id,
                "display_name": staff.display_name,
                "entity": staff.entity,
                "labor_category": staff.labor_category,
                "clearance_level": staff.clearance_level.value,
                "utilization_pct": util,
                "available_by_utilization": available,
                "clearance_satisfies_required": clearance_ok,
                "matching_required_categories": matching_categories,
                "can_fill_any_required_slot": can_fill,
                "entity_filter_applied": not include_canaide,
            }
        )

    gaps: list[dict[str, Any]] = []
    filled: list[dict[str, Any]] = []
    for category in required_categories:
        candidates = [
            s
            for s in available_staff
            if staff_matches_category(s, category)
            and clearance_satisfies(s.clearance_level, required_clearance)
        ]
        entry = {
            "labor_category": category,
            "clearance_required": required_clearance.value if required_clearance else None,
            "candidate_count": len(candidates),
            "candidates": [
                {
                    "staff_id": s.id,
                    "display_name": s.display_name,
                    "entity": s.entity,
                    "clearance_level": s.clearance_level.value,
                    "utilization_pct": float(s.utilization_pct or 0),
                }
                for s in candidates
            ],
        }
        if candidates:
            filled.append(entry)
        else:
            gaps.append({**entry, "status": "open"})

    return {
        "pursuit_id": pursuit.id,
        "pursuit_title": pursuit.title,
        "required_labor_categories": required_categories,
        "required_clearance_level": required_clearance.value if required_clearance else None,
        "clearance_matching": "hierarchical",
        "include_canaide": include_canaide,
        "entity_filtering": (
            "none — all entities included"
            if include_canaide
            else 'Apprio-only (entity == "Apprio"); pass include_canaide=True for cross-entity'
        ),
        "utilization_threshold": UTILIZATION_AVAILABLE_THRESHOLD,
        "staff_total_count": len(all_staff),
        "staff_considered_count": len(considered_staff),
        "staff_available_count": len(available_staff),
        "staff_evaluations": staff_evaluations,
        "gaps": gaps,
        "filled": filled,
        "gap_count": len(gaps),
    }


async def check_overcommitment(
    session: AsyncSession,
    include_canaide: bool = False,
) -> dict[str, Any]:
    """Sum commitment_pct across proposed/committed assignments per staff; flag totals > 100.

    Defaults to Apprio-only staff; set include_canaide=True to include all entities.
    """
    active_statuses = [AssignmentStatus.PROPOSED, AssignmentStatus.COMMITTED]
    assignments = list(
        (
            await session.execute(
                select(WorkforceAssignment).where(WorkforceAssignment.status.in_(active_statuses))
            )
        ).scalars()
    )
    all_staff = list((await session.execute(select(WorkforceStaff))).scalars())
    considered_staff = _filter_staff_by_entity(all_staff, include_canaide)
    staff_rows = {s.id: s for s in considered_staff}
    allowed_staff_ids = set(staff_rows.keys())

    totals: dict[int, float] = {}
    for assignment in assignments:
        if assignment.staff_id not in allowed_staff_ids:
            continue
        totals[assignment.staff_id] = totals.get(assignment.staff_id, 0.0) + float(
            assignment.commitment_pct or 0
        )

    flagged: list[dict[str, Any]] = []
    all_totals: list[dict[str, Any]] = []
    for staff_id, total in sorted(totals.items()):
        staff = staff_rows.get(staff_id)
        row = {
            "staff_id": staff_id,
            "display_name": staff.display_name if staff else None,
            "entity": staff.entity if staff else None,
            "total_commitment_pct": total,
            "overcommitted": total > 100.0,
        }
        all_totals.append(row)
        if total > 100.0:
            flagged.append(row)

    return {
        "include_canaide": include_canaide,
        "entity_filtering": (
            "none — all entities included"
            if include_canaide
            else 'Apprio-only (entity == "Apprio"); pass include_canaide=True for cross-entity'
        ),
        "staff_commitment_totals": all_totals,
        "overcommitted": flagged,
        "overcommitted_count": len(flagged),
    }
