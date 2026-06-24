from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import chromadb
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ai.gateway import estimate_batch_cost, get_usage_today, is_daily_limit_exception
from config import get_settings
from database import AsyncSessionLocal, get_db
from models.compliance import AppSetting, Control, ControlStatus, DataImport, EvidenceItem, EvidenceStatus, ImportStatus
from models.auditor import AuditorChecklist, AuditorChecklistItem, AuditorItemStatus
from services.background_jobs import load_status, queue_reanalyze_job
from services.change_log import log_change
from services.import_pipeline import (
    build_import_object_name,
    compute_content_hash,
    extract_text_content,
    get_minio_client,
    process_import_with_options,
    run_evidence_intelligence_on_import,
)

router = APIRouter(prefix="/documents", tags=["documents"])
settings = get_settings()
REANALYZE_STATUS_KEY = "reanalyze_status"


class _ReanalyzeRuntime:
    def __init__(self) -> None:
        self.running = False
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.subscribers: set[asyncio.Queue[str]] = set()
        self.state: dict[str, Any] = {
            "started_at": None,
            "completed": 0,
            "total": 0,
            "new_links": 0,
            "errors": 0,
            "message": "",
            "last_document": None,
            "documents": [],
        }


_REANALYZE_RUNTIME = _ReanalyzeRuntime()


@router.get("")
async def list_documents(
    framework: str | None = Query(default=None),
    control: str | None = Query(default=None),
    doc_type: str | None = Query(default=None),
    entity: str | None = Query(default=None),
    library: str = Query(default="main"),
    session: AsyncSession = Depends(get_db),
) -> list[dict]:
    normalized_library = _normalize_library(library)
    evidence = list(
        (
            await session.execute(
                select(EvidenceItem).where(EvidenceItem.library == normalized_library).options(
                    selectinload(EvidenceItem.controls).selectinload(Control.framework)
                )
            )
        ).scalars()
    )
    imports = list(
        (
            await session.execute(select(DataImport).order_by(DataImport.created_at.desc()))
        ).scalars()
    )
    imports = [row for row in imports if _normalize_library(row.library) == normalized_library]

    imports_by_filename: dict[str, DataImport] = {}
    imports_by_id: dict[int, DataImport] = {}
    for row in imports:
        imports_by_id[row.id] = row
        key = (row.filename or "").strip().lower()
        if key and key not in imports_by_filename:
            imports_by_filename[key] = row

    documents_by_filename: dict[str, dict] = {}
    for item in sorted(evidence, key=lambda row: row.id, reverse=True):
        key = (item.filename or "").strip().lower()
        if not key or key in documents_by_filename:
            continue
        import_row = imports_by_filename.get(key)
        detected_type = _detected_type_from_import(import_row)
        documents_by_filename[key] = {
            "id": f"evidence:{item.id}",
            "import_id": import_row.id if import_row else None,
            "filename": item.filename,
            "doc_type": _display_doc_type(
                detected_type=detected_type,
                evidence_type=item.evidence_type.value if item.evidence_type else None,
                filename=item.filename,
            ),
            "framework": _framework_label_for_evidence(item),
            "control_ids": [c.control_id for c in item.controls],
            "entity": _display_entity(item.entity, import_row),
            "date": item.collected_date or (import_row.data_date if import_row else None),
            "library": normalized_library,
            "duplicate_status": import_row.duplicate_status if import_row else "unique",
            "duplicate_flag_dismissed": bool(import_row.duplicate_flag_dismissed) if import_row else False,
            "duplicate_of_id": import_row.duplicate_of_id if import_row else None,
            "duplicate_confidence": import_row.duplicate_confidence if import_row else None,
            "duplicate_reason": import_row.duplicate_reason if import_row else None,
            "duplicate_of_filename": (
                imports_by_id.get(import_row.duplicate_of_id).filename
                if import_row and import_row.duplicate_of_id and imports_by_id.get(import_row.duplicate_of_id)
                else None
            ),
        }

    for row in imports:
        key = (row.filename or "").strip().lower()
        if not key or key in documents_by_filename:
            continue
        detected_type = _detected_type_from_import(row)
        documents_by_filename[key] = {
            "id": f"import:{row.id}",
            "import_id": row.id,
            "filename": row.filename,
            "doc_type": _display_doc_type(
                detected_type=detected_type,
                evidence_type=None,
                filename=row.filename,
            ),
            "framework": row.framework or "",
            "control_ids": row.control_ids or [],
            "entity": _display_entity(None, row),
            "date": row.data_date,
            "library": normalized_library,
            "duplicate_status": row.duplicate_status,
            "duplicate_flag_dismissed": bool(row.duplicate_flag_dismissed),
            "duplicate_of_id": row.duplicate_of_id,
            "duplicate_confidence": row.duplicate_confidence,
            "duplicate_reason": row.duplicate_reason,
            "duplicate_of_filename": (
                imports_by_id.get(row.duplicate_of_id).filename
                if row.duplicate_of_id and imports_by_id.get(row.duplicate_of_id)
                else None
            ),
        }

    documents = list(documents_by_filename.values())
    if framework:
        documents = [d for d in documents if d["framework"] == framework]
    if control:
        documents = [d for d in documents if control in d["control_ids"]]
    if doc_type:
        documents = [d for d in documents if (d["doc_type"] or "").lower() == doc_type.lower()]
    if entity:
        documents = [d for d in documents if (d["entity"] or "").lower() == entity.lower()]
    return documents


class DocumentSearchRequest(BaseModel):
    query: str


class DeleteDocumentRequest(BaseModel):
    force: bool = False


class BulkDeleteRequest(BaseModel):
    document_ids: list[str | int]


class ReanalyzeBatchRequest(BaseModel):
    bypass_limit: bool = False
    resume_only: bool = False


class ReanalyzeBackgroundRequest(BaseModel):
    limit: int = 10
    bypass_limit: bool = False


class SyncPreviewFileMeta(BaseModel):
    filename: str
    size: int


class SyncPreviewRequest(BaseModel):
    files: list[SyncPreviewFileMeta]


def _normalize_sync_name(value: str) -> str:
    return str(value or "").strip().lower()


def _normalize_library(value: str | None) -> str:
    return (value or "main").strip().lower() or "main"


