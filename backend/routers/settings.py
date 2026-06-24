from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai.gateway import estimate_batch_cost, get_usage_today
from database import get_db
from models.compliance import AppSetting
from services.change_log import log_change

router = APIRouter(prefix="/settings", tags=["settings"])

AUDIT_DATE_ISO_KEY = "audit_date_iso"
AUDIT_DATE_CMMC_KEY = "audit_date_cmmc"
AUDIT_DATE_DPA_KEY = "audit_date_dpa"
AUDIT_DATE_ATO_KEY = "audit_date_ato"
DEFAULT_AUDIT_DATE_ISO = "2026-05-15"
DEFAULT_AUDIT_DATE_CMMC = "2026-09-01"
DEFAULT_API_DAILY_LIMIT = "200"
DEFAULT_API_CALLS_ENABLED = "true"

ISO_FRAMEWORKS = ["iso27001", "iso20000", "iso9001"]
CMMC_FRAMEWORKS = ["cmmc_l2"]
DPA_FRAMEWORKS = ["dpa_attachment_c"]
ATO_FRAMEWORKS = ["nist_800_53"]


def _today() -> date:
    return datetime.utcnow().date()


def _build_audit_entry(
    audit_date: str | None,
    label: str,
    frameworks: list[str],
) -> dict[str, str | int | list[str] | None]:
    if not audit_date:
        return {
            "audit_date": None,
            "days_remaining": None,
            "label": label,
            "frameworks": frameworks,
        }
    parsed = datetime.strptime(audit_date, "%Y-%m-%d").date()
    return {
        "audit_date": audit_date,
        "days_remaining": (parsed - _today()).days,
        "label": label,
        "frameworks": frameworks,
    }


async def _resolve_audit_dates(session: AsyncSession) -> tuple[str, str, str | None, str | None]:
    iso_setting = (
        await session.execute(select(AppSetting).where(AppSetting.key == AUDIT_DATE_ISO_KEY))
    ).scalar_one_or_none()
    cmmc_setting = (
        await session.execute(select(AppSetting).where(AppSetting.key == AUDIT_DATE_CMMC_KEY))
    ).scalar_one_or_none()
    dpa_setting = (
        await session.execute(select(AppSetting).where(AppSetting.key == AUDIT_DATE_DPA_KEY))
    ).scalar_one_or_none()
    ato_setting = (
        await session.execute(select(AppSetting).where(AppSetting.key == AUDIT_DATE_ATO_KEY))
    ).scalar_one_or_none()
    iso_date = iso_setting.value if iso_setting and iso_setting.value else DEFAULT_AUDIT_DATE_ISO
    cmmc_date = cmmc_setting.value if cmmc_setting and cmmc_setting.value else DEFAULT_AUDIT_DATE_CMMC
    dpa_date = dpa_setting.value.strip() if dpa_setting and dpa_setting.value else None
    ato_date = ato_setting.value.strip() if ato_setting and ato_setting.value else None
    return iso_date, cmmc_date, dpa_date, ato_date


async def seed_audit_date_settings(session: AsyncSession) -> None:
    now = datetime.utcnow().isoformat()
    existing_iso = (
        await session.execute(select(AppSetting).where(AppSetting.key == AUDIT_DATE_ISO_KEY))
    ).scalar_one_or_none()
    existing_cmmc = (
        await session.execute(select(AppSetting).where(AppSetting.key == AUDIT_DATE_CMMC_KEY))
    ).scalar_one_or_none()
    existing_dpa = (
        await session.execute(select(AppSetting).where(AppSetting.key == AUDIT_DATE_DPA_KEY))
    ).scalar_one_or_none()
    existing_ato = (
        await session.execute(select(AppSetting).where(AppSetting.key == AUDIT_DATE_ATO_KEY))
    ).scalar_one_or_none()
    legacy = (
        await session.execute(select(AppSetting).where(AppSetting.key == "audit_date"))
    ).scalar_one_or_none()

    if existing_iso is None:
        existing_iso = AppSetting(key=AUDIT_DATE_ISO_KEY, value=DEFAULT_AUDIT_DATE_ISO, updated_at=now)
        session.add(existing_iso)
    if existing_cmmc is None:
        existing_cmmc = AppSetting(key=AUDIT_DATE_CMMC_KEY, value=DEFAULT_AUDIT_DATE_CMMC, updated_at=now)
        session.add(existing_cmmc)
    if existing_dpa is None:
        existing_dpa = AppSetting(key=AUDIT_DATE_DPA_KEY, value="", updated_at=now)
        session.add(existing_dpa)
    if existing_ato is None:
        existing_ato = AppSetting(key=AUDIT_DATE_ATO_KEY, value="", updated_at=now)
        session.add(existing_ato)
    if legacy is not None:
        await session.delete(legacy)

    await session.commit()


