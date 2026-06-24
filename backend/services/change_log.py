from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.compliance import ChangeLog


async def log_change(
    session: AsyncSession,
    *,
    category: str,
    action: str,
    subject: str | None = None,
    detail: str | None = None,
    triggered_by: str = "system",
) -> ChangeLog:
    entry = ChangeLog(
        category=category,
        action=action,
        subject=subject,
        detail=detail,
        triggered_by=triggered_by,
    )
    session.add(entry)
    await session.flush()
    return entry


async def list_change_log(
    session: AsyncSession,
    *,
    category: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = 50,
) -> list[ChangeLog]:
    stmt = select(ChangeLog)
    if category:
        stmt = stmt.where(ChangeLog.category == category)
    if start_date:
        stmt = stmt.where(ChangeLog.timestamp >= start_date)
    if end_date:
        stmt = stmt.where(ChangeLog.timestamp <= end_date)
    stmt = stmt.order_by(desc(ChangeLog.timestamp)).limit(limit)
    return list((await session.execute(stmt)).scalars())
