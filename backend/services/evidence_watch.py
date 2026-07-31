from __future__ import annotations

import asyncio
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select

from config import get_settings
from database import AsyncSessionLocal
from models.compliance import DataImport, ImportStatus
from services.document_sync import classify_sync_files, normalize_library
from services.import_pipeline import (
    build_import_object_name,
    compute_content_hash,
    get_minio_client,
    process_import_with_options,
)
from services.change_log import log_change

# Skip macOS / editor noise in the drop folder
_SKIP_NAMES = {".ds_store", "thumbs.db", ".gitkeep", ".gitignore"}
_SKIP_DIRS = {"processed", "quarantine"}
_SKIP_PREFIXES = (".", "~$",)
_SUPPORTED_SUFFIXES = {
    ".pdf",
    ".docx",
    ".doc",
    ".txt",
    ".md",
    ".csv",
    ".xlsx",
    ".xlsm",
    ".xls",
    ".json",
    ".yaml",
    ".yml",
    ".log",
    ".png",
    ".jpg",
    ".jpeg",
}


def _should_skip(path: Path) -> bool:
    name = path.name
    lower = name.lower()
    if lower in _SKIP_NAMES:
        return True
    if any(lower.startswith(prefix) for prefix in _SKIP_PREFIXES):
        return True
    if path.is_dir():
        return True
    if path.suffix.lower() not in _SUPPORTED_SUFFIXES:
        return True
    return False


def _iter_watch_files(watch_path: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(watch_path.iterdir()):
        if path.is_dir() and path.name.lower() in _SKIP_DIRS:
            continue
        if _should_skip(path):
            continue
        files.append(path)
    return files


async def ingest_local_file(
    *,
    path: Path,
    mode: str,
    existing: DataImport | None,
    library: str,
) -> dict[str, Any]:
    """Local-path entry into the existing import pipeline (MinIO + process_import_with_options)."""
    file_bytes = await asyncio.to_thread(path.read_bytes)
    filename = path.name
    content_hash = compute_content_hash(file_bytes)
    object_name = build_import_object_name(filename)
    client = get_minio_client()
    settings = get_settings()
    if not await asyncio.to_thread(client.bucket_exists, settings.minio_bucket):
        await asyncio.to_thread(client.make_bucket, settings.minio_bucket)
    await asyncio.to_thread(
        client.put_object,
        settings.minio_bucket,
        object_name,
        io.BytesIO(file_bytes),
        length=len(file_bytes),
        content_type="application/octet-stream",
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as session:
        if mode == "modified" and existing is not None:
            record = (
                await session.execute(select(DataImport).where(DataImport.id == existing.id))
            ).scalar_one_or_none()
            if record is None:
                raise ValueError(f"Existing import {existing.id} not found for {filename}")
            record.filename = filename
            record.source_system = "Evidence Watch"
            record.data_date = today
            record.file_size = len(file_bytes)
            record.minio_path = object_name
            record.content_hash = content_hash
            record.status = ImportStatus.QUEUED
            record.error_message = None
            record.library = library
            record.updated_at = datetime.now(timezone.utc)
            import_id = record.id
            is_new = False
        else:
            record = DataImport(
                filename=filename,
                source_system="Evidence Watch",
                data_date=today,
                file_size=len(file_bytes),
                framework=None,
                control_ids=[],
                minio_path=object_name,
                content_hash=content_hash,
                status=ImportStatus.QUEUED,
                library=library,
            )
            session.add(record)
            await session.flush()
            import_id = record.id
            is_new = True
        await session.commit()

    await process_import_with_options(
        import_id=import_id,
        content="",
        trigger_auditor_refresh=True,
        bypass_limit=False,
        is_new_import=is_new,
    )
    return {"import_id": import_id, "filename": filename, "mode": mode}


async def scan_evidence_drop_once() -> dict[str, Any]:
    from services.background_jobs import enqueue_evidence_watch_ingest

    settings = get_settings()
    watch_path = Path(settings.evidence_watch_path)
    library = normalize_library(settings.evidence_watch_library)
    watch_path.mkdir(parents=True, exist_ok=True)
    (watch_path / "processed").mkdir(parents=True, exist_ok=True)
    (watch_path / "quarantine").mkdir(parents=True, exist_ok=True)

    file_rows: list[dict[str, Any]] = []
    path_by_name: dict[str, Path] = {}
    for path in _iter_watch_files(watch_path):
        try:
            payload = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            logger.warning("Evidence watch skip unreadable {}: {}", path, exc)
            continue
        file_rows.append(
            {
                "filename": path.name,
                "size": len(payload),
                "content_hash": compute_content_hash(payload),
            }
        )
        path_by_name[path.name] = path

    async with AsyncSessionLocal() as session:
        summary, actions = await classify_sync_files(session, files=file_rows, library=library)

    queued_new = 0
    queued_modified = 0
    skipped = int(summary.get("unchanged", 0)) + int(summary.get("skipped", 0))
    errors: list[str] = list(summary.get("errors") or [])

    for action in actions:
        mode = action.get("mode")
        if mode not in {"new", "modified"}:
            continue
        filename = str(action.get("filename") or "")
        path = path_by_name.get(filename)
        if path is None:
            continue
        existing = action.get("existing")
        existing_id = getattr(existing, "id", None)
        try:
            async with AsyncSessionLocal() as session:
                job = await enqueue_evidence_watch_ingest(
                    session,
                    path=str(path),
                    mode=mode,
                    library=library,
                    existing_import_id=int(existing_id) if existing_id is not None else None,
                )
            if job is None:
                skipped += 1
                continue
            if mode == "new":
                queued_new += 1
            else:
                queued_modified += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{filename}: {exc}")
            logger.error("Evidence watch enqueue failed for {}: {}", filename, exc)

    result = {
        "scanned": len(file_rows),
        "new": queued_new,
        "modified": queued_modified,
        "queued": queued_new + queued_modified,
        "skipped": skipped,
        "errors": errors,
        "library": library,
        "path": str(watch_path),
    }
    logger.info(
        "Evidence watch cycle: path={} library={} scanned={} queued_new={} queued_modified={} skipped={} errors={}",
        watch_path,
        library,
        result["scanned"],
        queued_new,
        queued_modified,
        skipped,
        len(errors),
    )
    if queued_new or queued_modified:
        async with AsyncSessionLocal() as session:
            await log_change(
                session,
                category="document",
                action="Evidence watch scan",
                subject=str(watch_path),
                detail=(
                    f"Evidence watch queued: new={queued_new}, modified={queued_modified}, "
                    f"skipped={skipped}, scanned={len(file_rows)}"
                ),
            )
            await session.commit()
    return result


async def run_evidence_watch_loop() -> None:
    settings = get_settings()
    interval = max(5, int(settings.evidence_watch_interval_seconds or 60))
    logger.info(
        "Evidence watch loop started: path={} interval={}s library={} enabled={}",
        settings.evidence_watch_path,
        interval,
        settings.evidence_watch_library,
        settings.evidence_watch_enabled,
    )
    while True:
        try:
            if settings.evidence_watch_enabled:
                await scan_evidence_drop_once()
            else:
                logger.debug("Evidence watch disabled via EVIDENCE_WATCH_ENABLED")
        except Exception as exc:  # noqa: BLE001
            logger.error("Evidence watch loop error: {}", exc)
        await asyncio.sleep(interval)
        # Refresh settings each cycle so env changes via reload are picked up when possible
        settings = get_settings()
        interval = max(5, int(settings.evidence_watch_interval_seconds or 60))
