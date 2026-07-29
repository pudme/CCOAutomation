from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.compliance import (
    Control,
    ControlStatus,
    EvidenceControlLink,
    EvidenceItem,
    EvidenceRequirement,
    EvidenceStatus,
    Framework,
)


def _is_control_covered(control: Control) -> bool:
    if control.status in {ControlStatus.RISK_ACCEPTED, ControlStatus.NOT_APPLICABLE, ControlStatus.EVIDENCED}:
        return True

    current_evidence: list[EvidenceItem] = [
        item for item in control.evidence_items if item.status == EvidenceStatus.CURRENT
    ]
    if not current_evidence:
        return False

    required_types = {req.evidence_type for req in control.evidence_requirements if req.required}
    if not required_types:
        return True

    provided_types = {item.evidence_type for item in current_evidence}
    return required_types.issubset(provided_types)


async def scan_framework_readiness(session: AsyncSession) -> list[dict]:
    frameworks_result = await session.execute(select(Framework).order_by(Framework.short_name.asc()))
    frameworks = list(frameworks_result.scalars())

    readiness: list[dict] = []
    for framework in frameworks:
        controls_result = await session.execute(
            select(Control)
            .options(
                selectinload(Control.evidence_links).selectinload(EvidenceControlLink.evidence),
                selectinload(Control.evidence_requirements),
            )
            .where(Control.framework_id == framework.id)
        )
        controls = list(controls_result.scalars())
        total_controls = len(controls)
        covered_controls = sum(1 for control in controls if _is_control_covered(control))
        readiness.append(
            {
                "framework": framework.short_name,
                "name": framework.name,
                "total_controls": total_controls,
                "evidenced_controls": covered_controls,
                "percent_evidenced": round((covered_controls / total_controls) * 100, 2) if total_controls else 0,
            }
        )

    return readiness


async def scan_framework_gaps(session: AsyncSession) -> list[dict]:
    frameworks_result = await session.execute(select(Framework).order_by(Framework.short_name.asc()))
    frameworks = list(frameworks_result.scalars())
    gaps: list[dict] = []

    for framework in frameworks:
        controls_result = await session.execute(
            select(Control)
            .options(
                selectinload(Control.evidence_links).selectinload(EvidenceControlLink.evidence),
                selectinload(Control.evidence_requirements),
            )
            .where(Control.framework_id == framework.id)
        )
        controls = list(controls_result.scalars())
        missing = [control.control_id for control in controls if not _is_control_covered(control)]
        gaps.append(
            {
                "framework": framework.short_name,
                "missing_count": len(missing),
                "missing_control_ids": missing,
            }
        )

    return gaps
