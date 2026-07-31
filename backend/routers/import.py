from __future__ import annotations

import asyncio
import io
import json
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.compliance import BatchImport, Control, DataImport, ImportStatus
from services.change_log import log_change
from services.import_pipeline import (
    build_import_object_name,
    compute_content_hash,
    embed_import_text,
    extract_full_text_content,
    extract_text_content,
    get_minio_client,
    process_batch_import,
    process_import,
    process_import_with_options,
)
from config import get_settings

settings = get_settings()
_BLOCKED_FIXTURE_FILENAMES = {
    "auditor_questions.csv",
    "auditor_questions.txt",
    "crowdstrike.csv",
    "entra.csv",
    "intune.csv",
    "adp.csv",
    "mfa.csv",
    "training.csv",
    "mfa_with_names.csv",
    "active_employee_list_generic.csv",
    "readme.md",
}
_SUPPORTED_EXTENSIONS = {
    ".csv",
    ".xlsx",
    ".xls",
    ".pdf",
    ".docx",
    ".doc",
    ".png",
    ".jpg",
    ".jpeg",
    ".txt",
    ".md",
    ".yaml",
    ".yml",
}

router = APIRouter(prefix="/import", tags=["import"])


def _is_blocked_import_filename(filename: str) -> bool:
    normalized = Path(filename or "").name.strip().lower()
    if not normalized:
        return False
    if normalized in _BLOCKED_FIXTURE_FILENAMES:
        return True
    if normalized == "test.csv":
        return True
    if normalized.startswith("dupa"):
        return True
    if normalized.startswith("dup"):
        return True
    return False


class TextImportRequest(BaseModel):
    filename: str | None = None
    content: str
    source_system: str
    data_date: str
    control_ids: list[str] = Field(default_factory=list)
    framework: str | None = None
    notes: str | None = None
    library: str = "main"


class ImportTypeOverrideRequest(BaseModel):
    detected_type: str


class DuplicateCheckRequest(BaseModel):
    filenames: list[str] = Field(default_factory=list)


class ProcessImportRequest(BaseModel):
    bypass_limit: bool = False


