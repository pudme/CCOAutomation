from __future__ import annotations

import asyncio
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from services.doc_generator import (
    generate_audit_package_index,
    generate_corrective_action_report,
    generate_gap_report,
    generate_scorecard,
)

router = APIRouter(prefix="/reports", tags=["reports"])
settings = get_settings()


@router.get("")
async def list_reports() -> list[dict]:
    return [
        {"type": "gap", "endpoint": "/reports/gap?framework=iso27001"},
        {"type": "scorecard", "endpoint": "/reports/scorecard"},
        {"type": "corrective-actions", "endpoint": "/reports/corrective-actions"},
        {"type": "audit-index", "endpoint": "/reports/audit-package-index?framework=iso27001"},
    ]


@router.get("/gap")
async def run_gap_report(
    framework: str = Query(...),
    session: AsyncSession = Depends(get_db),
) -> dict:
    return await generate_gap_report(framework, session)


@router.get("/scorecard")
async def run_scorecard(session: AsyncSession = Depends(get_db)) -> dict:
    return await generate_scorecard(session)


@router.get("/corrective-actions")
async def run_corrective_actions(session: AsyncSession = Depends(get_db)) -> dict:
    return await generate_corrective_action_report(session)


@router.get("/audit-package-index")
async def run_audit_index(
    framework: str = Query(...),
    session: AsyncSession = Depends(get_db),
) -> dict:
    return await generate_audit_package_index(framework, session)


@router.get("/download/{filename}")
async def download_report(filename: str) -> StreamingResponse:
    from minio import Minio

    client = Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=False,
    )
    object_name = f"generated/{filename}"
    try:
        def _download() -> bytes:
            response = client.get_object(settings.minio_bucket, object_name)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        data = await asyncio.to_thread(_download)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"Report not found: {filename}") from exc
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

