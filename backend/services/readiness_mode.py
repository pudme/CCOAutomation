from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from framework_constants import CMMC_FRAMEWORKS, ISO_FRAMEWORKS
from models.auditor import AuditorChecklist, AuditorChecklistItem, AuditorChecklistStatus, AuditorItemStatus
from models.compliance import Control, ControlStatus, Framework

_STALE_REFRESH_WINDOW = timedelta(minutes=10)


def _is_iso_checklist(checklist: AuditorChecklist) -> bool:
    text = f"{checklist.audit_type or ''} {checklist.name or ''}".lower()
    return "iso" in text or "surveillance" in text


def _is_cmmc_checklist(checklist: AuditorChecklist) -> bool:
    text = f"{checklist.audit_type or ''} {checklist.name or ''}".lower()
    return "cmmc" in text


async def get_active_checklist_for_framework(
    session: AsyncSession,
    framework_short_name: str,
) -> AuditorChecklist | None:
    active = list(
        (
            await session.execute(
                select(AuditorChecklist).where(
                    AuditorChecklist.status == AuditorChecklistStatus.ACTIVE
                )
            )
        ).scalars()
    )
    key = framework_short_name.lower().strip()
    if key in ISO_FRAMEWORKS:
        for checklist in active:
            if _is_iso_checklist(checklist):
                return checklist
        return None
    if key in CMMC_FRAMEWORKS:
        for checklist in active:
            if _is_cmmc_checklist(checklist):
                return checklist
        return None
    return None


def _is_checklist_refresh_stale(checklist: AuditorChecklist) -> bool:
    if checklist.evidence_refresh_status != "in_progress":
        return False
    if not checklist.last_evidence_refresh:
        return True
    try:
        refresh_dt = datetime.fromisoformat(checklist.last_evidence_refresh)
    except ValueError:
        return True
    if refresh_dt.tzinfo is None:
        refresh_dt = refresh_dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - refresh_dt) > _STALE_REFRESH_WINDOW


async def get_framework_readiness_with_mode(
    session: AsyncSession,
    framework: Framework,
) -> dict:
    controls = list(
        (
            await session.execute(
                select(Control).where(Control.framework_id == framework.id)
            )
        ).scalars()
    )
    total_controls = len(controls)
    evidenced = len([control for control in controls if control.status == ControlStatus.EVIDENCED])
    framework_percentage = round((evidenced / total_controls) * 100, 2) if total_controls > 0 else 0

    checklist = await get_active_checklist_for_framework(session, framework.short_name)
    if checklist is not None and not _is_checklist_refresh_stale(checklist):
        items = list(
            (
                await session.execute(
                    select(AuditorChecklistItem).where(
                        AuditorChecklistItem.checklist_id == checklist.id
                    )
                )
            ).scalars()
        )
        total = len(items)
        satisfied = len(
            [
                item
                for item in items
                if item.status
                in {AuditorItemStatus.SATISFIED, AuditorItemStatus.EVIDENCE_SUBMITTED}
            ]
        )
        percentage = round((satisfied / total) * 100, 2) if total > 0 else 0
        # Auditor checklist rows can stay OPEN until reviewer action even when controls
        # are already evidenced; in that case show framework progress to avoid misleading 0%.
        if percentage == 0 and framework_percentage > 0:
            return {
                "framework": framework.short_name,
                "name": framework.name,
                "mode": "framework",
                "checklist_name": None,
                "checklist_id": None,
                "percentage": framework_percentage,
                "satisfied": 0,
                "total": 0,
                "evidenced_controls": evidenced,
                "total_controls": total_controls,
                "percent_evidenced": framework_percentage,
                "progress_label": f"{evidenced} / {total_controls} controls evidenced",
            }
        return {
            "framework": framework.short_name,
            "name": framework.name,
            "mode": "auditor",
            "checklist_name": checklist.name,
            "checklist_id": checklist.id,
            "percentage": percentage,
            "satisfied": satisfied,
            "total": total,
            "evidenced_controls": 0,
            "total_controls": 0,
            "percent_evidenced": percentage,
            "progress_label": f"{satisfied} / {total} auditor items satisfied",
        }

    return {
        "framework": framework.short_name,
        "name": framework.name,
        "mode": "framework",
        "checklist_name": None,
        "checklist_id": None,
        "percentage": framework_percentage,
        "satisfied": 0,
        "total": 0,
        "evidenced_controls": evidenced,
        "total_controls": total_controls,
        "percent_evidenced": framework_percentage,
        "progress_label": f"{evidenced} / {total_controls} controls evidenced",
    }
