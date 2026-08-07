import asyncio
from datetime import datetime, timedelta, timezone
import importlib

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from minio import Minio
from minio.error import S3Error
from sqlalchemy import select, text
from starlette.middleware.base import BaseHTTPMiddleware

from config import get_settings
from database import init_db
from routers import (
    auditor as auditor_router,
    chat,
    controls,
    dashboard,
    documents,
    evidence,
    findings,
    frameworks,
    history,
    ingest,
    obligations,
    personnel,
    reports,
    settings as settings_router,
    workforce,
)
from routers.settings import seed_audit_date_settings, seed_api_usage_settings
from database import AsyncSessionLocal
from models.compliance import AppSetting, BatchImport, DataImport, ImportStatus
from services.background_jobs import run_background_job_worker
from services.evidence_watch import run_evidence_watch_loop
from services.import_pipeline import (
    backfill_missing_content_hashes_if_needed,
    process_batch_import,
    run_embedding_migration_if_needed,
)

settings = get_settings()
import_router = importlib.import_module("routers.import")
_background_worker_task: asyncio.Task[None] | None = None
_evidence_watch_task: asyncio.Task[None] | None = None

app = FastAPI(title="Compliance Platform API")

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_DEV_KEY_HEADER = "X-CCOA-Dev-Key"


class DevWriteKeyMiddleware(BaseHTTPMiddleware):
    """Local write-gate stopgap until Cognito/API Gateway JWT at GovCloud tier.

    When CCOA_DEV_KEY is set, require matching X-CCOA-Dev-Key on write methods.
    When unset, writes remain open (development convenience).
    """

    async def dispatch(self, request: Request, call_next):
        if request.method in _WRITE_METHODS:
            expected = (settings.ccoa_dev_key or "").strip()
            if expected:
                provided = (request.headers.get(_DEV_KEY_HEADER) or "").strip()
                if provided != expected:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": f"Missing or invalid {_DEV_KEY_HEADER}"},
                    )
        return await call_next(request)


