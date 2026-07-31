from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.personnel_checker import run_personnel_check

router = APIRouter(prefix="/personnel", tags=["personnel"])


@router.get("")
async def list_personnel() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/compliance-report")
async def personnel_compliance_report(
    include_canaide: bool = Query(default=False),
    session: AsyncSession = Depends(get_db),
) -> dict:
    report = await run_personnel_check(session, include_canaide=include_canaide)
    return report.model_dump()

