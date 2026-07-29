from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from models.compliance import EvidenceCorrection


async def snapshot_evidence_correction(
    session: AsyncSession,
    *,
    evidence_id: int,
    field_name: str,
    before_value: str | None,
    after_value: str | None,
    source: str,
    control_id: int | None = None,
    operator: str = "Michael DuPlantis",
    detail: str | None = None,
    force: bool = False,
) -> EvidenceCorrection | None:
    """Append-only correction snapshot. Never delete these rows."""
    if not force and (before_value or "") == (after_value or ""):
        return None
    row = EvidenceCorrection(
        timestamp=datetime.now(timezone.utc),
        evidence_id=evidence_id,
        control_id=control_id,
        field_name=field_name,
        before_value=before_value,
        after_value=after_value,
        source=source,
        operator=operator,
        detail=detail,
    )
    session.add(row)
    await session.flush()
    return row


async def snapshot_evidence_state_before_overwrite(
    session: AsyncSession,
    evidence: Any,
    *,
    source: str,
) -> list[EvidenceCorrection]:
    """Snapshot classification fields before reanalyze overwrite."""
    rows: list[EvidenceCorrection] = []
    for field_name, before_value in (
        ("evidence_type", evidence.evidence_type.value if evidence.evidence_type else None),
        ("display_name", getattr(evidence, "display_name", None)),
        ("analysis_summary", getattr(evidence, "analysis_summary", None)),
        ("analysis_confidence", getattr(evidence, "analysis_confidence", None)),
    ):
        row = await snapshot_evidence_correction(
            session,
            evidence_id=evidence.id,
            field_name=field_name,
            before_value=before_value,
            after_value="(pending reanalyze)",
            source=source,
            detail="pre-reanalyze snapshot",
            force=True,
        )
        if row:
            rows.append(row)
    for link in list(getattr(evidence, "control_links", None) or []):
        for field_name, before_value in (
            ("control_display_name", link.display_name),
            ("control_link", f"linked:{link.control_id}"),
        ):
            row = await snapshot_evidence_correction(
                session,
                evidence_id=evidence.id,
                control_id=link.control_id,
                field_name=field_name,
                before_value=before_value,
                after_value="(pending reanalyze)",
                source=source,
                detail="pre-reanalyze snapshot",
                force=True,
            )
            if row:
                rows.append(row)
    return rows
