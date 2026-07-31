from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai.gateway import get_usage_today, is_daily_limit_exception
from database import AsyncSessionLocal
from models.compliance import (
    AppSetting,
    BackgroundJob,
    DataImport,
    EvidenceItem,
    EvidenceStatus,
    ImportStatus,
)
from services.import_pipeline import run_evidence_intelligence_on_import

REANALYZE_STATUS_KEY = "reanalyze_status"
REANALYZE_JOB_TYPE = "reanalyze_batch"
EVIDENCE_WATCH_INGEST_JOB_TYPE = "evidence_watch_ingest"
_WORKER_JOB_TYPES = (REANALYZE_JOB_TYPE, EVIDENCE_WATCH_INGEST_JOB_TYPE)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _tomorrow_utc_iso() -> str:
    now = _utc_now()
    tomorrow = (now + timedelta(days=1)).date()
    return datetime(
        tomorrow.year,
        tomorrow.month,
        tomorrow.day,
        0,
        0,
        0,
        tzinfo=timezone.utc,
    ).isoformat()


def _status_default() -> dict[str, Any]:
    return {
        "running": False,
        "queued": False,
        "job_id": None,
        "completed": 0,
        "total": 0,
        "new_links": 0,
        "message": "",
        "last_document": None,
        "remaining_unanalyzed": 0,
        "started_at": None,
        "finished_at": None,
        "stopped_reason": None,
    }


async def _save_status(session: AsyncSession, payload: dict[str, Any]) -> None:
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == REANALYZE_STATUS_KEY))
    ).scalars().first()
    value = json.dumps(payload, separators=(",", ":"))
    now = _utc_now().isoformat()
    if row is None:
        session.add(AppSetting(key=REANALYZE_STATUS_KEY, value=value, updated_at=now))
    else:
        row.value = value
        row.updated_at = now
    await session.commit()


async def load_status(session: AsyncSession) -> dict[str, Any]:
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == REANALYZE_STATUS_KEY))
    ).scalars().first()
    if row is None or not row.value:
        return _status_default()
    try:
        parsed = json.loads(row.value)
    except json.JSONDecodeError:
        return _status_default()
    return {**_status_default(), **parsed}