async def seed_api_usage_settings(session: AsyncSession) -> None:
    now = datetime.utcnow().isoformat()
    daily_limit = (
        await session.execute(select(AppSetting).where(AppSetting.key == "api_daily_limit"))
    ).scalar_one_or_none()
    calls_enabled = (
        await session.execute(select(AppSetting).where(AppSetting.key == "api_calls_enabled"))
    ).scalar_one_or_none()
    if daily_limit is None:
        session.add(
            AppSetting(
                key="api_daily_limit",
                value=DEFAULT_API_DAILY_LIMIT,
                updated_at=now,
            )
        )
    if calls_enabled is None:
        session.add(
            AppSetting(
                key="api_calls_enabled",
                value=DEFAULT_API_CALLS_ENABLED,
                updated_at=now,
            )
        )
    await session.commit()


@router.get("/audit-info")
async def get_audit_info(session: AsyncSession = Depends(get_db)) -> dict:
    iso_date, cmmc_date, dpa_date, ato_date = await _resolve_audit_dates(session)
    return {
        "iso": _build_audit_entry(iso_date, "ISO Surveillance Audit", ISO_FRAMEWORKS),
        "cmmc": _build_audit_entry(cmmc_date, "CMMC Level 2 Assessment", CMMC_FRAMEWORKS),
        "dpa": _build_audit_entry(dpa_date, "DPA Follow-up Review", DPA_FRAMEWORKS),
        "ato": _build_audit_entry(ato_date, "ATO Readiness", ATO_FRAMEWORKS),
    }


class AuditDatesPatch(BaseModel):
    iso_audit_date: str
    cmmc_audit_date: str
    dpa_audit_date: str | None = None
    ato_audit_date: str | None = None


class ApiLimitPatch(BaseModel):
    daily_limit: int


class ApiEnabledPatch(BaseModel):
    enabled: bool


class BatchCostEstimateBody(BaseModel):
    num_calls: int