app.add_middleware(DevWriteKeyMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event() -> None:
    global _background_worker_task, _evidence_watch_task
    await init_db()
    await _ensure_auditor_schema_columns()
    await _load_all_frameworks_on_startup()
    await _cleanup_stale_imports_on_startup()
    content_hash_backfill_stats = await backfill_missing_content_hashes_if_needed(null_ratio_threshold=0.5)
    if content_hash_backfill_stats.get("ran"):
        logger.info(
            "Content hash backfill completed: updated={}, errors={}, null_hash_before={}/{} ({:.1f}%)",
            content_hash_backfill_stats.get("updated", 0),
            content_hash_backfill_stats.get("errors", 0),
            content_hash_backfill_stats.get("null_hash_imports", 0),
            content_hash_backfill_stats.get("total_imports", 0),
            float(content_hash_backfill_stats.get("null_ratio", 0.0)) * 100.0,
        )
    else:
        logger.info(
            "Content hash backfill skipped: null_hash_imports={}/{} ({:.1f}%), threshold=50%",
            content_hash_backfill_stats.get("null_hash_imports", 0),
            content_hash_backfill_stats.get("total_imports", 0),
            float(content_hash_backfill_stats.get("null_ratio", 0.0)) * 100.0,
        )
    await _resume_queued_imports()
    await prewarm_chromadb()
    embedding_migration_stats = await run_embedding_migration_if_needed(threshold=400)
    if embedding_migration_stats.get("skipped"):
        logger.info(
            "Embedding migration skipped: compliance_docs count={} threshold met",
            embedding_migration_stats.get("current_count", 0),
        )
    else:
        logger.info(
            "Embedding migration complete: checked={}, reembedded={}, missing_text={}, errors={}, final_count={}",
            embedding_migration_stats.get("checked_imports", 0),
            embedding_migration_stats.get("reembedded", 0),
            embedding_migration_stats.get("missing_text", 0),
            embedding_migration_stats.get("errors", 0),
            embedding_migration_stats.get("final_count", 0),
        )
    _ensure_minio_bucket()
    async with AsyncSessionLocal() as session:
        await seed_audit_date_settings(session)
        await seed_api_usage_settings(session)
    async with AsyncSessionLocal() as session:
        worker_setting = (
            await session.execute(
                select(AppSetting).where(AppSetting.key == "background_jobs_enabled")
            )
        ).scalars().first()
    worker_enabled = (worker_setting.value if worker_setting else "true").lower() == "true"
    if worker_enabled and (_background_worker_task is None or _background_worker_task.done()):
        _background_worker_task = asyncio.create_task(run_background_job_worker())
    else:
        logger.info("Background job worker startup skipped (background_jobs_enabled=false)")
    if _evidence_watch_task is None or _evidence_watch_task.done():
        _evidence_watch_task = asyncio.create_task(run_evidence_watch_loop())
    logger.info("Compliance Platform API started")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global _background_worker_task, _evidence_watch_task
    for task_name, task in (
        ("background", _background_worker_task),
        ("evidence_watch", _evidence_watch_task),
    ):
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    _background_worker_task = None
    _evidence_watch_task = None


async def prewarm_chromadb() -> None:
    try:
        import chromadb

        client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        collection = client.get_or_create_collection("compliance_docs")
        logger.info(
            "ChromaDB pre-warmed: compliance_docs has {} documents",
            collection.count(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ChromaDB pre-warm failed (non-fatal): {}", exc)


async def _load_all_frameworks_on_startup() -> None:
    import time
    from pathlib import Path

    from services.framework_loader import load_framework

    frameworks_dir = Path(__file__).parent / "config" / "frameworks"
    yaml_files = sorted(frameworks_dir.glob("*.yaml"))
    if not yaml_files:
        logger.warning("No framework YAML files found in {}", frameworks_dir)
        return
    totals = {
        "frameworks_loaded": 0,
        "controls_created": 0,
        "controls_updated": 0,
        "mappings_created": 0,
    }
    started = time.perf_counter()
    async with AsyncSessionLocal() as session:
        for yaml_file in yaml_files:
            try:
                summary = await load_framework(yaml_file, session)
                totals["frameworks_loaded"] += int(summary.get("frameworks_loaded", 0))
                totals["controls_created"] += int(summary.get("controls_created", 0))
                totals["controls_updated"] += int(summary.get("controls_updated", 0))
                totals["mappings_created"] += int(summary.get("mappings_created", 0))
                logger.info("Loaded framework YAML {}: {}", yaml_file.name, summary)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to load framework {}: {}", yaml_file.name, exc)
    elapsed_s = time.perf_counter() - started
    logger.info(
        "Framework startup load complete: files={} frameworks={} created={} updated={} mappings={} elapsed_seconds={:.3f}",
        len(yaml_files),
        totals["frameworks_loaded"],
        totals["controls_created"],
        totals["controls_updated"],
        totals["mappings_created"],
        elapsed_s,
    )


async def _ensure_auditor_schema_columns() -> None:
    statements = [
        "ALTER TABLE IF EXISTS data_imports ADD COLUMN IF NOT EXISTS batch_id VARCHAR(64)",
        "ALTER TABLE IF EXISTS data_imports ADD COLUMN IF NOT EXISTS auditor_engagement_name VARCHAR(255)",
        "ALTER TABLE IF EXISTS data_imports ADD COLUMN IF NOT EXISTS auditor_engagement_type VARCHAR(120)",
        "ALTER TABLE IF EXISTS data_imports ADD COLUMN IF NOT EXISTS auditor_certification_body VARCHAR(120)",
        "ALTER TABLE IF EXISTS data_imports ADD COLUMN IF NOT EXISTS auditor_period_year VARCHAR(4)",
        "ALTER TABLE IF EXISTS data_imports ADD COLUMN IF NOT EXISTS auditor_merge_with_existing BOOLEAN",
        "ALTER TABLE IF EXISTS data_imports ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE IF EXISTS data_imports ADD COLUMN IF NOT EXISTS library VARCHAR(16) NOT NULL DEFAULT 'main'",
        "ALTER TABLE IF EXISTS data_imports ADD COLUMN IF NOT EXISTS file_size INTEGER",
        "ALTER TABLE IF EXISTS data_imports ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64)",
        "ALTER TABLE IF EXISTS data_imports ADD COLUMN IF NOT EXISTS duplicate_status VARCHAR(32) NOT NULL DEFAULT 'unique'",
        "ALTER TABLE IF EXISTS data_imports ADD COLUMN IF NOT EXISTS duplicate_of_id INTEGER REFERENCES data_imports(id)",
        "ALTER TABLE IF EXISTS data_imports ADD COLUMN IF NOT EXISTS duplicate_confidence VARCHAR(16)",
        "ALTER TABLE IF EXISTS data_imports ADD COLUMN IF NOT EXISTS duplicate_reason TEXT",
        "ALTER TABLE IF EXISTS data_imports ADD COLUMN IF NOT EXISTS duplicate_flag_dismissed BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE IF EXISTS auditor_checklists ADD COLUMN IF NOT EXISTS audit_type VARCHAR(120)",
        "ALTER TABLE IF EXISTS auditor_checklists ADD COLUMN IF NOT EXISTS audit_period_year VARCHAR(4)",
        "ALTER TABLE IF EXISTS auditor_checklists ADD COLUMN IF NOT EXISTS fields_found JSON",
        "ALTER TABLE IF EXISTS auditor_checklists ADD COLUMN IF NOT EXISTS last_evidence_refresh VARCHAR(32)",
        "ALTER TABLE IF EXISTS auditor_checklists ADD COLUMN IF NOT EXISTS evidence_refresh_status VARCHAR(24)",
        "ALTER TABLE IF EXISTS auditor_checklists ADD COLUMN IF NOT EXISTS evidence_refresh_error TEXT",
        "ALTER TABLE IF EXISTS auditor_checklists ADD COLUMN IF NOT EXISTS source_import_id INTEGER REFERENCES data_imports(id)",
        "ALTER TABLE IF EXISTS auditor_checklist_items ADD COLUMN IF NOT EXISTS source_import_id INTEGER",
        "ALTER TABLE IF EXISTS auditor_checklist_items ADD COLUMN IF NOT EXISTS raw_fields JSON",
        "ALTER TABLE IF EXISTS auditor_checklist_items ADD COLUMN IF NOT EXISTS evidence_mapping JSON",
        # Column may already exist without FK from older ensures — attach FK + index idempotently.
        (
            "DO $$ BEGIN "
            "ALTER TABLE auditor_checklist_items "
            "ADD CONSTRAINT auditor_checklist_items_source_import_id_fkey "
            "FOREIGN KEY (source_import_id) REFERENCES data_imports(id); "
            "EXCEPTION WHEN duplicate_object THEN NULL; "
            "WHEN undefined_table THEN NULL; "
            "END $$"
        ),
        "CREATE INDEX IF NOT EXISTS ix_auditor_checklist_items_source_import_id ON auditor_checklist_items (source_import_id)",
        "CREATE INDEX IF NOT EXISTS ix_auditor_checklists_source_import_id ON auditor_checklists (source_import_id)",
        "ALTER TABLE IF EXISTS batch_imports ADD COLUMN IF NOT EXISTS skipped_files JSON",
        "ALTER TABLE IF EXISTS evidence_items ADD COLUMN IF NOT EXISTS analysis_confidence VARCHAR(16)",
        "ALTER TABLE IF EXISTS evidence_items ADD COLUMN IF NOT EXISTS analysis_summary TEXT",
        "ALTER TABLE IF EXISTS evidence_items ADD COLUMN IF NOT EXISTS library VARCHAR(16) NOT NULL DEFAULT 'main'",
        "ALTER TABLE IF EXISTS evidence_items ADD COLUMN IF NOT EXISTS display_name VARCHAR(500)",
        "ALTER TABLE IF EXISTS evidence_control ADD COLUMN IF NOT EXISTS display_name VARCHAR(500)",
        (
            "CREATE TABLE IF NOT EXISTS evidence_corrections ("
            "id SERIAL PRIMARY KEY,"
            "timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            "evidence_id INTEGER REFERENCES evidence_items(id) ON DELETE SET NULL,"
            "control_id INTEGER REFERENCES controls(id),"
            "field_name VARCHAR(64) NOT NULL,"
            "before_value TEXT,"
            "after_value TEXT,"
            "source VARCHAR(64) NOT NULL DEFAULT 'api',"
            "operator VARCHAR(100) NOT NULL DEFAULT 'Michael DuPlantis',"
            "detail TEXT"
            ")"
        ),
        "CREATE INDEX IF NOT EXISTS ix_evidence_corrections_evidence_id ON evidence_corrections (evidence_id)",
        "CREATE INDEX IF NOT EXISTS ix_evidence_corrections_control_id ON evidence_corrections (control_id)",
        "ALTER TABLE IF EXISTS evidence_corrections ALTER COLUMN evidence_id DROP NOT NULL",
        "ALTER TABLE IF EXISTS controls ADD COLUMN IF NOT EXISTS description TEXT",
        "ALTER TABLE IF EXISTS controls ADD COLUMN IF NOT EXISTS implementation_guidance TEXT",
        (
            "CREATE TABLE IF NOT EXISTS background_jobs ("
            "id SERIAL PRIMARY KEY,"
            "job_type VARCHAR(80) NOT NULL,"
            "status VARCHAR(24) NOT NULL DEFAULT 'queued',"
            "payload JSON,"
            "result JSON,"
            "error_message TEXT,"
            "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            "started_at TIMESTAMPTZ NULL,"
            "finished_at TIMESTAMPTZ NULL"
            ")"
        ),
        "CREATE INDEX IF NOT EXISTS ix_background_jobs_job_type ON background_jobs (job_type)",
        "CREATE INDEX IF NOT EXISTS ix_background_jobs_status ON background_jobs (status)",
        (
            "CREATE TABLE IF NOT EXISTS change_log ("
            "id SERIAL PRIMARY KEY,"
            "timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
            "category VARCHAR(32) NOT NULL,"
            "action VARCHAR(64) NOT NULL,"
            "subject VARCHAR(255),"
            "detail TEXT,"
            "triggered_by VARCHAR(64) NOT NULL DEFAULT 'system'"
            ")"
        ),
        "CREATE INDEX IF NOT EXISTS ix_change_log_category ON change_log (category)",
        "CREATE INDEX IF NOT EXISTS ix_data_imports_batch_id ON data_imports (batch_id)",
    ]
    async with AsyncSessionLocal() as session:
        for statement in statements:
            await session.execute(text(statement))
        await session.commit()


async def _cleanup_stale_imports_on_startup() -> None:
    now = datetime.now(timezone.utc)
    stale_processing_cutoff = now - timedelta(minutes=30)
    batch_timeout_cutoff = now - timedelta(hours=4)

    async with AsyncSessionLocal() as session:
        stale_processing_records = list(
            (
                await session.execute(
                    select(DataImport).where(
                        DataImport.status == ImportStatus.PROCESSING,
                        DataImport.updated_at < stale_processing_cutoff,
                    )
                )
            ).scalars()
        )
        for record in stale_processing_records:
            record.status = ImportStatus.FAILED
            record.error_message = "Stale — server restarted mid-processing"
            record.updated_at = now

        stale_queued_records = list(
            (
                await session.execute(
                    select(DataImport).where(
                        DataImport.status == ImportStatus.QUEUED,
                        DataImport.updated_at < batch_timeout_cutoff,
                    )
                )
            ).scalars()
        )
        for record in stale_queued_records:
            record.status = ImportStatus.FAILED
            record.error_message = "Batch timeout"
            record.updated_at = now

        timed_out_batches = list(
            (
                await session.execute(
                    select(BatchImport).where(BatchImport.created_at < batch_timeout_cutoff)
                )
            ).scalars()
        )
        for batch in timed_out_batches:
            unresolved_records = list(
                (
                    await session.execute(
                        select(DataImport).where(
                            DataImport.batch_id == batch.batch_id,
                            DataImport.status.in_([ImportStatus.QUEUED, ImportStatus.PROCESSING]),
                            DataImport.updated_at < batch_timeout_cutoff,
                        )
                    )
                ).scalars()
            )
            for record in unresolved_records:
                record.status = ImportStatus.FAILED
                record.error_message = "Batch timeout"
                record.updated_at = now

        await session.commit()
        logger.info(
            "Startup import cleanup complete: stale_processing={}, stale_queued={}, batch_timeout_batches={}",
            len(stale_processing_records),
            len(stale_queued_records),
            len(timed_out_batches),
        )


async def _resume_queued_imports() -> None:
    now = datetime.now(timezone.utc)
    resume_cutoff = now - timedelta(hours=4)
    async with AsyncSessionLocal() as session:
        queued_records = list(
            (
                await session.execute(
                    select(DataImport).where(
                        DataImport.status == ImportStatus.QUEUED,
                        DataImport.updated_at >= resume_cutoff,
                    )
                )
            ).scalars()
        )
    if not queued_records:
        logger.info("Startup queued import resume: nothing to resume")
        return

    by_batch: dict[str, list[int]] = {}
    unbatched: list[int] = []
    for record in queued_records:
        if record.batch_id:
            by_batch.setdefault(record.batch_id, []).append(record.id)
        else:
            unbatched.append(record.id)

    # Sequential resume only. Folder Sync writes unbatched QUEUED rows; the previous
    # one-create_task-per-import path launched N concurrent process_import coroutines
    # (observed N=161), each holding an AsyncSession through Claude/MinIO work, and
    # exhausted the default pool (pool_size=5, max_overflow=10).
    async def _resume_all_sequentially() -> None:
        for batch_id, import_ids in by_batch.items():
            await process_batch_import(batch_id=batch_id, import_ids=sorted(import_ids))
        if unbatched:
            await process_batch_import(
                batch_id="startup-resume-unbatched",
                import_ids=sorted(unbatched),
            )

    asyncio.create_task(_resume_all_sequentially())

    logger.info(
        "Startup queued import resume launched (sequential): queued_records={}, batches={}, unbatched={}",
        len(queued_records),
        len(by_batch),
        len(unbatched),
    )


def _ensure_minio_bucket() -> None:
    client = Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=False,
    )
    try:
        if not client.bucket_exists(settings.minio_bucket):
            client.make_bucket(settings.minio_bucket)
    except S3Error as exc:
        logger.error(f"MinIO bucket initialization failed: {exc}")
        raise


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "anthropic_configured": "yes" if bool(settings.anthropic_api_key) else "no",
    }


app.include_router(frameworks.router)
app.include_router(controls.router)
app.include_router(evidence.router)
app.include_router(findings.router)
app.include_router(history.router)
app.include_router(obligations.router)
app.include_router(personnel.router)
app.include_router(workforce.router)
app.include_router(documents.router)
app.include_router(reports.router)
app.include_router(ingest.router)
app.include_router(chat.router)
app.include_router(dashboard.router)
app.include_router(import_router.router)
app.include_router(settings_router.router)
app.include_router(auditor_router.router, prefix="/auditor")