async def _classify_sync_files(
    session: AsyncSession,
    *,
    files: list[dict[str, Any]],
    library: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    imports_all = list(
        (
            await session.execute(
                select(DataImport)
                .order_by(DataImport.created_at.desc())
            )
        ).scalars()
    )
    imports = [row for row in imports_all if _normalize_library(row.library) == library]
    by_name: dict[str, DataImport] = {}
    by_name_main: dict[str, DataImport] = {}
    for row in imports:
        key = _normalize_sync_name(row.filename)
        if key and key not in by_name:
            by_name[key] = row
    if library == "dpa":
        for row in imports_all:
            if _normalize_library(row.library) != "main":
                continue
            key = _normalize_sync_name(row.filename)
            if key and key not in by_name_main:
                by_name_main[key] = row

    actions: list[dict[str, Any]] = []
    summary = {
        "total_scanned": len(files),
        "new": 0,
        "modified": 0,
        "unchanged": 0,
        "skipped": 0,
        "new_files": [],
        "modified_files": [],
        "new_details": [],
        "modified_details": [],
        "errors": [],
        "main_library_collisions": [],
    }
    for file in files:
        filename = str(file.get("filename") or "").strip()
        if not filename:
            summary["skipped"] += 1
            summary["errors"].append("Missing filename")
            continue
        file_size = int(file.get("size") or 0)
        if library == "dpa" and _normalize_sync_name(filename) in by_name_main:
            summary["main_library_collisions"].append(filename)
        incoming_hash = str(file.get("content_hash") or "").strip().lower() or None
        existing = by_name.get(_normalize_sync_name(filename))
        if existing is None:
            summary["new"] += 1
            summary["new_files"].append(filename)
            actions.append({"mode": "new", "filename": filename, "size": file_size, "existing": None})
            continue

        existing_hash = (existing.content_hash or "").strip().lower() or None
        if existing_hash and incoming_hash:
            if existing_hash == incoming_hash:
                summary["unchanged"] += 1
                actions.append(
                    {
                        "mode": "unchanged",
                        "filename": filename,
                        "size": file_size,
                        "content_hash": incoming_hash,
                        "existing": existing,
                    }
                )
                continue
        elif existing.file_size is not None and int(existing.file_size) == file_size:
            summary["unchanged"] += 1
            actions.append({"mode": "unchanged", "filename": filename, "size": file_size, "existing": existing})
            continue

        summary["modified"] += 1
        summary["modified_files"].append(filename)
        actions.append(
            {
                "mode": "modified",
                "filename": filename,
                "size": file_size,
                "content_hash": incoming_hash,
                "existing": existing,
            }
        )

    if summary["total_scanned"] > 0 and summary["new"] == 0 and summary["modified"] == 0:
        summary["up_to_date"] = True
    return summary, actions


@router.get("/reanalyze-batch-preview")
async def reanalyze_batch_preview(
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_db),
) -> dict:
    rows = await _reanalyze_candidates(session, sample_limit=limit)
    remaining_unanalyzed = int(
        (
            await session.execute(
                select(func.count(EvidenceItem.id)).where(
                    EvidenceItem.analysis_summary.is_(None),
                    EvidenceItem.status == EvidenceStatus.CURRENT,
                )
            )
        ).scalar()
        or 0
    )
    usage = await get_usage_today()
    return {
        "limit": limit,
        "documents": [str(row["filename"]) for row in rows],
        "total_unanalyzed": remaining_unanalyzed,
        "remaining_unanalyzed": remaining_unanalyzed,
        "api_usage": usage,
    }


@router.post("/search")
async def search_documents(payload: DocumentSearchRequest) -> list[dict]:
    if not payload.query.strip():
        raise HTTPException(status_code=400, detail="query is required")
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    collection = client.get_or_create_collection("meeting_notes")
    result = collection.query(query_texts=[payload.query], n_results=5)
    ids = result.get("ids", [[]])[0]
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    return [
        {
            "id": str(ids[i]),
            "snippet": str(docs[i])[:300],
            "metadata": metas[i] if i < len(metas) else {},
            "relevance_rank": i + 1,
        }
        for i in range(len(ids))
    ]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _sync_preview_inputs(
    *,
    files: list[UploadFile],
    metadata_json: str | None,
    payload: SyncPreviewRequest | None,
) -> list[dict[str, Any]]:
    if payload is not None:
        return [{"filename": row.filename, "size": int(row.size)} for row in payload.files]
    if metadata_json:
        parsed = json.loads(metadata_json)
        if isinstance(parsed, list):
            return [
                {
                    "filename": str(row.get("filename") or ""),
                    "size": int(row.get("size") or 0),
                }
                for row in parsed
                if isinstance(row, dict)
            ]
    upload_preview_rows: list[dict[str, Any]] = []
    for file in files:
        filename = str(file.filename or "")
        payload = await file.read()
        upload_preview_rows.append(
            {
                "filename": filename,
                "size": len(payload),
                "content_hash": compute_content_hash(payload),
            }
        )
    return upload_preview_rows


