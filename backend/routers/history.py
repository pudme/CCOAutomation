from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.change_log import list_change_log

router = APIRouter(prefix="/history", tags=["history"])


def _parse_date(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    parsed = datetime.strptime(value, "%Y-%m-%d")
    if end_of_day:
        parsed = parsed + timedelta(days=1) - timedelta(microseconds=1)
    return parsed


@router.get("")
async def get_history(
    category: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
) -> dict:
    rows = await list_change_log(
        session,
        category=category,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date, end_of_day=True),
        limit=limit,
    )
    return {
        "entries": [
            {
                "id": row.id,
                "timestamp": row.timestamp.isoformat(),
                "category": row.category,
                "action": row.action,
                "subject": row.subject,
                "detail": row.detail,
                "triggered_by": row.triggered_by,
            }
            for row in rows
        ],
        "limit": limit,
    }
