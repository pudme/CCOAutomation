from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai.gateway import MODEL_HAIKU, estimate_batch_cost, is_daily_limit_exception
from database import get_db
from models.auditor import (
    AuditorChecklist,
    AuditorChecklistItem,
    AuditorChecklistStatus,
    AuditorItemPriority,
    AuditorItemStatus,
)
from models.compliance import DataImport, EvidenceItem
from services.change_log import log_change

router = APIRouter(tags=["auditor"])


def _serialize_item(item: AuditorChecklistItem) -> dict:
    return {
        "id": item.id,
        "checklist_id": item.checklist_id,
        "source_import_id": item.source_import_id,
        "item_number": item.item_number,
        "description": item.description,
        "control_ids": item.control_ids or [],
        "status": item.status.value,
        "our_response": item.our_response,
        "evidence_ids": item.evidence_ids or [],
        "due_date": item.due_date,
        "auditor_notes": item.auditor_notes,
        "priority": item.priority.value,
        "raw_fields": item.raw_fields or {},
        "evidence_mapping": item.evidence_mapping or {"results": []},
    }


def _serialize_checklist(item: AuditorChecklist) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "audit_type": item.audit_type,
        "audit_period_year": item.audit_period_year,
        "audit_date": item.audit_date,
        "auditor_name": item.auditor_name,
        "framework": item.framework,
        "created_at": item.created_at.isoformat(),
        "status": item.status.value,
        "source_import_id": item.source_import_id,
        "fields_found": item.fields_found or [],
        "last_evidence_refresh": item.last_evidence_refresh,
        "evidence_refresh_status": item.evidence_refresh_status or "idle",
        "evidence_refresh_error": item.evidence_refresh_error,
    }


def _normalize_library(value: str | None) -> str:
    return (value or "main").strip().lower() or "main"


@router.get("/checklists")
async def get_checklists(
    library: str = "main",
    session: AsyncSession = Depends(get_db),
) -> list[dict]:
    normalized_library = _normalize_library(library)
    rows = (await session.execute(
        select(AuditorChecklist, DataImport.library)
        .outerjoin(DataImport, DataImport.id == AuditorChecklist.source_import_id)
        .order_by(AuditorChecklist.created_at.desc())
    )).all()
    payload: list[dict] = []
    for checklist, source_library in rows:
        checklist_library = _normalize_library(source_library if source_library is not None else "main")
        if checklist_library != normalized_library:
            continue
        item = _serialize_checklist(checklist)
        item["library"] = checklist_library
        payload.append(item)
    return payload


@router.get("/checklists/{checklist_id}")
async def get_checklist(
    checklist_id: int,
    library: str = "main",
    session: AsyncSession = Depends(get_db),
) -> dict:
    checklist = (
        await session.execute(select(AuditorChecklist).where(AuditorChecklist.id == checklist_id))
    ).scalar_one_or_none()
    if checklist is None:
        raise HTTPException(status_code=404, detail="Checklist not found")
    if checklist.source_import_id is not None:
        source_import = (
            await session.execute(select(DataImport).where(DataImport.id == checklist.source_import_id))
        ).scalar_one_or_none()
        if source_import is not None and _normalize_library(source_import.library) != _normalize_library(library):
            raise HTTPException(status_code=404, detail="Checklist not found")
    items = list(
        (
            await session.execute(
                select(AuditorChecklistItem)
                .where(AuditorChecklistItem.checklist_id == checklist_id)
                .order_by(AuditorChecklistItem.id.asc())
            )
        ).scalars()
    )
    source_import_ids = sorted({item.source_import_id for item in items if item.source_import_id is not None})
    source_files: list[dict] = []
    if source_import_ids:
        imports = list(
            (
                await session.execute(
                    select(DataImport).where(DataImport.id.in_(source_import_ids))
                )
            ).scalars()
        )
        by_import_id = {row.id: row for row in imports}
        for source_import_id in source_import_ids:
            import_row = by_import_id.get(source_import_id)
            if import_row is None:
                continue
            source_files.append(
                {
                    "import_id": source_import_id,
                    "filename": import_row.filename,
                    "created_at": import_row.created_at.isoformat(),
                    "item_count": len([item for item in items if item.source_import_id == source_import_id]),
                }
            )
    return {
        **_serialize_checklist(checklist),
        "library": _normalize_library(library),
        "items": [_serialize_item(item) for item in items],
        "source_files": source_files,
    }