@router.post("/file")
async def import_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source_system: str = Form(...),
    data_date: str = Form(...),
    control_ids: str | None = Form(None),
    framework: str | None = Form(None),
    notes: str | None = Form(None),
    auditor_engagement_name: str | None = Form(None),
    auditor_engagement_type: str | None = Form(None),
    auditor_certification_body: str | None = Form(None),
    auditor_period_year: str | None = Form(None),
    auditor_merge_with_existing: bool | None = Form(None),
    library: str = Form("main"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename is required")
    if (
        (getattr(settings, "app_env", "development") or "development").lower() != "test"
        and _is_blocked_import_filename(file.filename)
    ):
        raise HTTPException(
            status_code=400,
            detail="Fixture/sample imports are blocked outside test mode.",
        )
    _validate_data_date(data_date)

    parsed_control_ids = _parse_control_ids(control_ids)
    file_bytes = await file.read()
    object_name = build_import_object_name(file.filename)

    client = get_minio_client()
    if not await asyncio.to_thread(client.bucket_exists, settings.minio_bucket):
        await asyncio.to_thread(client.make_bucket, settings.minio_bucket)
    await asyncio.to_thread(
        client.put_object,
        settings.minio_bucket,
        object_name,
        io.BytesIO(file_bytes),
        length=len(file_bytes),
        content_type=file.content_type or "application/octet-stream",
    )

    record = DataImport(
        filename=file.filename,
        source_system=source_system,
        data_date=data_date,
        file_size=len(file_bytes),
        control_ids=parsed_control_ids,
        framework=framework,
        notes=notes,
        auditor_engagement_name=auditor_engagement_name,
        auditor_engagement_type=auditor_engagement_type,
        auditor_certification_body=auditor_certification_body,
        auditor_period_year=auditor_period_year,
        auditor_merge_with_existing=auditor_merge_with_existing,
        minio_path=object_name,
        content_hash=compute_content_hash(file_bytes),
        status=ImportStatus.QUEUED,
        library=(library or "main").strip().lower() or "main",
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)

    # Return quickly after file persistence; run extraction/classification async.
    background_tasks.add_task(process_import, record.id, "")

    return {"import_id": record.id, "filename": file.filename, "status": record.status.value}


@router.post("/batch")
async def import_batch(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    source_system: str = Form("Manual/Other"),
    data_date: str | None = Form(None),
    notes: str | None = Form(None),
    framework: str | None = Form(None),
    library: str = Form("main"),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")

    effective_date = data_date or datetime.utcnow().strftime("%Y-%m-%d")
    _validate_data_date(effective_date)
    batch_id = str(uuid.uuid4())

    client = get_minio_client()
    if not await asyncio.to_thread(client.bucket_exists, settings.minio_bucket):
        await asyncio.to_thread(client.make_bucket, settings.minio_bucket)

    skipped: list[dict[str, str]] = []
    import_ids: list[int] = []
    seen_filenames: set[str] = set()

    for upload in files:
        filename = upload.filename or ""
        if not filename:
            skipped.append({"filename": "(unnamed)", "reason": "Missing filename"})
            continue
        normalized_name = filename.strip().lower()
        if normalized_name in seen_filenames:
            skipped.append({"filename": filename, "reason": "Duplicate filename in batch request"})
            continue
        seen_filenames.add(normalized_name)
        if (
            (getattr(settings, "app_env", "development") or "development").lower() != "test"
            and _is_blocked_import_filename(filename)
        ):
            skipped.append({"filename": filename, "reason": "Fixture/sample imports are blocked outside test mode"})
            continue
        suffix = Path(filename).suffix.lower()
        if not suffix:
            skipped.append({"filename": filename, "reason": "Unsupported file type"})
            continue
        if suffix not in _SUPPORTED_EXTENSIONS:
            skipped.append({"filename": filename, "reason": f"Unsupported file type: {suffix}"})
            continue

        try:
            file_bytes = await upload.read()
            object_name = build_import_object_name(filename)
            await asyncio.to_thread(
                client.put_object,
                settings.minio_bucket,
                object_name,
                io.BytesIO(file_bytes),
                length=len(file_bytes),
                content_type=upload.content_type or "application/octet-stream",
            )
            record = DataImport(
                filename=filename,
                source_system=source_system,
                data_date=effective_date,
                file_size=len(file_bytes),
                framework=framework,
                notes=notes,
                batch_id=batch_id,
                control_ids=[],
                minio_path=object_name,
                content_hash=compute_content_hash(file_bytes),
                status=ImportStatus.QUEUED,
                library=(library or "main").strip().lower() or "main",
            )
            session.add(record)
            await session.flush()

            text = await asyncio.to_thread(extract_text_content, filename, file_bytes)
            full_text = await asyncio.to_thread(extract_full_text_content, filename, file_bytes) or text
            await asyncio.to_thread(
                embed_import_text,
                import_id=record.id,
                text=full_text,
                metadata={
                    "filename": filename,
                    "source_system": source_system,
                    "data_date": effective_date,
                    "framework": framework or "",
                    "batch_id": batch_id,
                },
            )
            import_ids.append(record.id)
        except Exception as exc:  # noqa: BLE001
            skipped.append({"filename": filename, "reason": str(exc)})

    batch_record = BatchImport(
        batch_id=batch_id,
        total_files=len(files),
        operator="Michael DuPlantis",
        skipped_files=skipped,
    )
    session.add(batch_record)
    await session.commit()

    if import_ids:
        background_tasks.add_task(process_batch_import, batch_id, import_ids)

    return {
        "batch_id": batch_id,
        "import_ids": import_ids,
        "skipped": skipped,
        "queued_count": len(import_ids),
    }


@router.post("/text")
async def import_text(
    payload: TextImportRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="content is required")
    _validate_data_date(payload.data_date)
    text_filename = (payload.filename or "").strip() or f"text-import-{int(datetime.utcnow().timestamp())}.txt"
    content_bytes = payload.content.encode("utf-8")
    object_name = build_import_object_name(text_filename)

    client = get_minio_client()
    if not await asyncio.to_thread(client.bucket_exists, settings.minio_bucket):
        await asyncio.to_thread(client.make_bucket, settings.minio_bucket)
    await asyncio.to_thread(
        client.put_object,
        settings.minio_bucket,
        object_name,
        io.BytesIO(content_bytes),
        length=len(content_bytes),
        content_type="text/plain",
    )

    record = DataImport(
        filename=text_filename,
        source_system=payload.source_system,
        data_date=payload.data_date,
        file_size=len(content_bytes),
        control_ids=payload.control_ids,
        framework=payload.framework,
        notes=payload.notes,
        minio_path=object_name,
        content_hash=compute_content_hash(content_bytes),
        status=ImportStatus.QUEUED,
        library=(payload.library or "main").strip().lower() or "main",
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)

    await asyncio.to_thread(
        embed_import_text,
        import_id=record.id,
        text=payload.content,
        metadata={
            "filename": text_filename,
            "source_system": payload.source_system,
            "data_date": payload.data_date,
            "framework": payload.framework or "",
        },
    )
    background_tasks.add_task(process_import, record.id, payload.content)

    return {"import_id": record.id, "filename": text_filename, "status": record.status.value}


@router.get("/batch/{batch_id}/status")
async def batch_status(batch_id: str, session: AsyncSession = Depends(get_db)) -> dict:
    batch = (
        await session.execute(select(BatchImport).where(BatchImport.batch_id == batch_id))
    ).scalars().first()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    imports = list(
        (
            await session.execute(
                select(DataImport)
                .where(DataImport.batch_id == batch_id)
                .order_by(DataImport.created_at.asc())
            )
        ).scalars()
    )
    queued = len([row for row in imports if row.status == ImportStatus.QUEUED])
    processing = len([row for row in imports if row.status == ImportStatus.PROCESSING])
    complete = len([row for row in imports if row.status == ImportStatus.COMPLETE])
    failed = len([row for row in imports if row.status == ImportStatus.FAILED])
    skipped = int(batch.total_files or 0) - len(imports)
    imports_payload = [
        {
            "import_id": row.id,
            "filename": row.filename,
            "status": row.status.value,
            "detected_type": _history_item(row).get("detected_type"),
            "detection_confidence": _history_item(row).get("detection_confidence"),
            "agent_summary": row.identified_summary,
            "error_message": row.error_message,
            "retry_count": row.retry_count,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in imports
    ]
    return {
        "batch_id": batch_id,
        "total_files": int(batch.total_files or 0),
        "queued": queued,
        "processing": processing,
        "complete": complete,
        "failed": failed,
        "skipped": max(0, skipped),
        "imports": imports_payload,
    }


@router.get("/history")
async def import_history(session: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await session.execute(select(DataImport).order_by(DataImport.created_at.desc()))
    records = list(result.scalars())
    return [
        _history_item(record)
        for record in records
    ]


@router.get("/{import_id}/status")
async def import_status(import_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    record = (
        await session.execute(select(DataImport).where(DataImport.id == import_id))
    ).scalars().first()
    if record is None:
        raise HTTPException(status_code=404, detail="Import not found")
    return _history_item(record)


@router.get("/batches")
async def list_batches(
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_db),
) -> list[dict]:
    batches = list(
        (
            await session.execute(
                select(BatchImport)
                .order_by(BatchImport.created_at.desc())
                .limit(limit)
            )
        ).scalars()
    )
    payload: list[dict] = []
    for batch in batches:
        imports = list(
            (
                await session.execute(
                    select(DataImport).where(DataImport.batch_id == batch.batch_id)
                )
            ).scalars()
        )
        payload.append(
            {
                "batch_id": batch.batch_id,
                "created_at": batch.created_at.isoformat(),
                "total_files": int(batch.total_files or 0),
                "complete": len([row for row in imports if row.status == ImportStatus.COMPLETE]),
                "failed": len([row for row in imports if row.status == ImportStatus.FAILED]),
                "processing": len([row for row in imports if row.status == ImportStatus.PROCESSING]),
                "queued": len([row for row in imports if row.status == ImportStatus.QUEUED]),
                "skipped": max(0, int(batch.total_files or 0) - len(imports)),
            }
        )
    return payload


@router.post("/check-duplicates")
async def check_duplicates(payload: DuplicateCheckRequest, session: AsyncSession = Depends(get_db)) -> dict[str, list[str]]:
    if not payload.filenames:
        return {"duplicates": []}
    normalized = sorted({name.strip().lower() for name in payload.filenames if name.strip()})
    if not normalized:
        return {"duplicates": []}
    duplicates = list(
        (
            await session.execute(
                select(DataImport.filename).where(
                    func.lower(DataImport.filename).in_(normalized)
                )
            )
        ).scalars()
    )
    return {"duplicates": duplicates}


@router.post("/reprocess/{import_id}")
async def reprocess_import(
    import_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    record = (
        await session.execute(select(DataImport).where(DataImport.id == import_id))
    ).scalars().first()
    if record is None:
        raise HTTPException(status_code=404, detail="Import not found")
    if not record.minio_path:
        raise HTTPException(status_code=400, detail="Import has no stored file payload")
    record.status = ImportStatus.QUEUED
    record.error_message = None
    await session.commit()
    background_tasks.add_task(process_import, record.id, "")
    return {"import_id": record.id, "status": record.status.value}


@router.post("/{import_id}/process")
async def process_queued_import(
    import_id: int,
    payload: ProcessImportRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    record = (
        await session.execute(select(DataImport).where(DataImport.id == import_id))
    ).scalars().first()
    if record is None:
        raise HTTPException(status_code=404, detail="Import not found")
    if record.status not in {ImportStatus.QUEUED, ImportStatus.FAILED}:
        raise HTTPException(status_code=400, detail="Only queued or failed imports can be reprocessed")
    if not record.minio_path:
        raise HTTPException(status_code=400, detail="Import has no stored file payload")

    await process_import_with_options(
        import_id=record.id,
        content="",
        trigger_auditor_refresh=True,
        bypass_limit=bool(payload.bypass_limit),
    )

    refreshed = (
        await session.execute(select(DataImport).where(DataImport.id == import_id))
    ).scalars().first()
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Import not found after processing")
    return _history_item(refreshed)


@router.post("/batch/{batch_id}/retry-failed-rate-limit")
async def retry_failed_rate_limited_imports(
    batch_id: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
) -> dict[str, int | str]:
    imports = list(
        (
            await session.execute(
                select(DataImport).where(DataImport.batch_id == batch_id)
            )
        ).scalars()
    )
    if not imports:
        raise HTTPException(status_code=404, detail="Batch not found")

    retried = 0
    for record in imports:
        message = (record.error_message or "").lower()
        if record.status == ImportStatus.FAILED and (
            "rate_limit" in message or "rate limit" in message or "429" in message
        ):
            record.status = ImportStatus.QUEUED
            record.error_message = None
            background_tasks.add_task(process_import, record.id, "")
            retried += 1
    await session.commit()
    return {"batch_id": batch_id, "retried": retried}


@router.patch("/{import_id}/force-fail")
async def force_fail_import(
    import_id: int,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    record = (
        await session.execute(select(DataImport).where(DataImport.id == import_id))
    ).scalars().first()
    if record is None:
        raise HTTPException(status_code=404, detail="Import not found")
    if record.status in {ImportStatus.COMPLETE, ImportStatus.FAILED}:
        return {"import_id": record.id, "status": record.status.value}
    record.status = ImportStatus.FAILED
    record.error_message = "Manually failed by operator"
    await log_change(
        session,
        category="import",
        action="Import force-failed",
        subject=record.filename,
        detail=f"Import {record.id} force-failed: {record.filename}",
        triggered_by="api",
    )
    await session.commit()
    return {"import_id": record.id, "status": record.status.value}


@router.patch("/{import_id}/override-type")
async def override_detected_type(
    import_id: int,
    payload: ImportTypeOverrideRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    record = (
        await session.execute(select(DataImport).where(DataImport.id == import_id))
    ).scalars().first()
    if record is None:
        raise HTTPException(status_code=404, detail="Import not found")
    marker = f"[override_type:{payload.detected_type}]"
    notes = record.notes or ""
    if "[override_type:" in notes:
        start = notes.index("[override_type:")
        end = notes.find("]", start)
        if end != -1:
            notes = notes[:start].rstrip() + " " + notes[end + 1 :].lstrip()
    record.notes = f"{notes.strip()} {marker}".strip()
    await log_change(
        session,
        category="import",
        action="Import type overridden",
        subject=record.filename,
        detail=f"Import {record.id} type overridden to {payload.detected_type}",
        triggered_by="api",
    )
    await session.commit()
    return {"import_id": record.id, "status": "override_saved", "detected_type": payload.detected_type}


def _history_item(record: DataImport) -> dict:
    detected_type = None
    confidence = None
    recommended_action = None
    relevant_controls: list[str] = []
    column_mapping: dict = {}
    for update in record.proposed_updates or []:
        if update.startswith("Detected type:"):
            detected_type = update.split(":", 1)[1].strip()
        elif update.startswith("Detection confidence:"):
            confidence = update.split(":", 1)[1].strip()
        elif update.startswith("Recommended action:"):
            recommended_action = update.split(":", 1)[1].strip()
        elif update.startswith("Relevant controls (AI):"):
            controls = update.split(":", 1)[1].strip()
            if controls and controls.lower() != "none":
                relevant_controls = [item.strip() for item in controls.split(",") if item.strip()]
        elif update.startswith("Column mapping:"):
            payload = update.split(":", 1)[1].strip()
            try:
                column_mapping = json.loads(payload)
            except json.JSONDecodeError:
                column_mapping = {}
    return {
            "import_id": record.id,
            "filename": record.filename,
            "source_system": record.source_system,
            "data_date": record.data_date,
            "status": record.status.value,
            "library": record.library,
            "identified_summary": record.identified_summary,
            "proposed_updates": record.proposed_updates or [],
            "detected_type": detected_type,
            "detection_confidence": confidence,
            "recommended_action": recommended_action,
            "relevant_controls": relevant_controls,
            "column_mapping": column_mapping,
            "error_message": record.error_message,
            "retry_count": record.retry_count,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }


@router.get("/controls")
async def list_controls(session: AsyncSession = Depends(get_db)) -> list[str]:
    result = await session.execute(select(Control.control_id).order_by(Control.control_id.asc()))
    return list(result.scalars())


def _validate_data_date(data_date: str) -> None:
    try:
        datetime.strptime(data_date, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="data_date must be YYYY-MM-DD") from exc


def _parse_control_ids(raw_control_ids: str | None) -> list[str]:
    if not raw_control_ids:
        return []
    try:
        parsed = json.loads(raw_control_ids)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        return [value.strip() for value in raw_control_ids.split(",") if value.strip()]
    return []