@router.post("/sync-preview")
async def sync_folder_preview(
    files: list[UploadFile] = File(default=[]),
    metadata_json: str | None = Form(default=None),
    library: str = Form(default="main"),
    payload: SyncPreviewRequest | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict:
    normalized_library = _normalize_library(library)
    preview_files = await _sync_preview_inputs(files=files, metadata_json=metadata_json, payload=payload)
    summary, _actions = await _classify_sync_files(session, files=preview_files, library=normalized_library)
    return summary


@router.post("/sync")
async def sync_folder(
    files: list[UploadFile] = File(...),
    bypass_limit: bool = Form(False),
    library: str = Form("main"),
) -> StreamingResponse:
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required.")
    normalized_library = _normalize_library(library)

    async def event_generator():
        processed = 0
        today = datetime.utcnow().strftime("%Y-%m-%d")
        upload_payloads: list[dict[str, Any]] = []
        for file in files:
            filename = str(file.filename or "").strip()
            upload_payloads.append(
                {
                    "filename": filename,
                    "bytes": await file.read(),
                }
            )
        preview_input = [
            {
                "filename": row["filename"],
                "size": len(row["bytes"]),
                "content_hash": compute_content_hash(row["bytes"]),
            }
            for row in upload_payloads
        ]

        async with AsyncSessionLocal() as session:
            summary, actions = await _classify_sync_files(session, files=preview_input, library=normalized_library)
            actionable = [row for row in actions if row["mode"] in {"new", "modified"}]
            if actionable and not bypass_limit:
                estimate = await estimate_batch_cost(len(actionable))
                if estimate["will_exceed_limit"]:
                    yield _sse(
                        "error",
                        {
                            "message": "Daily API limit would be exceeded.",
                            "type": "daily_limit",
                            "estimate": estimate,
                        },
                    )
                    return
            yield _sse(
                "start",
                {
                    "total_scanned": summary["total_scanned"],
                    "total_to_import": len(actionable),
                    "summary": summary,
                    "message": f"Syncing folder - importing {len(actionable)} files...",
                },
            )

            if len(actionable) == 0:
                now = datetime.utcnow().isoformat()
                sync_setting_key = "last_dpa_folder_sync" if normalized_library == "dpa" else "last_folder_sync"
                setting = (
                    await session.execute(select(AppSetting).where(AppSetting.key == sync_setting_key))
                ).scalars().first()
                if setting is None:
                    session.add(AppSetting(key=sync_setting_key, value=now, updated_at=now))
                else:
                    setting.value = now
                    setting.updated_at = now
                await log_change(
                    session,
                    category="sync",
                    action="Folder sync completed",
                    subject="documents",
                    detail=(
                        f"Folder sync: {summary['new']} new, {summary['modified']} modified, {summary['unchanged']} unchanged"
                    ),
                )
                await session.commit()
                yield _sse("complete", summary)
                return

            payload_by_name = {_normalize_sync_name(row["filename"]): row for row in upload_payloads}
            minio_client = get_minio_client()
            if not minio_client.bucket_exists(settings.minio_bucket):
                minio_client.make_bucket(settings.minio_bucket)

            import_details: list[dict[str, Any]] = []
            for action in actionable:
                filename = action["filename"]
                payload = payload_by_name.get(_normalize_sync_name(filename))
                if payload is None:
                    summary["skipped"] += 1
                    summary["errors"].append(f"Missing payload for {filename}")
                    continue
                file_bytes = payload["bytes"]
                object_name = build_import_object_name(filename)
                minio_client.put_object(
                    settings.minio_bucket,
                    object_name,
                    io.BytesIO(file_bytes),
                    length=len(file_bytes),
                    content_type="application/octet-stream",
                )
                existing: DataImport | None = action.get("existing")
                mode = action["mode"]
                if mode == "modified":
                    record = (
                        await session.execute(
                            select(DataImport)
                            .where(
                                func.lower(DataImport.filename) == filename.strip().lower(),
                                DataImport.library == normalized_library,
                            )
                            .order_by(DataImport.updated_at.desc(), DataImport.id.desc())
                            .limit(1)
                        )
                    ).scalars().first()
                    if record is None and existing is not None:
                        record = existing
                    if record is None:
                        summary["skipped"] += 1
                        summary["errors"].append(
                            f"{filename}: modified file could not be matched to an existing {normalized_library} import record"
                        )
                        continue
                    record.filename = filename
                    record.source_system = "Folder Sync"
                    record.data_date = today
                    record.file_size = len(file_bytes)
                    record.minio_path = object_name
                    record.content_hash = compute_content_hash(file_bytes)
                    record.status = ImportStatus.QUEUED
                    record.error_message = None
                    record.updated_at = datetime.utcnow()
                else:
                    record = DataImport(
                        filename=filename,
                        source_system="Folder Sync",
                        data_date=today,
                        file_size=len(file_bytes),
                        framework=None,
                        control_ids=[],
                        minio_path=object_name,
                        content_hash=compute_content_hash(file_bytes),
                        status=ImportStatus.QUEUED,
                        library=normalized_library,
                    )
                    session.add(record)
                record.library = normalized_library
                await session.commit()
                await session.refresh(record)
                import_details.append({"record_id": record.id, "filename": filename, "mode": mode})

            total_to_import = len(import_details)
            for detail in import_details:
                processed += 1
                try:
                    await process_import_with_options(
                        import_id=detail["record_id"],
                        content="",
                        trigger_auditor_refresh=True,
                        bypass_limit=bool(bypass_limit),
                        is_new_import=detail["mode"] == "new",
                    )
                except Exception as exc:  # noqa: BLE001
                    summary["errors"].append(f"{detail['filename']}: {exc}")
                current = (
                    await session.execute(select(DataImport).where(DataImport.id == detail["record_id"]))
                ).scalars().first()
                if current is not None and current.status == ImportStatus.FAILED:
                    summary["errors"].append(f"{detail['filename']}: {current.error_message or 'Import failed'}")
                if current is not None and current.status == ImportStatus.COMPLETE:
                    detail_entry = {
                        "filename": detail["filename"],
                        "controls_linked": len(current.control_ids or []),
                    }
                    if detail["mode"] == "new":
                        summary["new_details"].append(detail_entry)
                    else:
                        summary["modified_details"].append(detail_entry)
                yield _sse(
                    "file",
                    {
                        "filename": detail["filename"],
                        "mode": detail["mode"],
                        "completed": processed,
                        "total": total_to_import,
                    },
                )

            now = datetime.utcnow().isoformat()
            sync_setting_key = "last_dpa_folder_sync" if normalized_library == "dpa" else "last_folder_sync"
            setting = (
                await session.execute(select(AppSetting).where(AppSetting.key == sync_setting_key))
            ).scalars().first()
            if setting is None:
                session.add(AppSetting(key=sync_setting_key, value=now, updated_at=now))
            else:
                setting.value = now
                setting.updated_at = now
            await log_change(
                session,
                category="sync",
                action="Folder sync completed",
                subject="documents",
                detail=f"Folder sync: {summary['new']} new, {summary['modified']} modified, {summary['unchanged']} unchanged",
            )
            await session.commit()
            yield _sse("complete", summary)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.post("/reanalyze-batch")
async def reanalyze_batch(
    payload: ReanalyzeBatchRequest,
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    async with _REANALYZE_RUNTIME.lock:
        if payload.resume_only and not _REANALYZE_RUNTIME.running:
            raise HTTPException(status_code=409, detail="No active reanalysis run.")
        if not _REANALYZE_RUNTIME.running and not payload.resume_only:
            candidates = await _reanalyze_candidates(session)
            estimate = await estimate_batch_cost(len(candidates))
            if estimate["will_exceed_limit"] and not payload.bypass_limit:
                raise HTTPException(status_code=402, detail=estimate)
            _REANALYZE_RUNTIME.running = True
            _REANALYZE_RUNTIME.state = {
                "started_at": datetime.utcnow().isoformat(),
                "completed": 0,
                "total": len(candidates),
                "new_links": 0,
                "errors": 0,
                "message": "Starting document reanalysis.",
                "last_document": None,
                "documents": [str(row["filename"]) for row in candidates],
            }
            await _set_reanalyze_cancel_requested(session, False)
            await _save_reanalyze_status(
                {
                    "running": True,
                    "queued": False,
                    "job_id": None,
                    "completed": 0,
                    "total": len(candidates),
                    "new_links": 0,
                    "message": "Starting document reanalysis.",
                    "last_document": None,
                    "remaining_unanalyzed": len(candidates),
                    "started_at": _REANALYZE_RUNTIME.state["started_at"],
                    "finished_at": None,
                    "stopped_reason": None,
                }
            )
            _REANALYZE_RUNTIME.task = asyncio.create_task(
                _run_reanalysis_stream_process(candidates, bool(payload.bypass_limit))
            )

    async def event_generator():
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        _REANALYZE_RUNTIME.subscribers.add(queue)
        try:
            state = dict(_REANALYZE_RUNTIME.state)
            usage = await get_usage_today()
            yield _sse(
                "start",
                {
                    "total": int(state.get("total") or 0),
                    "documents": state.get("documents") or [],
                    "message": state.get("message") or "Reanalysis in progress.",
                    "api_usage": usage,
                },
            )
            yield _sse(
                "progress",
                {
                    "completed": int(state.get("completed") or 0),
                    "total": int(state.get("total") or 0),
                    "new_links": int(state.get("new_links") or 0),
                    "message": state.get("message") or "Reanalysis in progress.",
                    "last_document": state.get("last_document"),
                },
            )
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15)
                    yield message
                    if message.startswith("event: complete") or message.startswith("event: cancelled"):
                        break
                except TimeoutError:
                    if not _REANALYZE_RUNTIME.running:
                        break
                    yield ": ping\n\n"
        finally:
            _REANALYZE_RUNTIME.subscribers.discard(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.post("/reanalyze-batch-background")
async def reanalyze_batch_background(
    payload: ReanalyzeBackgroundRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    requested = max(1, min(int(payload.limit or 10), 50))
    estimate = await estimate_batch_cost(requested)
    if estimate["will_exceed_limit"] and not payload.bypass_limit:
        raise HTTPException(status_code=402, detail=estimate)
    job = await queue_reanalyze_job(
        session,
        limit=requested,
        bypass_limit=bool(payload.bypass_limit),
    )
    return {"job_id": job.id, "status": "queued"}


@router.post("/reanalyze-all")
async def reanalyze_all_compat(
    session: AsyncSession = Depends(get_db),
) -> dict:
    return await reanalyze_batch_background(
        ReanalyzeBackgroundRequest(limit=10, bypass_limit=False),
        session,
    )


@router.post("/reanalyze-cancel")
async def reanalyze_cancel(session: AsyncSession = Depends(get_db)) -> dict:
    await _set_reanalyze_cancel_requested(session, True)
    return {"cancel_requested": True}


@router.get("/reanalyze-status")
async def reanalyze_status(session: AsyncSession = Depends(get_db)) -> dict:
    status = await load_status(session)
    if status.get("running") and not _REANALYZE_RUNTIME.running:
        status["running"] = False
        status["queued"] = False
        status["stopped_reason"] = status.get("stopped_reason") or "disconnected"
        status["finished_at"] = status.get("finished_at") or datetime.utcnow().isoformat()
        await _save_reanalyze_status(status)
    if _REANALYZE_RUNTIME.running:
        status.update(
            {
                "running": True,
                "queued": False,
                "job_id": None,
                "completed": int(_REANALYZE_RUNTIME.state.get("completed") or 0),
                "total": int(_REANALYZE_RUNTIME.state.get("total") or 0),
                "new_links": int(_REANALYZE_RUNTIME.state.get("new_links") or 0),
                "message": str(_REANALYZE_RUNTIME.state.get("message") or "Reanalysis in progress."),
                "last_document": _REANALYZE_RUNTIME.state.get("last_document"),
                "started_at": _REANALYZE_RUNTIME.state.get("started_at"),
                "finished_at": None,
                "stopped_reason": None,
                "remaining_unanalyzed": max(
                    int(_REANALYZE_RUNTIME.state.get("total") or 0)
                    - int(_REANALYZE_RUNTIME.state.get("completed") or 0),
                    0,
                ),
            }
        )
    if not status.get("total"):
        status["remaining_unanalyzed"] = int(
            (
                await session.execute(
                    select(func.count(EvidenceItem.id)).where(
                        EvidenceItem.analysis_summary.is_(None),
                        EvidenceItem.status == EvidenceStatus.CURRENT,
                    )
                )
            ).scalar()
            or 0
        )
    return status


@router.post("/refresh-links")
async def refresh_evidence_links(session: AsyncSession = Depends(get_db)) -> dict[str, int]:
    controls = list(
        (
            await session.execute(
                select(Control).options(selectinload(Control.evidence_items))
            )
        ).scalars()
    )
    updated_controls = 0
    evidenced = 0
    reset = 0
    for control in controls:
        has_current_evidence = any(
            evidence.status == EvidenceStatus.CURRENT for evidence in (control.evidence_items or [])
        )
        if has_current_evidence:
            evidenced += 1
            if control.status != ControlStatus.EVIDENCED:
                control.status = ControlStatus.EVIDENCED
                updated_controls += 1
            continue
        if control.status == ControlStatus.EVIDENCED:
            control.status = ControlStatus.NOT_STARTED
            reset += 1
            updated_controls += 1
    await session.commit()
    return {"updated_controls": updated_controls, "evidenced": evidenced, "reset": reset}


def _serialize_duplicate_record(record: DataImport, duplicate_of_filename: str | None) -> dict:
    return {
        "import_id": record.id,
        "filename": record.filename,
        "duplicate_status": record.duplicate_status,
        "duplicate_of_id": record.duplicate_of_id,
        "duplicate_of_filename": duplicate_of_filename,
        "confidence": record.duplicate_confidence,
        "reason": record.duplicate_reason,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


@router.get("/duplicates")
async def list_duplicates(
    library: str = Query(default="main"),
    session: AsyncSession = Depends(get_db),
) -> list[dict]:
    normalized_library = _normalize_library(library)
    rows = list(
        (
            await session.execute(
                select(DataImport).where(
                    DataImport.duplicate_status.in_(["suspected", "confirmed_duplicate"]),
                    DataImport.duplicate_flag_dismissed.is_(False),
                    DataImport.library == normalized_library,
                )
            )
        ).scalars()
    )
    ids = {row.duplicate_of_id for row in rows if row.duplicate_of_id}
    related = list((await session.execute(select(DataImport).where(DataImport.id.in_(ids)))).scalars()) if ids else []
    by_id = {row.id: row.filename for row in related}
    return [_serialize_duplicate_record(row, by_id.get(row.duplicate_of_id)) for row in rows]


@router.post("/duplicates/{import_id}/dismiss")
async def dismiss_duplicate(import_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    row = (await session.execute(select(DataImport).where(DataImport.id == import_id))).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Import not found")
    row.duplicate_flag_dismissed = True
    row.duplicate_status = "false_positive"
    row.duplicate_of_id = None
    row.duplicate_confidence = None
    row.duplicate_reason = None
    await log_change(
        session,
        category="document",
        action="Duplicate flag dismissed",
        subject=row.filename,
        detail=f"Duplicate flag dismissed: {row.filename}",
    )
    await session.commit()
    return _serialize_duplicate_record(row, None)


async def _remove_evidence_links_for_filename(session: AsyncSession, filename: str) -> None:
    filename_key = filename.strip().lower()
    evidence_rows = list(
        (
            await session.execute(
                select(EvidenceItem).where(func.lower(EvidenceItem.filename) == filename_key)
            )
        ).scalars()
    )
    for evidence in evidence_rows:
        linked_rows = await _find_linked_auditor_items(session=session, evidence_id=evidence.id)
        if linked_rows:
            await _unlink_evidence_from_auditor_items(
                session=session,
                evidence_id=evidence.id,
                linked_rows=linked_rows,
            )
        await session.delete(evidence)


@router.post("/duplicates/{import_id}/confirm")
async def confirm_duplicate(import_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    selected = (await session.execute(select(DataImport).where(DataImport.id == import_id))).scalars().first()
    if selected is None:
        raise HTTPException(status_code=404, detail="Import not found")
    duplicate_of = None
    if selected.duplicate_of_id:
        duplicate_of = (
            await session.execute(select(DataImport).where(DataImport.id == selected.duplicate_of_id))
        ).scalars().first()
    target = selected
    canonical = duplicate_of
    if duplicate_of is not None and duplicate_of.id > selected.id:
        target = duplicate_of
        canonical = selected
    duplicate_name = canonical.filename if canonical is not None else "another document"
    target.status = ImportStatus.FAILED
    target.error_message = f"Confirmed duplicate of {duplicate_name}"
    target.duplicate_status = "confirmed_duplicate"
    target.duplicate_flag_dismissed = False
    await _remove_evidence_links_for_filename(session, target.filename)
    await log_change(
        session,
        category="document",
        action="Duplicate flag confirmed",
        subject=target.filename,
        detail=f"Duplicate flag confirmed: {target.filename}",
    )
    await session.commit()
    return _serialize_duplicate_record(target, duplicate_name)


@router.get("/{document_id}/download")
async def download_document(document_id: str, session: AsyncSession = Depends(get_db)) -> StreamingResponse:
    if ":" not in document_id:
        raise HTTPException(status_code=400, detail="Invalid document id")
    source, raw_id = document_id.split(":", 1)
    from minio import Minio

    client = Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=False,
    )
    filename = "download.bin"
    object_path = ""
    if source == "evidence":
        item = (
            await session.execute(select(EvidenceItem).where(EvidenceItem.id == int(raw_id)))
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="Document not found")
        filename = item.filename
        object_path = item.file_path or f"evidence/{item.filename}"
    elif source == "import":
        item = (
            await session.execute(select(DataImport).where(DataImport.id == int(raw_id)))
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="Document not found")
        filename = item.filename
        object_path = item.minio_path or f"imports/{item.filename}"
    else:
        raise HTTPException(status_code=400, detail="Unsupported document source")

    try:
        response = client.get_object(settings.minio_bucket, object_path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="File not found in object store") from exc
    data = response.read()
    response.close()
    response.release_conn()
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{document_id}/preview")
async def preview_document(document_id: str, session: AsyncSession = Depends(get_db)) -> dict:
    if ":" not in document_id:
        raise HTTPException(status_code=400, detail="Invalid document id")
    source, raw_id = document_id.split(":", 1)
    from minio import Minio

    client = Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=False,
    )
    if source == "import":
        item = (
            await session.execute(select(DataImport).where(DataImport.id == int(raw_id)))
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="Document not found")
        object_path = item.minio_path or f"imports/{item.filename}"
        try:
            response = client.get_object(settings.minio_bucket, object_path)
            file_bytes = response.read()
            response.close()
            response.release_conn()
        except Exception:
            file_bytes = b""
        return {
            "id": document_id,
            "filename": item.filename,
            "preview_text": extract_text_content(item.filename, file_bytes)[:2000],
            "doc_type": "import",
            "framework": item.framework,
            "control_ids": item.control_ids or [],
        }
    if source == "evidence":
        item = (
            await session.execute(
                select(EvidenceItem)
                .options(selectinload(EvidenceItem.controls))
                .where(EvidenceItem.id == int(raw_id))
            )
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="Document not found")
        preview_text = item.description or item.notes or "No textual preview available."
        return {
            "id": document_id,
            "filename": item.filename,
            "preview_text": preview_text,
            "doc_type": item.evidence_type.value,
            "framework": _framework_label_for_evidence(item),
            "control_ids": [control.control_id for control in item.controls],
        }
    raise HTTPException(status_code=400, detail="Unsupported document source")


@router.delete("/bulk")
async def bulk_delete_documents(
    payload: BulkDeleteRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    from minio import Minio

    client = Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=False,
    )

    deleted = 0
    failed = 0
    processed_keys: set[str] = set()
    for raw_document_id in payload.document_ids or []:
        try:
            document_id = _normalize_document_id(raw_document_id)
            source, raw_id = _split_document_id(document_id)
            filename_key = None
            if source == "import":
                row = (
                    await session.execute(select(DataImport.filename).where(DataImport.id == int(raw_id)))
                ).scalar_one_or_none()
                filename_key = (row or "").strip().lower()
            elif source == "evidence":
                row = (
                    await session.execute(select(EvidenceItem.filename).where(EvidenceItem.id == int(raw_id)))
                ).scalar_one_or_none()
                filename_key = (row or "").strip().lower()
            if filename_key and filename_key in processed_keys:
                continue
            await _delete_document_by_id(
                session=session,
                client=client,
                document_id=document_id,
                force=True,
            )
            if filename_key:
                processed_keys.add(filename_key)
            deleted += 1
        except Exception:  # noqa: BLE001
            failed += 1

    await session.commit()
    return {"deleted": deleted, "failed": failed}


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    payload: DeleteDocumentRequest | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict:
    from minio import Minio

    client = Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=False,
    )
    force = payload.force if payload else False
    deleted_filename = await _delete_document_by_id(
        session=session,
        client=client,
        document_id=document_id,
        force=force,
    )
    await session.commit()
    return {"status": "deleted", "filename": deleted_filename}


async def _delete_document_by_id(
    session: AsyncSession,
    client,
    document_id: str,
    force: bool,
) -> str:
    source, raw_id = _split_document_id(document_id)
    filename = None
    if source == "import":
        item = (
            await session.execute(select(DataImport).where(DataImport.id == int(raw_id)))
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="Document not found")
        filename = item.filename

    elif source == "evidence":
        item = (
            await session.execute(select(EvidenceItem).where(EvidenceItem.id == int(raw_id)))
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="Document not found")
        filename = item.filename

    if not filename:
        raise HTTPException(status_code=400, detail="Unsupported document source")
    return await _delete_documents_by_filename(
        session=session,
        client=client,
        filename=filename,
        force=force,
    )


def _split_document_id(document_id: str) -> tuple[str, str]:
    if ":" not in document_id:
        raise HTTPException(status_code=400, detail="Invalid document id")
    source, raw_id = document_id.split(":", 1)
    if source not in {"import", "evidence"}:
        raise HTTPException(status_code=400, detail="Unsupported document source")
    return source, raw_id


def _normalize_document_id(value: str | int) -> str:
    if isinstance(value, int):
        return f"evidence:{value}"
    return value


async def _find_linked_auditor_items(session: AsyncSession, evidence_id: int) -> list[dict]:
    rows = (
        await session.execute(
            select(
                AuditorChecklistItem,
                AuditorChecklist.name,
            ).join(
                AuditorChecklist,
                AuditorChecklist.id == AuditorChecklistItem.checklist_id,
            )
        )
    ).all()
    linked: list[dict] = []
    for item, checklist_name in rows:
        evidence_ids = item.evidence_ids or []
        if evidence_id in evidence_ids:
            linked.append(
                {
                    "item": item,
                    "checklist_name": checklist_name,
                }
            )
    return linked


async def _unlink_evidence_from_auditor_items(
    session: AsyncSession,
    evidence_id: int,
    linked_rows: list[dict],
) -> None:
    for row in linked_rows:
        item: AuditorChecklistItem = row["item"]
        remaining = [entry for entry in (item.evidence_ids or []) if entry != evidence_id]
        item.evidence_ids = remaining
        if len(remaining) == 0:
            item.status = AuditorItemStatus.OPEN
        session.add(item)


async def _delete_documents_by_filename(
    session: AsyncSession,
    client,
    filename: str,
    force: bool,
) -> str:
    filename_key = filename.strip().lower()
    evidence_rows = list(
        (
            await session.execute(
                select(EvidenceItem).where(func.lower(EvidenceItem.filename) == filename_key)
            )
        ).scalars()
    )
    import_rows = list(
        (
            await session.execute(
                select(DataImport).where(func.lower(DataImport.filename) == filename_key)
            )
        ).scalars()
    )
    if not evidence_rows and not import_rows:
        raise HTTPException(status_code=404, detail="Document not found")

    linked_rows: list[dict] = []
    for evidence in evidence_rows:
        linked_rows.extend(await _find_linked_auditor_items(session=session, evidence_id=evidence.id))
    if linked_rows and not force:
        checklist_names = sorted({row["checklist_name"] for row in linked_rows})
        detail = {
            "message": (
                f"This document is linked to {len(linked_rows)} auditor checklist item(s) "
                f"in {', '.join(checklist_names)}. Deleting it will remove it from those items "
                "and may change their status. Delete anyway?"
            ),
            "linked_items_count": len(linked_rows),
            "checklist_names": checklist_names,
        }
        raise HTTPException(status_code=409, detail=detail)

    if linked_rows:
        for evidence in evidence_rows:
            evidence_linked_rows = await _find_linked_auditor_items(
                session=session,
                evidence_id=evidence.id,
            )
            if evidence_linked_rows:
                await _unlink_evidence_from_auditor_items(
                    session=session,
                    evidence_id=evidence.id,
                    linked_rows=evidence_linked_rows,
                )

    object_paths = {
        row.file_path
        for row in evidence_rows
        if row.file_path
    } | {
        row.minio_path
        for row in import_rows
        if row.minio_path
    }
    for object_path in object_paths:
        try:
            client.remove_object(settings.minio_bucket, object_path)
        except Exception:
            pass

    for row in evidence_rows:
        await session.delete(row)
    for row in import_rows:
        await session.delete(row)
    await log_change(
        session,
        category="document",
        action="Document deleted",
        subject=filename,
        detail=f"Document deleted: {filename}",
    )
    return filename


def _framework_label_for_evidence(item: EvidenceItem) -> str:
    framework_names = sorted({control.framework.short_name for control in item.controls if control.framework})
    if not framework_names:
        return ""
    return ",".join(framework_names)


def _detected_type_from_import(record: DataImport | None) -> str | None:
    if record is None:
        return None
    for update in record.proposed_updates or []:
        if isinstance(update, str) and update.startswith("Detected type:"):
            value = update.split(":", 1)[1].strip()
            return value or None
    return None


def _display_doc_type(
    *,
    detected_type: str | None,
    evidence_type: str | None,
    filename: str,
) -> str:
    detected_map = {
        "policy_document": "Policy",
        "audit_log": "Log",
        "access_rights_report": "Report",
        "intune_compliance": "Report",
        "crowdstrike_inventory": "Report",
        "training_completion": "Spreadsheet",
        "mfa_enrollment": "Spreadsheet",
        "entra_id_export": "Spreadsheet",
        "active_employee_list": "Spreadsheet",
        "terminated_employee_list": "Spreadsheet",
        "auditor_checklist": "Spreadsheet",
        "evidence_document": "Record",
        "unknown": None,
    }
    evidence_map = {
        "policy": "Policy",
        "report": "Report",
        "record": "Record",
        "log": "Log",
        "screenshot": "Screenshot",
        "attestation": "Attestation",
        "other": "Other",
        "risk_acceptance": "Record",
        "justification": "Record",
        "script": "Other",
        "config": "Other",
        "contract": "Record",
    }
    if detected_type and detected_type in detected_map and detected_map[detected_type]:
        return detected_map[detected_type] or "Other"
    if evidence_type and evidence_type in evidence_map:
        return evidence_map[evidence_type]
    suffix = Path(filename).suffix.strip(".").upper()
    return suffix or "Other"


def _display_entity(evidence_entity: str | None, import_row: DataImport | None) -> str:
    canonical = (evidence_entity or "").strip().lower()
    if canonical in {"apprio", "canaide", "both"}:
        return canonical.title() if canonical != "both" else "Both"

    text = " ".join(
        part
        for part in [
            import_row.identified_summary if import_row else "",
            " ".join(str(entry) for entry in (import_row.proposed_updates or [])) if import_row else "",
        ]
        if part
    ).lower()
    has_apprio = "apprio" in text
    has_canaide = "canaide" in text
    if has_apprio and has_canaide:
        return "Both"
    if has_apprio:
        return "Apprio"
    if has_canaide:
        return "Canaide"
    return ""


async def _reanalyze_candidates(
    session: AsyncSession,
    sample_limit: int | None = None,
) -> list[dict]:
    stmt = (
        select(EvidenceItem)
        .where(
            EvidenceItem.analysis_summary.is_(None),
            EvidenceItem.status == EvidenceStatus.CURRENT,
        )
        .order_by(EvidenceItem.id.asc())
    )
    if sample_limit is not None:
        stmt = stmt.limit(sample_limit)
    evidence_rows = list((await session.execute(stmt)).scalars())
    output: list[dict] = []
    for evidence in evidence_rows:
        import_row = (
            await session.execute(
                select(DataImport)
                .where(
                    func.lower(DataImport.filename) == evidence.filename.strip().lower(),
                    DataImport.status == ImportStatus.COMPLETE,
                )
                .order_by(DataImport.id.desc())
                .limit(1)
            )
        ).scalars().first()
        output.append(
            {
                "filename": evidence.filename,
                "evidence_row": evidence,
                "import_row": import_row,
            }
        )
    return output


async def _broadcast_reanalyze_event(event: str, data: dict[str, Any]) -> None:
    message = _sse(event, data)
    dead_queues: list[asyncio.Queue[str]] = []
    for queue in _REANALYZE_RUNTIME.subscribers:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            dead_queues.append(queue)
    for queue in dead_queues:
        _REANALYZE_RUNTIME.subscribers.discard(queue)


async def _save_reanalyze_status(payload: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as status_session:
        row = (
            await status_session.execute(select(AppSetting).where(AppSetting.key == REANALYZE_STATUS_KEY))
        ).scalars().first()
        now = datetime.utcnow().isoformat()
        value = json.dumps(payload, separators=(",", ":"))
        if row is None:
            status_session.add(AppSetting(key=REANALYZE_STATUS_KEY, value=value, updated_at=now))
        else:
            row.value = value
            row.updated_at = now
        await status_session.commit()


async def _create_auto_recovered_import(
    session: AsyncSession,
    *,
    filename: str,
    evidence_row: EvidenceItem | None,
) -> DataImport:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    synthetic = DataImport(
        filename=filename,
        source_system="auto-recovered",
        data_date=evidence_row.collected_date if evidence_row and evidence_row.collected_date else today,
        framework=None,
        control_ids=[],
        minio_path=(evidence_row.file_path if evidence_row else None),
        status=ImportStatus.COMPLETE,
        library=(evidence_row.library if evidence_row and evidence_row.library else "main"),
        identified_summary="Auto-recovered import record created during reanalysis.",
        proposed_updates=["Auto-recovered from evidence item for reanalysis continuity."],
    )
    session.add(synthetic)
    await session.commit()
    await session.refresh(synthetic)
    return synthetic


async def _run_reanalysis_stream_process(candidates: list[dict], bypass_limit: bool) -> None:
    started = perf_counter()
    total = len(candidates)
    completed = 0
    new_links = 0
    errors = 0
    processed_since_persist = 0
    try:
        usage = await get_usage_today()
        await _broadcast_reanalyze_event(
            "start",
            {
                "total": total,
                "documents": [str(row["filename"]) for row in candidates],
                "message": "Starting document reanalysis.",
                "api_usage": usage,
            },
        )

        for row in candidates:
            filename = str(row["filename"])
            evidence_row = row.get("evidence_row")
            async with AsyncSessionLocal() as run_session:
                import_row = row.get("import_row")
                if import_row is None:
                    import_row = await _create_auto_recovered_import(
                        run_session,
                        filename=filename,
                        evidence_row=evidence_row,
                    )
                await _broadcast_reanalyze_event(
                    "progress",
                    {
                        "completed": completed,
                        "total": total,
                        "new_links": new_links,
                        "message": f"Analyzing {filename}",
                        "last_document": filename,
                    },
                )
                try:
                    result = await run_evidence_intelligence_on_import(
                        import_row,
                        run_session,
                        bypass_limit=bypass_limit,
                        max_chars=8000,
                    )
                    links_created = int(result.get("links_created") or 0)
                    new_links += links_created
                    completed += 1
                    processed_since_persist += 1
                    _REANALYZE_RUNTIME.state.update(
                        {
                            "completed": completed,
                            "new_links": new_links,
                            "last_document": filename,
                            "message": f"Processed {filename}",
                        }
                    )
                    await _broadcast_reanalyze_event(
                        "document",
                        {
                            "filename": filename,
                            "status": "complete",
                            "new_links": links_created,
                            "controls": [str(value) for value in (result.get("controls") or [])],
                            "summary": str(result.get("summary") or ""),
                            "completed": completed,
                            "total": total,
                            "new_links_total": new_links,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    if is_daily_limit_exception(exc):
                        usage_limit = await get_usage_today()
                        await _broadcast_reanalyze_event("error", {"message": str(exc), "type": "daily_limit"})
                        await _broadcast_reanalyze_event(
                            "complete",
                            {
                                "completed": completed,
                                "total": total,
                                "new_links": new_links,
                                "errors": errors,
                                "elapsed_seconds": round(perf_counter() - started, 2),
                                "stopped_reason": "daily_limit_reached",
                                "api_usage": usage_limit,
                            },
                        )
                        return
                    completed += 1
                    errors += 1
                    processed_since_persist += 1
                    _REANALYZE_RUNTIME.state.update(
                        {
                            "completed": completed,
                            "errors": errors,
                            "last_document": filename,
                            "message": f"Failed {filename}",
                        }
                    )
                    logger.error("Reanalyze failed for '{}': {}", filename, exc)
                    await _broadcast_reanalyze_event(
                        "document",
                        {
                            "filename": filename,
                            "status": "error",
                            "reason": str(exc),
                            "completed": completed,
                            "total": total,
                            "new_links_total": new_links,
                        },
                    )

                if processed_since_persist >= 10:
                    processed_since_persist = 0
                    await _save_reanalyze_status(
                        {
                            "running": True,
                            "queued": False,
                            "job_id": None,
                            "completed": completed,
                            "total": total,
                            "new_links": new_links,
                            "message": _REANALYZE_RUNTIME.state.get("message") or "",
                            "last_document": _REANALYZE_RUNTIME.state.get("last_document"),
                            "remaining_unanalyzed": max(total - completed, 0),
                            "started_at": _REANALYZE_RUNTIME.state.get("started_at"),
                            "finished_at": None,
                            "stopped_reason": None,
                        }
                    )
                if completed < total:
                    await asyncio.sleep(2)
                if await _get_reanalyze_cancel_requested(run_session):
                    usage_cancel = await get_usage_today()
                    await _broadcast_reanalyze_event(
                        "cancelled",
                        {
                            "completed": completed,
                            "total": total,
                            "new_links": new_links,
                            "errors": errors,
                            "elapsed_seconds": round(perf_counter() - started, 2),
                            "api_usage": usage_cancel,
                        },
                    )
                    await _save_reanalyze_status(
                        {
                            "running": False,
                            "queued": False,
                            "job_id": None,
                            "completed": completed,
                            "total": total,
                            "new_links": new_links,
                            "message": "Reanalysis cancelled.",
                            "last_document": _REANALYZE_RUNTIME.state.get("last_document"),
                            "remaining_unanalyzed": max(total - completed, 0),
                            "started_at": _REANALYZE_RUNTIME.state.get("started_at"),
                            "finished_at": datetime.utcnow().isoformat(),
                            "stopped_reason": "cancelled",
                        }
                    )
                    return

        usage_done = await get_usage_today()
        await _broadcast_reanalyze_event(
            "complete",
            {
                "completed": completed,
                "total": total,
                "new_links": new_links,
                "errors": errors,
                "elapsed_seconds": round(perf_counter() - started, 2),
                "stopped_reason": None,
                "api_usage": usage_done,
            },
        )
        await _save_reanalyze_status(
            {
                "running": False,
                "queued": False,
                "job_id": None,
                "completed": completed,
                "total": total,
                "new_links": new_links,
                "message": "Reanalysis complete.",
                "last_document": _REANALYZE_RUNTIME.state.get("last_document"),
                "remaining_unanalyzed": max(total - completed, 0),
                "started_at": _REANALYZE_RUNTIME.state.get("started_at"),
                "finished_at": datetime.utcnow().isoformat(),
                "stopped_reason": None,
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Reanalyze runtime task failed: {}", exc)
        await _broadcast_reanalyze_event("error", {"message": str(exc), "type": "unexpected"})
        await _save_reanalyze_status(
            {
                "running": False,
                "queued": False,
                "job_id": None,
                "completed": completed,
                "total": total,
                "new_links": new_links,
                "message": f"Reanalysis failed: {exc}",
                "last_document": _REANALYZE_RUNTIME.state.get("last_document"),
                "remaining_unanalyzed": max(total - completed, 0),
                "started_at": _REANALYZE_RUNTIME.state.get("started_at"),
                "finished_at": datetime.utcnow().isoformat(),
                "stopped_reason": "error",
            }
        )
    finally:
        _REANALYZE_RUNTIME.running = False
        _REANALYZE_RUNTIME.task = None


async def _set_reanalyze_cancel_requested(session: AsyncSession, value: bool) -> None:
    now = datetime.utcnow().isoformat()
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == "reanalyze_cancel_requested"))
    ).scalars().first()
    text = "true" if value else "false"
    if row is None:
        session.add(
            AppSetting(
                key="reanalyze_cancel_requested",
                value=text,
                updated_at=now,
            )
        )
    else:
        row.value = text
        row.updated_at = now
    await session.commit()


async def _get_reanalyze_cancel_requested(session: AsyncSession) -> bool:
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == "reanalyze_cancel_requested"))
    ).scalars().first()
    return bool(row and (row.value or "").strip().lower() == "true")