class ChecklistCreateRequest(BaseModel):
    name: str
    audit_type: str | None = None
    audit_period_year: str | None = None
    audit_date: str | None = None
    auditor_name: str | None = None
    framework: str | None = None
    status: str = "active"
    source_import_id: int | None = None
    fields_found: list[str] = Field(default_factory=list)


@router.post("/checklists")
async def create_checklist(payload: ChecklistCreateRequest, session: AsyncSession = Depends(get_db)) -> dict:
    checklist = AuditorChecklist(
        name=payload.name,
        audit_type=payload.audit_type,
        audit_period_year=payload.audit_period_year,
        audit_date=payload.audit_date,
        auditor_name=payload.auditor_name,
        framework=payload.framework,
        status=AuditorChecklistStatus(payload.status),
        source_import_id=payload.source_import_id,
        fields_found=payload.fields_found,
    )
    session.add(checklist)
    await session.flush()
    await log_change(
        session,
        category="auditor",
        action="Checklist created",
        subject=checklist.name,
        detail=f"Checklist created: {checklist.name}",
        triggered_by="api",
    )
    await session.commit()
    await session.refresh(checklist)
    return _serialize_checklist(checklist)


class ChecklistItemCreateRequest(BaseModel):
    source_import_id: int | None = None
    item_number: str
    description: str
    control_ids: list[str] = Field(default_factory=list)
    status: str = "open"
    our_response: str | None = None
    evidence_ids: list[int] = Field(default_factory=list)
    due_date: str | None = None
    auditor_notes: str | None = None
    priority: str = "medium"
    raw_fields: dict = Field(default_factory=dict)


