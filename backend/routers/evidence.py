from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.compliance import Control, EvidenceControlLink, EvidenceItem, EvidenceType, Framework
from services.evidence_corrections import snapshot_evidence_correction

router = APIRouter(prefix="/evidence", tags=["evidence"])


def _serialize_link(link: EvidenceControlLink) -> dict:
    control = link.control
    framework = control.framework if control else None
    return {
        "control_id": control.control_id if control else None,
        "control_db_id": link.control_id,
        "framework": framework.short_name if framework else None,
        "framework_name": framework.name if framework else None,
        "display_name": link.display_name,
        "title": control.title if control else None,
    }


def _serialize_evidence(
    item: EvidenceItem,
    *,
    preferred_control_id: str | None = None,
    preferred_framework: str | None = None,
) -> dict:
    links = [_serialize_link(link) for link in (item.control_links or [])]
    # Default display is base filename; framework/control filters surface that link's display_name.
    display = item.filename
    if preferred_control_id:
        for link in links:
            if link.get("control_id") == preferred_control_id and link.get("display_name"):
                display = link["display_name"]
                break
    elif preferred_framework:
        for link in links:
            if link.get("framework") == preferred_framework and link.get("display_name"):
                display = link["display_name"]
                break
    elif item.display_name and not links:
        display = item.display_name
    return {
        "id": item.id,
        "filename": item.filename,
        "display_name": display,
        "item_display_name": item.display_name,
        "file_path": item.file_path,
        "evidence_type": item.evidence_type.value if item.evidence_type else None,
        "description": item.description,
        "entity": item.entity,
        "collected_date": item.collected_date,
        "review_date": item.review_date,
        "status": item.status.value if item.status else None,
        "notes": item.notes,
        "analysis_confidence": item.analysis_confidence,
        "analysis_summary": item.analysis_summary,
        "library": item.library,
        "controls": links,
    }


@router.get("")
async def list_evidence(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    control_id: str | None = Query(default=None),
    framework: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> dict:
    stmt = (
        select(EvidenceItem)
        .options(
            selectinload(EvidenceItem.control_links)
            .selectinload(EvidenceControlLink.control)
            .selectinload(Control.framework)
        )
        .order_by(EvidenceItem.id.desc())
    )
    if control_id:
        stmt = stmt.join(EvidenceItem.control_links).join(EvidenceControlLink.control).where(
            Control.control_id == control_id
        )
    if framework:
        stmt = (
            stmt.join(EvidenceItem.control_links)
            .join(EvidenceControlLink.control)
            .join(Control.framework)
            .where(Framework.short_name == framework)
        )
    total = int(
        (
            await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
        ).scalar()
        or 0
    )
    rows = list(
        (await session.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().unique()
    )
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [
            _serialize_evidence(item, preferred_control_id=control_id, preferred_framework=framework)
            for item in rows
        ],
    }


@router.get("/{evidence_id}")
async def get_evidence(
    evidence_id: int,
    control_id: str | None = Query(default=None),
    framework: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> dict:
    item = (
        await session.execute(
            select(EvidenceItem)
            .options(
                selectinload(EvidenceItem.control_links)
                .selectinload(EvidenceControlLink.control)
                .selectinload(Control.framework)
            )
            .where(EvidenceItem.id == evidence_id)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return _serialize_evidence(item, preferred_control_id=control_id, preferred_framework=framework)


class EvidencePatchRequest(BaseModel):
    evidence_type: str | None = None
    filename: str | None = None


@router.patch("/{evidence_id}")
async def patch_evidence(
    evidence_id: int,
    payload: EvidencePatchRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    item = (
        await session.execute(select(EvidenceItem).where(EvidenceItem.id == evidence_id))
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    if payload.evidence_type is not None:
        before = item.evidence_type.value if item.evidence_type else None
        item.evidence_type = EvidenceType(payload.evidence_type)
        await snapshot_evidence_correction(
            session,
            evidence_id=item.id,
            field_name="evidence_type",
            before_value=before,
            after_value=item.evidence_type.value,
            source="api_patch",
        )
    if payload.filename is not None:
        before = item.filename
        item.filename = payload.filename.strip()
        await snapshot_evidence_correction(
            session,
            evidence_id=item.id,
            field_name="filename",
            before_value=before,
            after_value=item.filename,
            source="api_patch",
        )
    await session.commit()
    await session.refresh(item)
    return await get_evidence(evidence_id, session=session)


class EvidenceControlPatchRequest(BaseModel):
    display_name: str | None = None
    remove: bool = False


@router.patch("/{evidence_id}/controls/{control_id}")
async def patch_evidence_control(
    evidence_id: int,
    control_id: str,
    payload: EvidenceControlPatchRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    item = (
        await session.execute(
            select(EvidenceItem)
            .options(
                selectinload(EvidenceItem.control_links)
                .selectinload(EvidenceControlLink.control)
                .selectinload(Control.framework)
            )
            .where(EvidenceItem.id == evidence_id)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    link = next(
        (
            row
            for row in (item.control_links or [])
            if row.control and row.control.control_id == control_id
        ),
        None,
    )
    if link is None:
        raise HTTPException(status_code=404, detail="Control link not found on this evidence item")

    if payload.remove:
        await snapshot_evidence_correction(
            session,
            evidence_id=item.id,
            control_id=link.control_id,
            field_name="control_link",
            before_value=f"linked:{control_id}",
            after_value="removed",
            source="api_patch",
        )
        await snapshot_evidence_correction(
            session,
            evidence_id=item.id,
            control_id=link.control_id,
            field_name="control_display_name",
            before_value=link.display_name,
            after_value=None,
            source="api_patch",
            detail="link removed",
        )
        item.control_links.remove(link)
        await session.delete(link)
    elif payload.display_name is not None:
        before = link.display_name
        link.display_name = payload.display_name.strip()
        await snapshot_evidence_correction(
            session,
            evidence_id=item.id,
            control_id=link.control_id,
            field_name="control_display_name",
            before_value=before,
            after_value=link.display_name,
            source="api_patch",
        )
    else:
        raise HTTPException(status_code=400, detail="Provide display_name or remove=true")

    await session.commit()
    return await get_evidence(evidence_id, session=session)
