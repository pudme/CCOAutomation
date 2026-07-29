from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.compliance import Control, EvidenceControlLink, Framework
from services.readiness_mode import get_framework_readiness_with_mode

router = APIRouter(prefix="/frameworks", tags=["frameworks"])


@router.get("")
async def list_frameworks(session: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await session.execute(
        select(Framework)
        .where(Framework.short_name != "obligations")
        .order_by(Framework.short_name.asc())
    )
    frameworks = list(result.scalars())
    return [
        {
            "id": framework.id,
            "name": framework.name,
            "version": framework.version,
            "short_name": framework.short_name,
            "description": framework.description,
            "loaded_date": framework.loaded_date,
        }
        for framework in frameworks
    ]


@router.get("/{framework_id}")
async def framework_detail(framework_id: str, session: AsyncSession = Depends(get_db)) -> dict:
    framework: Framework | None = None
    if framework_id.isdigit():
        framework = (
            await session.execute(select(Framework).where(Framework.id == int(framework_id)))
        ).scalars().first()
    if framework is None:
        framework = (
            await session.execute(select(Framework).where(Framework.short_name == framework_id))
        ).scalars().first()
    if framework is None:
        raise HTTPException(status_code=404, detail="Framework not found")
    controls = list(
        (
            await session.execute(
                select(Control).where(Control.framework_id == framework.id).order_by(Control.control_id.asc())
            )
        ).scalars()
    )
    domain_summary: dict[str, dict[str, int]] = {}
    for control in controls:
        stats = domain_summary.setdefault(
            control.domain, {"control_count": 0, "evidenced_count": 0}
        )
        stats["control_count"] += 1
        if control.status.value == "evidenced":
            stats["evidenced_count"] += 1
    readiness = await get_framework_readiness_with_mode(session, framework)
    return {
        "id": framework.id,
        "name": framework.name,
        "version": framework.version,
        "short_name": framework.short_name,
        "description": framework.description,
        "loaded_date": framework.loaded_date,
        "domain_summary": [
            {
                "domain": domain,
                "control_count": values["control_count"],
                "evidenced_count": values["evidenced_count"],
            }
            for domain, values in sorted(domain_summary.items())
        ],
        "readiness_mode": readiness["mode"],
        "active_checklist_name": readiness["checklist_name"],
    }


@router.get("/{framework_id}/controls")
async def framework_controls(
    framework_id: str,
    domain: str | None = Query(default=None),
    status: str | None = Query(default=None),
    evidence_gap: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> list[dict]:
    framework = (
        await session.execute(select(Framework).where(Framework.short_name == framework_id))
    ).scalars().first()
    if framework is None and framework_id.isdigit():
        framework = (
            await session.execute(select(Framework).where(Framework.id == int(framework_id)))
        ).scalars().first()
    if framework is None:
        raise HTTPException(status_code=404, detail="Framework not found")
    stmt = (
        select(Control)
        .options(
            selectinload(Control.evidence_links).selectinload(EvidenceControlLink.evidence),
            selectinload(Control.evidence_requirements),
        )
        .where(Control.framework_id == framework.id)
        .order_by(Control.control_id.asc())
    )
    controls = list((await session.execute(stmt)).scalars())
    payload = []
    for control in controls:
        row = {
            "id": control.id,
            "control_id": control.control_id,
            "title": control.title,
            "status": control.status.value,
            "domain": control.domain,
            "description": control.description,
            "implementation_guidance": control.implementation_guidance,
            "owner": control.owner_default,
            "evidence_on_file": len(control.evidence_items),
            "evidence_required": len(control.evidence_requirements),
            "last_reviewed": control.last_reviewed,
        }
        payload.append(row)
    if domain:
        payload = [p for p in payload if p["domain"] == domain]
    if status:
        payload = [p for p in payload if p["status"] == status]
    if evidence_gap:
        payload = [p for p in payload if (p["evidence_on_file"] < p["evidence_required"]) == (evidence_gap == "true")]
    return payload