@router.post("/checklists/{checklist_id}/items")
async def create_checklist_item(
    checklist_id: int,
    payload: ChecklistItemCreateRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    checklist = (
        await session.execute(select(AuditorChecklist).where(AuditorChecklist.id == checklist_id))
    ).scalar_one_or_none()
    if checklist is None:
        raise HTTPException(status_code=404, detail="Checklist not found")
    item = AuditorChecklistItem(
        checklist_id=checklist_id,
        source_import_id=payload.source_import_id,
        item_number=payload.item_number,
        description=payload.description,
        control_ids=payload.control_ids,
        status=AuditorItemStatus(payload.status),
        our_response=payload.our_response,
        evidence_ids=payload.evidence_ids,
        due_date=payload.due_date,
        auditor_notes=payload.auditor_notes,
        priority=AuditorItemPriority(payload.priority),
        raw_fields=payload.raw_fields,
        evidence_mapping={"results": []},
    )
    session.add(item)
    await session.flush()
    await log_change(
        session,
        category="auditor",
        action="Checklist item created",
        subject=item.item_number,
        detail=f"Checklist item created: {item.item_number} (checklist={checklist_id})",
        triggered_by="api",
    )
    await session.commit()
    await session.refresh(item)
    return _serialize_item(item)


class ChecklistItemPatchRequest(BaseModel):
    status: str | None = None
    our_response: str | None = None
    evidence_ids: list[int] | None = None
    due_date: str | None = None
    auditor_notes: str | None = None
    priority: str | None = None
    control_ids: list[str] | None = None
    raw_fields: dict | None = None


class MatchEvidenceRequest(BaseModel):
    bypass_limit: bool = False


class RefreshEvidenceRequest(BaseModel):
    bypass_limit: bool = False


class GenerateResponseRequest(BaseModel):
    bypass_limit: bool = False


@router.patch("/checklists/{checklist_id}/items/{item_id}")
async def patch_checklist_item(
    checklist_id: int,
    item_id: int,
    payload: ChecklistItemPatchRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    item = (
        await session.execute(
            select(AuditorChecklistItem).where(
                AuditorChecklistItem.id == item_id,
                AuditorChecklistItem.checklist_id == checklist_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Checklist item not found")
    if payload.status is not None:
        item.status = AuditorItemStatus(payload.status)
    if payload.our_response is not None:
        item.our_response = payload.our_response
    if payload.evidence_ids is not None:
        item.evidence_ids = payload.evidence_ids
    if payload.due_date is not None:
        item.due_date = payload.due_date
    if payload.auditor_notes is not None:
        item.auditor_notes = payload.auditor_notes
    if payload.priority is not None:
        item.priority = AuditorItemPriority(payload.priority)
    if payload.control_ids is not None:
        item.control_ids = payload.control_ids
    if payload.raw_fields is not None:
        item.raw_fields = payload.raw_fields
    await log_change(
        session,
        category="auditor",
        action="Checklist item updated",
        subject=item.item_number,
        detail=f"Checklist item updated: {item.item_number} (checklist={checklist_id})",
        triggered_by="api",
    )
    await session.commit()
    return _serialize_item(item)


@router.post("/checklists/{checklist_id}/items/{item_id}/generate-response")
async def generate_item_response(
    checklist_id: int,
    item_id: int,
    payload: GenerateResponseRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    from services.auditor_evidence_mapper import generate_response_for_single_item

    try:
        result = await generate_response_for_single_item(
            checklist_id,
            item_id,
            session,
            bypass_limit=bool(payload.bypass_limit),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Response generation failed: {exc}") from exc

    item = (
        await session.execute(
            select(AuditorChecklistItem).where(
                AuditorChecklistItem.checklist_id == checklist_id,
                AuditorChecklistItem.id == item_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Checklist item not found")
    if result.get("updated"):
        await log_change(
            session,
            category="auditor",
            action="Response generated",
            subject=item.item_number,
            detail=f"Response generated for item {item.item_number} (checklist={checklist_id})",
            triggered_by="api",
        )
        await session.commit()
    return {"result": result, "item": _serialize_item(item)}


@router.get("/checklists/{checklist_id}/summary")
async def checklist_summary(checklist_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    checklist = (
        await session.execute(select(AuditorChecklist).where(AuditorChecklist.id == checklist_id))
    ).scalar_one_or_none()
    if checklist is None:
        raise HTTPException(status_code=404, detail="Checklist not found")
    items = list(
        (
            await session.execute(
                select(AuditorChecklistItem).where(AuditorChecklistItem.checklist_id == checklist_id)
            )
        ).scalars()
    )
    counts = {status.value: 0 for status in AuditorItemStatus}
    for item in items:
        counts[item.status.value] += 1
    total = len(items)
    satisfied = counts[AuditorItemStatus.SATISFIED.value]
    percent_satisfied = round((satisfied / total) * 100, 2) if total else 0
    evidence_item_count = (
        await session.execute(select(func.count(EvidenceItem.id)))
    ).scalar_one()
    return {
        "checklist_id": checklist_id,
        "counts_by_status": counts,
        "percent_satisfied": percent_satisfied,
        "total_items": total,
        "evidence_item_count": int(evidence_item_count or 0),
    }


@router.post("/checklists/{checklist_id}/refresh-evidence")
async def refresh_evidence_mapping(
    checklist_id: int,
    payload: RefreshEvidenceRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    checklist = (
        await session.execute(select(AuditorChecklist).where(AuditorChecklist.id == checklist_id))
    ).scalar_one_or_none()
    if checklist is None:
        raise HTTPException(status_code=404, detail="Checklist not found")
    from services.auditor_evidence_mapper import trigger_mapping_job_with_mode

    if checklist.evidence_refresh_status == "in_progress":
        stale = False
        if checklist.last_evidence_refresh:
            try:
                refresh_dt = datetime.fromisoformat(checklist.last_evidence_refresh)
                if refresh_dt.tzinfo is None:
                    refresh_dt = refresh_dt.replace(tzinfo=timezone.utc)
                stale = (datetime.now(timezone.utc) - refresh_dt) > timedelta(minutes=10)
            except ValueError:
                stale = True
        if not stale:
            return {
                "checklist_id": checklist_id,
                "queued": False,
                "evidence_refresh_status": "in_progress",
                "last_evidence_refresh": checklist.last_evidence_refresh,
            }
        checklist.evidence_refresh_status = "failed"
        checklist.evidence_refresh_error = "Recovered stale in-progress refresh; restarted mapping."
        await session.commit()

    item_count = int(
        (
            await session.execute(
                select(func.count(AuditorChecklistItem.id)).where(
                    AuditorChecklistItem.checklist_id == checklist_id
                )
            )
        ).scalar()
        or 0
    )
    estimate = await estimate_batch_cost(max(item_count, 1), model=MODEL_HAIKU)
    if estimate["will_exceed_limit"] and not payload.bypass_limit:
        raise HTTPException(status_code=402, detail=estimate)

    checklist.evidence_refresh_status = "queued"
    checklist.evidence_refresh_error = None
    await session.commit()

    started = trigger_mapping_job_with_mode(
        checklist_id,
        mode="refresh_existing",
        bypass_limit=bool(payload.bypass_limit),
    )
    if not started:
        checklist.evidence_refresh_status = "failed"
        checklist.evidence_refresh_error = "Unable to start background mapping task."
        await session.commit()
        return {
            "checklist_id": checklist_id,
            "queued": False,
            "evidence_refresh_status": checklist.evidence_refresh_status,
            "error": checklist.evidence_refresh_error,
        }

    return {
        "checklist_id": checklist_id,
        "queued": True,
        "evidence_refresh_status": "queued",
        "estimated_calls": max(item_count, 1),
        "last_evidence_refresh": checklist.last_evidence_refresh,
    }


@router.post("/checklists/{checklist_id}/match-evidence")
async def match_evidence_to_requests(
    checklist_id: int,
    payload: MatchEvidenceRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    checklist = (
        await session.execute(select(AuditorChecklist).where(AuditorChecklist.id == checklist_id))
    ).scalar_one_or_none()
    if checklist is None:
        raise HTTPException(status_code=404, detail="Checklist not found")

    item_count = int(
        (
            await session.execute(
                select(func.count(AuditorChecklistItem.id)).where(
                    AuditorChecklistItem.checklist_id == checklist_id
                )
            )
        ).scalar()
        or 0
    )
    estimate = await estimate_batch_cost(max(item_count, 1), model=MODEL_HAIKU)
    if estimate["will_exceed_limit"] and not payload.bypass_limit:
        raise HTTPException(status_code=402, detail=estimate)

    checklist.evidence_refresh_status = "in_progress"
    checklist.evidence_refresh_error = None
    checklist.last_evidence_refresh = datetime.now(timezone.utc).isoformat()
    await session.commit()

    from services.auditor_evidence_mapper import _semantic_match_checklist

    try:
        mapping_stats = await _semantic_match_checklist(
            checklist_id,
            session,
            bypass_limit=bool(payload.bypass_limit),
        )
    except Exception as exc:  # noqa: BLE001
        if is_daily_limit_exception(exc):
            estimate = await estimate_batch_cost(max(item_count, 1), model=MODEL_HAIKU)
            raise HTTPException(status_code=402, detail=estimate) from exc
        raise HTTPException(status_code=500, detail=f"Evidence matching failed: {exc}") from exc

    summary = await checklist_summary(checklist_id, session)
    refreshed_checklist = (
        await session.execute(select(AuditorChecklist).where(AuditorChecklist.id == checklist_id))
    ).scalar_one_or_none()
    return {
        "checklist_id": checklist_id,
        "queued": False,
        "evidence_refresh_status": (
            refreshed_checklist.evidence_refresh_status if refreshed_checklist is not None else "complete"
        ),
        "estimated_calls": max(item_count, 1),
        "mapping_stats": mapping_stats,
        "summary": summary,
        "last_evidence_refresh": (
            refreshed_checklist.last_evidence_refresh if refreshed_checklist is not None else None
        ),
    }


@router.delete("/checklists/{checklist_id}")
async def delete_checklist(checklist_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    checklist = (
        await session.execute(select(AuditorChecklist).where(AuditorChecklist.id == checklist_id))
    ).scalar_one_or_none()
    if checklist is None:
        raise HTTPException(status_code=404, detail="Checklist not found")
    name = checklist.name
    await session.execute(
        select(AuditorChecklistItem).where(AuditorChecklistItem.checklist_id == checklist_id)
    )
    await session.execute(
        AuditorChecklistItem.__table__.delete().where(AuditorChecklistItem.checklist_id == checklist_id)
    )
    await session.execute(
        AuditorChecklist.__table__.delete().where(AuditorChecklist.id == checklist_id)
    )
    await log_change(
        session,
        category="auditor",
        action="Checklist deleted",
        subject=name,
        detail=f"Checklist deleted: {name}",
        triggered_by="api",
    )
    await session.commit()
    return {"status": "deleted", "checklist_id": checklist_id}


@router.delete("/checklists/{checklist_id}/source-files/{import_id}")
async def delete_checklist_source_file(
    checklist_id: int,
    import_id: int,
    session: AsyncSession = Depends(get_db),
) -> dict:
    checklist = (
        await session.execute(select(AuditorChecklist).where(AuditorChecklist.id == checklist_id))
    ).scalar_one_or_none()
    if checklist is None:
        raise HTTPException(status_code=404, detail="Checklist not found")
    await session.execute(
        AuditorChecklistItem.__table__.delete().where(
            AuditorChecklistItem.checklist_id == checklist_id,
            AuditorChecklistItem.source_import_id == import_id,
        )
    )
    await log_change(
        session,
        category="auditor",
        action="Checklist source file deleted",
        subject=str(checklist_id),
        detail=f"Source file import_id={import_id} removed from checklist {checklist_id}",
        triggered_by="api",
    )
    await session.commit()
    return {"status": "deleted", "checklist_id": checklist_id, "import_id": import_id}
