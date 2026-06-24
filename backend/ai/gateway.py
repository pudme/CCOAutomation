"""
Single gateway for all Anthropic API calls.
Enforces daily limits, tracks usage, and supports operator overrides.
Every Claude API call in the platform must go through call_claude().
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from anthropic import AsyncAnthropic
from loguru import logger
from sqlalchemy import select

from config import get_settings
from database import AsyncSessionLocal
from models.compliance import AppSetting

_settings = get_settings()
_client = AsyncAnthropic(api_key=_settings.anthropic_api_key)

MODEL_SONNET = "claude-sonnet-4-20250514"
MODEL_HAIKU = "claude-haiku-4-5-20251001"

# Approximate costs per call (USD)
COST_PER_CALL_SONNET = 0.003
COST_PER_CALL_HAIKU = 0.00015
COST_PER_CALL_USD = COST_PER_CALL_HAIKU


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


async def _get_setting(key: str, default: str = "") -> str:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AppSetting).where(AppSetting.key == key))
        setting = result.scalars().first()
        return setting.value if setting else default


async def _set_setting(key: str, value: str) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AppSetting).where(AppSetting.key == key))
        setting = result.scalars().first()
        if setting:
            setting.value = value
            setting.updated_at = _utc_now_iso()
        else:
            session.add(AppSetting(key=key, value=value, updated_at=_utc_now_iso()))
        await session.commit()


async def get_usage_today() -> dict[str, Any]:
    today = date.today().isoformat()
    count = int(await _get_setting(f"api_calls_{today}", "0"))
    limit = int(await _get_setting("api_daily_limit", "200"))
    enabled = (await _get_setting("api_calls_enabled", "true")) == "true"
    return {
        "today_count": count,
        "daily_limit": limit,
        "remaining": max(0, limit - count),
        "enabled": enabled,
        "reset_at": f"{today}T23:59:59Z",
        "estimated_cost_today": round(count * COST_PER_CALL_USD, 4),
    }


def is_daily_limit_exception(exc: Exception | str | None) -> bool:
    text = str(exc or "").lower()
    return "daily api limit" in text or "api limit of" in text


async def call_claude(
    messages: list[dict[str, Any]],
    system: str | None = None,
    max_tokens: int = 1000,
    model: str | None = None,
    bypass_limit: bool = False,
    tools: list[dict[str, Any]] | None = None,
) -> Any:
    """
    Single entry point for all Anthropic API calls.
    Enforces daily limits unless bypass_limit=True.
    Always increments the usage counter regardless of bypass.
    """
    resolved_model = model or MODEL_SONNET
    today = date.today().isoformat()
    count_key = f"api_calls_{today}"

    enabled = (await _get_setting("api_calls_enabled", "true")) == "true"
    if not enabled:
        raise Exception(
            "AI features are currently disabled. Enable in Settings -> API Usage Controls."
        )

    if not bypass_limit:
        current_count = int(await _get_setting(count_key, "0"))
        daily_limit = int(await _get_setting("api_daily_limit", "200"))
        if current_count >= daily_limit:
            raise Exception(
                f"Daily API limit of {daily_limit} calls reached. "
                "Reset tomorrow or increase limit in Settings -> API Usage Controls. "
                "To run a large batch operation now, use the 'Run Anyway' override."
            )

    current = int(await _get_setting(count_key, "0"))
    await _set_setting(count_key, str(current + 1))

    if bypass_limit:
        logger.warning("API limit bypassed by operator for call #{} today", current + 1)

    kwargs: dict[str, Any] = {
        "model": resolved_model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools

    logger.info(
        "Calling Claude model={} max_tokens={} tools={} bypass_limit={}",
        resolved_model,
        max_tokens,
        len(tools or []),
        bypass_limit,
    )
    return await _client.messages.create(**kwargs)


async def estimate_batch_cost(num_calls: int, model: str = MODEL_HAIKU) -> dict[str, Any]:
    usage = await get_usage_today()
    cost_per_call = COST_PER_CALL_HAIKU if model == MODEL_HAIKU else COST_PER_CALL_SONNET
    will_exceed = (usage["today_count"] + num_calls) > usage["daily_limit"]
    overage = max(0, (usage["today_count"] + num_calls) - usage["daily_limit"])
    return {
        "estimated_calls": num_calls,
        "estimated_cost_usd": round(num_calls * cost_per_call, 4),
        "current_today": usage["today_count"],
        "daily_limit": usage["daily_limit"],
        "remaining": usage["remaining"],
        "will_exceed_limit": will_exceed,
        "overage_calls": overage,
        "overage_cost_usd": round(overage * cost_per_call, 4),
    }
