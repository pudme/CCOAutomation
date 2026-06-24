from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.compliance import (
    AgentActionLog,
    Finding,
    FindingStatus,
    Framework,
    Obligation,
)
from services.gap_scanner import scan_framework_gaps
from services.personnel_checker import run_personnel_check
from services.readiness_mode import get_framework_readiness_with_mode

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
async def get_dashboard_summary(session: AsyncSession = Depends(get_db)) -> dict:
    frameworks = list(
        (
            await session.execute(
                select(Framework)
                .where(Framework.short_name != "obligations")
                .order_by(Framework.short_name.asc())
            )
        ).scalars()
    )
    readiness = []
    for framework in frameworks:
        readiness.append(await get_framework_readiness_with_mode(session, framework))
    gap_scan = await scan_framework_gaps(session)

    findings_result = await session.execute(
        select(Finding.severity, func.count(Finding.id))
        .where(Finding.status.not_in([FindingStatus.CLOSED, FindingStatus.VERIFIED]))
        .group_by(Finding.severity)
    )
    findings_by_severity = {severity.value: count for severity, count in findings_result.all()}

    obligations_result = await session.execute(select(Obligation))
    obligations_due = []
    threshold = date.today() + timedelta(days=30)
    for obligation in obligations_result.scalars():
        status_value = str(getattr(obligation.status, "value", obligation.status) or "").strip().lower()
        if status_value in {"satisfied", "completed"}:
            continue
        if obligation.due_date:
            try:
                due = datetime.strptime(obligation.due_date, "%Y-%m-%d").date()
                if due <= threshold:
                    obligations_due.append(
                        {
                            "obligation_id": obligation.obligation_id,
                            "source": obligation.source,
                            "due_date": obligation.due_date,
                            "status": obligation.status.value,
                        }
                    )
            except ValueError:
                continue

    actions_result = await session.execute(select(AgentActionLog).order_by(AgentActionLog.timestamp.desc()).limit(10))
    recent_actions = [
        {
            "timestamp": action.timestamp.isoformat(),
            "tool_name": action.tool_name,
            "result_summary": action.result_summary,
            "operator": action.operator,
        }
        for action in actions_result.scalars()
    ]

    personnel_report = await run_personnel_check(session)
    personnel_exceptions = {
        "training_gaps": personnel_report.summary.training_gap_count,
        "mfa_gaps": personnel_report.summary.mfa_gap_count,
        "nda_gaps": personnel_report.summary.nda_gap_count,
        "terminated_access_gaps": personnel_report.summary.access_revocation_count,
    }

    return {
        "framework_readiness": readiness,
        "framework_gaps": gap_scan,
        "open_findings_by_severity": findings_by_severity,
        "obligations_due_30_days": obligations_due,
        "recent_agent_actions": recent_actions,
        "personnel_exceptions": personnel_exceptions,
    }