@router.patch("/audit-dates")
async def patch_audit_dates(
    payload: AuditDatesPatch,
    session: AsyncSession = Depends(get_db),
) -> dict:
    try:
        datetime.strptime(payload.iso_audit_date, "%Y-%m-%d")
        datetime.strptime(payload.cmmc_audit_date, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="iso_audit_date and cmmc_audit_date must be YYYY-MM-DD",
        ) from exc
    if payload.dpa_audit_date:
        try:
            datetime.strptime(payload.dpa_audit_date, "%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="dpa_audit_date must be YYYY-MM-DD") from exc
    if payload.ato_audit_date:
        try:
            datetime.strptime(payload.ato_audit_date, "%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="ato_audit_date must be YYYY-MM-DD") from exc
    now = datetime.utcnow().isoformat()
    existing_iso = (
        await session.execute(select(AppSetting).where(AppSetting.key == AUDIT_DATE_ISO_KEY))
    ).scalar_one_or_none()
    existing_cmmc = (
        await session.execute(select(AppSetting).where(AppSetting.key == AUDIT_DATE_CMMC_KEY))
    ).scalar_one_or_none()
    existing_dpa = (
        await session.execute(select(AppSetting).where(AppSetting.key == AUDIT_DATE_DPA_KEY))
    ).scalar_one_or_none()
    existing_ato = (
        await session.execute(select(AppSetting).where(AppSetting.key == AUDIT_DATE_ATO_KEY))
    ).scalar_one_or_none()
    if existing_iso is None:
        existing_iso = AppSetting(key=AUDIT_DATE_ISO_KEY, value=payload.iso_audit_date, updated_at=now)
        session.add(existing_iso)
    else:
        existing_iso.value = payload.iso_audit_date
        existing_iso.updated_at = now
    if existing_cmmc is None:
        existing_cmmc = AppSetting(key=AUDIT_DATE_CMMC_KEY, value=payload.cmmc_audit_date, updated_at=now)
        session.add(existing_cmmc)
    else:
        existing_cmmc.value = payload.cmmc_audit_date
        existing_cmmc.updated_at = now
    dpa_value = (payload.dpa_audit_date or "").strip()
    if existing_dpa is None:
        existing_dpa = AppSetting(key=AUDIT_DATE_DPA_KEY, value=dpa_value, updated_at=now)
        session.add(existing_dpa)
    else:
        existing_dpa.value = dpa_value
        existing_dpa.updated_at = now
    ato_value = (payload.ato_audit_date or "").strip()
    if existing_ato is None:
        existing_ato = AppSetting(key=AUDIT_DATE_ATO_KEY, value=ato_value, updated_at=now)
        session.add(existing_ato)
    else:
        existing_ato.value = ato_value
        existing_ato.updated_at = now

    legacy = (
        await session.execute(select(AppSetting).where(AppSetting.key == "audit_date"))
    ).scalar_one_or_none()
    if legacy is not None:
        await session.delete(legacy)

    await session.commit()
    await log_change(
        session,
        category="settings",
        action="Setting updated",
        subject="audit_dates",
        detail=(
            f"Setting audit dates updated: ISO={payload.iso_audit_date}, "
            f"CMMC={payload.cmmc_audit_date}, DPA={dpa_value or 'unset'}, ATO={ato_value or 'unset'}"
        ),
    )
    await session.commit()
    return {
        "iso": _build_audit_entry(existing_iso.value, "ISO Surveillance Audit", ISO_FRAMEWORKS),
        "cmmc": _build_audit_entry(existing_cmmc.value, "CMMC Level 2 Assessment", CMMC_FRAMEWORKS),
        "dpa": _build_audit_entry(existing_dpa.value if existing_dpa else None, "DPA Follow-up Review", DPA_FRAMEWORKS),
        "ato": _build_audit_entry(existing_ato.value if existing_ato else None, "ATO Readiness", ATO_FRAMEWORKS),
    }


@router.get("/all")
async def get_all_settings(session: AsyncSession = Depends(get_db)) -> dict[str, str]:
    settings_rows = list((await session.execute(select(AppSetting))).scalars())
    return {row.key: row.value for row in settings_rows}


@router.get("/api-usage")
async def get_api_usage() -> dict:
    return await get_usage_today()


@router.patch("/api-limit")
async def patch_api_limit(
    payload: ApiLimitPatch,
    session: AsyncSession = Depends(get_db),
) -> dict:
    if payload.daily_limit < 1:
        raise HTTPException(status_code=400, detail="daily_limit must be >= 1")
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == "api_daily_limit"))
    ).scalar_one_or_none()
    now = datetime.utcnow().isoformat()
    if row is None:
        row = AppSetting(key="api_daily_limit", value=str(payload.daily_limit), updated_at=now)
        session.add(row)
    else:
        row.value = str(payload.daily_limit)
        row.updated_at = now
    await session.commit()
    await log_change(
        session,
        category="settings",
        action="Setting updated",
        subject="api_daily_limit",
        detail=f"Setting api_daily_limit updated to {payload.daily_limit}",
    )
    await session.commit()
    return await get_usage_today()


@router.patch("/api-enabled")
async def patch_api_enabled(
    payload: ApiEnabledPatch,
    session: AsyncSession = Depends(get_db),
) -> dict:
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == "api_calls_enabled"))
    ).scalar_one_or_none()
    now = datetime.utcnow().isoformat()
    value = "true" if payload.enabled else "false"
    if row is None:
        row = AppSetting(key="api_calls_enabled", value=value, updated_at=now)
        session.add(row)
    else:
        row.value = value
        row.updated_at = now
    await session.commit()
    await log_change(
        session,
        category="settings",
        action="Setting updated",
        subject="api_calls_enabled",
        detail=f"Setting api_calls_enabled updated to {value}",
    )
    await session.commit()
    return await get_usage_today()


@router.post("/estimate-batch-cost")
async def post_estimate_batch_cost(payload: BatchCostEstimateBody) -> dict:
    if payload.num_calls < 1:
        raise HTTPException(status_code=400, detail="num_calls must be >= 1")
    return await estimate_batch_cost(payload.num_calls)