async def queue_reanalyze_job(
    session: AsyncSession,
    *,
    limit: int,
    bypass_limit: bool,
) -> BackgroundJob:
    payload = {
        "limit": max(1, min(int(limit), 50)),
        "bypass_limit": bool(bypass_limit),
    }
    job = BackgroundJob(
        job_type=REANALYZE_JOB_TYPE,
        status="queued",
        payload=payload,
        created_at=_utc_now(),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    status = await load_status(session)
    status.update(
        {
            "queued": True,
            "running": status.get("running", False),
            "job_id": job.id,
            "message": "Queued for background processing",
            "finished_at": None,
        }
    )
    await _save_status(session, status)
    return job


async def _count_remaining_unanalyzed(session: AsyncSession) -> int:
    return int(
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


async def get_next_queued_job(session: AsyncSession) -> BackgroundJob | None:
    jobs = list(
        (
            await session.execute(
            select(BackgroundJob)
            .where(
                BackgroundJob.status == "queued",
                BackgroundJob.job_type.in_(_WORKER_JOB_TYPES),
            )
            .order_by(BackgroundJob.created_at.asc(), BackgroundJob.id.asc())
        )
        ).scalars()
    )
    if not jobs:
        return None
    now = _utc_now()
    job: BackgroundJob | None = None
    for candidate in jobs:
        payload = candidate.payload or {}
        pause_until = _parse_iso_utc(payload.get("pause_until"))
        if pause_until and pause_until > now:
            continue
        job = candidate
        break
    if job is None:
        return None
    job.status = "processing"
    job.started_at = _utc_now()
    job.error_message = None
    await session.commit()
    await session.refresh(job)
    return job


async def enqueue_evidence_watch_ingest(
    session: AsyncSession,
    *,
    path: str,
    mode: str,
    library: str,
    existing_import_id: int | None = None,
) -> BackgroundJob | None:
    """Enqueue watch ingest if the same path is not already queued/processing."""
    pending = list(
        (
            await session.execute(
                select(BackgroundJob).where(
                    BackgroundJob.job_type == EVIDENCE_WATCH_INGEST_JOB_TYPE,
                    BackgroundJob.status.in_(("queued", "processing")),
                )
            )
        ).scalars()
    )
    for row in pending:
        if (row.payload or {}).get("path") == path:
            logger.info("Evidence watch ingest already queued for path={}", path)
            return None
    job = BackgroundJob(
        job_type=EVIDENCE_WATCH_INGEST_JOB_TYPE,
        status="queued",
        payload={
            "path": path,
            "mode": mode,
            "library": library,
            "existing_import_id": existing_import_id,
        },
        created_at=_utc_now(),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    logger.info("Queued evidence_watch_ingest job id={} path={}", job.id, path)
    return job


def _move_watch_file(src: Path, dest_dir_name: str) -> Path:
    source = Path(src)
    dest_dir = source.parent / dest_dir_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source.name
    if dest.exists():
        stem = source.stem
        suffix = source.suffix
        n = 2
        while True:
            candidate = dest_dir / f"{stem} ({n}){suffix}"
            if not candidate.exists():
                dest = candidate
                break
            n += 1
    source.rename(dest)
    return dest


async def process_evidence_watch_ingest_job(job: BackgroundJob) -> None:
    from services.evidence_watch import ingest_local_file

    payload = job.payload or {}
    path_str = str(payload.get("path") or "")
    mode = str(payload.get("mode") or "new")
    library = str(payload.get("library") or "main")
    existing_import_id = payload.get("existing_import_id")
    path = Path(path_str)
    started = _utc_now()
    error_message: str | None = None
    result: dict[str, Any] = {}

    try:
        if not path_str or not path.is_file():
            raise FileNotFoundError(f"Watch ingest path missing: {path_str}")
        existing: DataImport | None = None
        if existing_import_id is not None:
            async with AsyncSessionLocal() as session:
                existing = (
                    await session.execute(
                        select(DataImport).where(DataImport.id == int(existing_import_id))
                    )
                ).scalar_one_or_none()
        ingest_result = await ingest_local_file(
            path=path,
            mode=mode,
            existing=existing,
            library=library,
        )
        moved = _move_watch_file(path, "processed")
        result = {**ingest_result, "moved_to": str(moved)}
        status = "complete"
    except Exception as exc:  # noqa: BLE001
        error_message = str(exc)
        status = "failed"
        logger.error("Evidence watch ingest job id={} failed: {}", job.id, exc)
        try:
            if path.is_file():
                moved = _move_watch_file(path, "quarantine")
                result = {"moved_to": str(moved), "error": error_message}
            else:
                result = {"error": error_message}
        except Exception as move_exc:  # noqa: BLE001
            logger.error("Failed to quarantine {}: {}", path, move_exc)
            result = {"error": error_message, "quarantine_error": str(move_exc)}

    finished = _utc_now()
    async with AsyncSessionLocal() as session:
        job_row = (
            await session.execute(select(BackgroundJob).where(BackgroundJob.id == job.id))
        ).scalars().first()
        if job_row is not None:
            job_row.status = status
            job_row.finished_at = finished
            job_row.error_message = error_message
            job_row.result = {
                **result,
                "elapsed_seconds": int((finished - started).total_seconds()),
            }
            await session.commit()


async def process_reanalysis_job(job: BackgroundJob) -> None:
    payload = job.payload or {}
    limit = max(1, min(int(payload.get("limit", 10) or 10), 50))
    bypass_limit = False
    if bool(payload.get("bypass_limit", False)):
        logger.warning(
            "Ignoring bypass_limit=True for background job id={} to enforce daily API limits",
            job.id,
        )
    started = _utc_now()

    async with AsyncSessionLocal() as session:
        rows = list(
            (
                await session.execute(
                    select(EvidenceItem)
                    .where(
                        EvidenceItem.analysis_summary.is_(None),
                        EvidenceItem.status == EvidenceStatus.CURRENT,
                    )
                    .order_by(EvidenceItem.id.asc())
                    .limit(limit)
                )
            ).scalars()
        )
        status = await load_status(session)
        status.update(
            {
                "queued": False,
                "running": True,
                "job_id": job.id,
                "completed": 0,
                "total": len(rows),
                "new_links": 0,
                "message": "Analyzing documents in background",
                "last_document": None,
                "started_at": started.isoformat(),
                "finished_at": None,
                "stopped_reason": None,
            }
        )
        await _save_status(session, status)

    analyzed = 0
    new_links = 0
    documents: list[dict[str, Any]] = []
    stopped_reason: str | None = None

    for evidence in rows:
        async with AsyncSessionLocal() as session:
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
            if import_row is None:
                logger.warning(
                    "Background reanalysis skipped '{}': no matching complete import",
                    evidence.filename,
                )
                continue
            try:
                result = await run_evidence_intelligence_on_import(
                    import_row,
                    session,
                    bypass_limit=bypass_limit,
                )
            except Exception as exc:  # noqa: BLE001
                if is_daily_limit_exception(exc):
                    stopped_reason = "daily_limit_reached"
                    logger.warning("Background reanalysis hit daily limit at '{}'", import_row.filename)
                    break
                raise

            analyzed += 1
            links = int(result.get("links_created") or 0)
            new_links += links
            documents.append(
                {
                    "filename": import_row.filename,
                    "new_links": links,
                    "controls": [str(value) for value in (result.get("controls") or [])],
                }
            )
            remaining = await _count_remaining_unanalyzed(session)
            status = await load_status(session)
            status.update(
                {
                    "running": True,
                    "queued": False,
                    "job_id": job.id,
                    "completed": analyzed,
                    "total": len(rows),
                    "new_links": new_links,
                    "last_document": import_row.filename,
                    "remaining_unanalyzed": remaining,
                    "message": f"Analyzing {import_row.filename}",
                    "stopped_reason": None,
                }
            )
            await _save_status(session, status)

        await asyncio.sleep(2)

    finished = _utc_now()
    async with AsyncSessionLocal() as session:
        job_row = (
            await session.execute(select(BackgroundJob).where(BackgroundJob.id == job.id))
        ).scalars().first()
        if job_row is not None:
            remaining_requested = max(0, limit - analyzed)
            if stopped_reason == "daily_limit_reached" and remaining_requested > 0:
                next_payload = dict(job_row.payload or {})
                next_payload["limit"] = remaining_requested
                next_payload["bypass_limit"] = False
                next_payload["pause_until"] = _tomorrow_utc_iso()
                job_row.payload = next_payload
                job_row.status = "queued"
                job_row.started_at = None
                logger.warning(
                    "Daily limit reached — pausing job until tomorrow. job_id={} remaining={}",
                    job_row.id,
                    remaining_requested,
                )
            else:
                job_row.status = "complete"
            job_row.finished_at = finished
            job_row.result = {
                "analyzed": analyzed,
                "requested": limit,
                "new_links": new_links,
                "documents": documents,
                "elapsed_seconds": int((finished - started).total_seconds()),
                "stopped_reason": stopped_reason,
            }
            await session.commit()

        remaining = await _count_remaining_unanalyzed(session)
        usage = await get_usage_today()
        status = await load_status(session)
        status.update(
            {
                "running": False,
                "queued": stopped_reason == "daily_limit_reached",
                "job_id": job.id,
                "completed": analyzed,
                "total": limit,
                "new_links": new_links,
                "remaining_unanalyzed": remaining,
                "finished_at": finished.isoformat(),
                "message": (
                    "Daily limit reached — pausing job until tomorrow."
                    if stopped_reason == "daily_limit_reached"
                    else f"Background reanalysis complete: {analyzed}/{limit} documents."
                ),
                "stopped_reason": stopped_reason,
                "api_usage": usage,
            }
        )
        await _save_status(session, status)


async def run_background_job_worker() -> None:
    logger.info("Background job worker started")
    while True:
        try:
            async with AsyncSessionLocal() as session:
                enabled_setting = (
                    await session.execute(
                        select(AppSetting).where(AppSetting.key == "background_jobs_enabled")
                    )
                ).scalars().first()
                enabled = (enabled_setting.value if enabled_setting else "true").lower() == "true"
            if not enabled:
                await asyncio.sleep(5)
                continue
            async with AsyncSessionLocal() as session:
                job = await get_next_queued_job(session)
            if job is not None:
                logger.info("Processing background job id={} type={}", job.id, job.job_type)
                if job.job_type == EVIDENCE_WATCH_INGEST_JOB_TYPE:
                    await process_evidence_watch_ingest_job(job)
                else:
                    await process_reanalysis_job(job)
        except Exception as exc:  # noqa: BLE001
            logger.error("Background worker error: {}", exc)
        await asyncio.sleep(5)
