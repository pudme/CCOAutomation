from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai.gateway import estimate_batch_cost, get_usage_today
from database import get_db
from framework_constants import ATO_FRAMEWORKS as _ATO
from framework_constants import CMMC_FRAMEWORKS as _CMMC
from framework_constants import DPA_FRAMEWORKS as _DPA
from framework_constants import ISO_FRAMEWORKS as _ISO
from models.compliance import AppSetting
from services.change_log import log_change

router = APIRouter(prefix="/settings", tags=["settings"])

AUDIT_DATE_ISO_KEY = "audit_date_iso"
AUDIT_DATE_CMMC_KEY = "audit_date_cmmc"
AUDIT_DATE_DPA_KEY = "audit_date_dpa"
AUDIT_DATE_ATO_KEY = "audit_date_ato"
AUDIT_ENABLED_ISO_KEY = "audit_enabled_iso"
AUDIT_ENABLED_CMMC_KEY = "audit_enabled_cmmc"
AUDIT_ENABLED_DPA_KEY = "audit_enabled_dpa"
AUDIT_ENABLED_ATO_KEY = "audit_enabled_ato"
DEFAULT_AUDIT_DATE_ISO = "2026-05-15"
DEFAULT_AUDIT_DATE_CMMC = "2026-09-01"
DEFAULT_API_DAILY_LIMIT = "200"
DEFAULT_API_CALLS_ENABLED = "true"

ISO_FRAMEWORKS = sorted(_ISO)
CMMC_FRAMEWORKS = sorted(name for name in _CMMC if name != "cmmc")
DPA_FRAMEWORKS = sorted(_DPA)
ATO_FRAMEWORKS = sorted(_ATO)

AUDIT_KEYS = ("iso", "cmmc", "dpa", "ato")
AUDIT_ENABLED_KEYS = {
    "iso": AUDIT_ENABLED_ISO_KEY,
    "cmmc": AUDIT_ENABLED_CMMC_KEY,
    "dpa": AUDIT_ENABLED_DPA_KEY,
    "ato": AUDIT_ENABLED_ATO_KEY,
}
AUDIT_LABELS = {
    "iso": "ISO Surveillance Audit",
    "cmmc": "CMMC Level 2 Assessment",
    "dpa": "DPA Follow-up Review",
    "ato": "ATO Readiness",
}


def _today() -> date:
    return datetime.utcnow().date()


def _parse_bool_setting(value: str | None, *, default: bool = True) -> bool:
    if value is None or not str(value).strip():
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _build_audit_entry(
    audit_date: str | None,
    label: str,
    frameworks: list[str],
    *,
    enabled: bool = True,
) -> dict[str, str | int | list[str] | bool | None]:
    if not audit_date:
        return {
            "audit_date": None,
            "days_remaining": None,
            "label": label,
            "frameworks": frameworks,
            "enabled": enabled,
        }
    parsed = datetime.strptime(audit_date, "%Y-%m-%d").date()
    return {
        "audit_date": audit_date,
        "days_remaining": (parsed - _today()).days,
        "label": label,
        "frameworks": frameworks,
        "enabled": enabled,
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


async def _resolve_audit_enabled(session: AsyncSession) -> dict[str, bool]:
    enabled: dict[str, bool] = {}
    for key, setting_key in AUDIT_ENABLED_KEYS.items():
        row = (
            await session.execute(select(AppSetting).where(AppSetting.key == setting_key))
        ).scalar_one_or_none()
        enabled[key] = _parse_bool_setting(row.value if row else None, default=True)
    return enabled


async def _build_audit_info_payload(session: AsyncSession) -> dict:
    iso_date, cmmc_date, dpa_date, ato_date = await _resolve_audit_dates(session)
    enabled = await _resolve_audit_enabled(session)
    return {
        "iso": _build_audit_entry(
            iso_date, AUDIT_LABELS["iso"], ISO_FRAMEWORKS, enabled=enabled["iso"]
        ),
        "cmmc": _build_audit_entry(
            cmmc_date, AUDIT_LABELS["cmmc"], CMMC_FRAMEWORKS, enabled=enabled["cmmc"]
        ),
        "dpa": _build_audit_entry(
            dpa_date, AUDIT_LABELS["dpa"], DPA_FRAMEWORKS, enabled=enabled["dpa"]
        ),
        "ato": _build_audit_entry(
            ato_date, AUDIT_LABELS["ato"], ATO_FRAMEWORKS, enabled=enabled["ato"]
        ),
    }


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
    for setting_key in AUDIT_ENABLED_KEYS.values():
        existing_enabled = (
            await session.execute(select(AppSetting).where(AppSetting.key == setting_key))
        ).scalar_one_or_none()
        if existing_enabled is None:
            session.add(AppSetting(key=setting_key, value="true", updated_at=now))
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
    return await _build_audit_info_payload(session)


class AuditDatesPatch(BaseModel):
    iso_audit_date: str
    cmmc_audit_date: str
    dpa_audit_date: str | None = None
    ato_audit_date: str | None = None


class AuditEnabledPatch(BaseModel):
    audit: str
    enabled: bool


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
    return await _build_audit_info_payload(session)


@router.patch("/audit-enabled")
async def patch_audit_enabled(
    payload: AuditEnabledPatch,
    session: AsyncSession = Depends(get_db),
) -> dict:
    audit_key = (payload.audit or "").strip().lower()
    if audit_key not in AUDIT_ENABLED_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"audit must be one of: {', '.join(AUDIT_KEYS)}",
        )
    setting_key = AUDIT_ENABLED_KEYS[audit_key]
    now = datetime.utcnow().isoformat()
    value = "true" if payload.enabled else "false"
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == setting_key))
    ).scalar_one_or_none()
    if row is None:
        row = AppSetting(key=setting_key, value=value, updated_at=now)
        session.add(row)
    else:
        row.value = value
        row.updated_at = now
    await session.commit()
    await log_change(
        session,
        category="settings",
        action="Setting updated",
        subject=setting_key,
        detail=f"Setting {setting_key} updated to {value}",
    )
    await session.commit()
    return await _build_audit_info_payload(session)


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
