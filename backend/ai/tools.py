from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

import chromadb
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from models.compliance import (
    AgentActionLog,
    Control,
    ControlStatus,
    CorrectiveAction,
    DataImport,
    EvidenceItem,
    EvidenceRequirement,
    EvidenceStatus,
    EvidenceType,
    Finding,
    FindingSeverity,
    FindingStatus,
    Framework,
    Obligation,
    ObligationStatus,
)
from services.personnel_checker import PersonnelComplianceReport, run_personnel_check
from services.doc_generator import (
    generate_audit_package_index as generate_audit_package_index_file,
    generate_corrective_action_report as generate_corrective_action_report_file,
    generate_gap_report as generate_gap_report_file,
    generate_scorecard as generate_scorecard_file,
)
from models.auditor import (
    AuditorChecklist,
    AuditorChecklistItem,
    AuditorChecklistStatus,
    AuditorItemStatus,
)

settings = get_settings()

_FRAMEWORK_NAME_OVERRIDES: dict[str, str] = {
    "iso27001": "ISO/IEC 27001:2022",
    "iso20000": "ISO/IEC 20000-1:2018",
    "iso9001": "ISO 9001:2015",
    "cmmc_l2": "CMMC Level 2",
}


def _to_plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _to_plain(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain(item) for item in value]
    return value


class DocumentChunk(BaseModel):
    chunk_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControlResponse(BaseModel):
    control_id: str
    framework: str
    title: str
    status: ControlStatus
    notes: str | None = None


class GapControl(BaseModel):
    control_id: str
    title: str
    gap_status: EvidenceStatus


class GapReport(BaseModel):
    framework: str
    controls: list[GapControl]


class FindingResponse(BaseModel):
    finding_id: str
    title: str
    severity: FindingSeverity
    status: FindingStatus


class ObligationResponse(BaseModel):
    obligation_id: str
    source: str
    due_date: str | None
    status: ObligationStatus


class IngestionResult(BaseModel):
    source_label: str
    summary: str
    extracted_actions: list[str] = Field(default_factory=list)


class ReportResult(BaseModel):
    name: str
    format: str
    summary: str


class AuditorChecklistItemResponse(BaseModel):
    id: int
    item_number: str
    description: str
    control_ids: list[str] = Field(default_factory=list)
    status: str
    our_response: str | None = None
    evidence_ids: list[int] = Field(default_factory=list)
    due_date: str | None = None
    auditor_notes: str | None = None
    priority: str


class AuditorChecklistResponse(BaseModel):
    id: int
    name: str
    audit_date: str | None = None
    auditor_name: str | None = None
    framework: str | None = None
    status: str
    source_import_id: int | None = None
    items: list[AuditorChecklistItemResponse] = Field(default_factory=list)


async def _log_write(
    session: AsyncSession,
    tool_name: str,
    parameters: dict[str, Any],
    result_summary: str,
    conversation_id: int | None = None,
    operator: str = "Michael DuPlantis",
) -> None:
    session.add(
        AgentActionLog(
            tool_name=tool_name,
            parameters=parameters,
            result_summary=result_summary,
            conversation_id=conversation_id,
            operator=operator,
        )
    )
    await session.flush()


async def search_documents(session: AsyncSession, query: str) -> list[dict[str, Any]]:
    if not query.strip():
        raise ValueError("query is required")
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    collection = client.get_or_create_collection("compliance_docs")
    result = collection.query(query_texts=[query], n_results=5)
    chunks: list[DocumentChunk] = []
    ids = result.get("ids", [[]])[0]
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    for idx, chunk_id in enumerate(ids):
        chunks.append(
            DocumentChunk(
                chunk_id=str(chunk_id),
                text=str(docs[idx]) if idx < len(docs) else "",
                metadata=metas[idx] if idx < len(metas) and metas[idx] else {},
            )
        )
    return _to_plain(chunks)


async def get_control(session: AsyncSession, control_id: str, framework: str) -> dict[str, Any]:
    if not control_id.strip() or not framework.strip():
        raise ValueError("control_id and framework are required")
    result = await session.execute(
        select(Control, Framework)
        .join(Framework, Control.framework_id == Framework.id)
        .where(Control.control_id == control_id, Framework.short_name == framework)
    )
    row = result.first()
    if row is None:
        raise ValueError("Control not found")
    control, framework_obj = row
    return _to_plain(
        ControlResponse(
        control_id=control.control_id,
        framework=framework_obj.short_name,
        title=control.title,
        status=control.status,
        notes=control.status_notes,
    )
    )


async def get_framework_gaps(session: AsyncSession, framework_short_name: str) -> dict[str, Any]:
    if not framework_short_name.strip():
        raise ValueError("framework_short_name is required")
    framework_rows = list(
        (
            await session.execute(
                select(Framework).where(Framework.short_name == framework_short_name)
            )
        ).scalars()
    )
    if not framework_rows:
        raise ValueError("Framework not found")
    framework_ids = [row.id for row in framework_rows]
    rows = (
        await session.execute(select(Control).where(Control.framework_id.in_(framework_ids)))
    ).scalars()
    dedup_controls: dict[str, Control] = {}
    for control in rows:
        key = str(control.control_id or "").strip()
        if not key:
            continue
        dedup_controls[key] = control
    controls: list[GapControl] = []
    for control in dedup_controls.values():
        evidence_result = await session.execute(select(EvidenceItem).join(EvidenceItem.controls).where(Control.id == control.id))
        evidence = list(evidence_result.scalars())
        status = EvidenceStatus.MISSING
        if any(item.status == EvidenceStatus.STALE for item in evidence):
            status = EvidenceStatus.STALE
        elif any(item.status == EvidenceStatus.CURRENT for item in evidence):
            required_result = await session.execute(
                select(EvidenceRequirement).where(EvidenceRequirement.control_id == control.id)
            )
            required = [req.evidence_type for req in required_result.scalars()]
            current_types = {item.evidence_type for item in evidence if item.status == EvidenceStatus.CURRENT}
            status = EvidenceStatus.CURRENT if set(required).issubset(current_types) else EvidenceStatus.PENDING
        controls.append(GapControl(control_id=control.control_id, title=control.title, gap_status=status))
    return _to_plain(GapReport(framework=framework_short_name, controls=controls))


async def get_open_findings(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(select(Finding).where(Finding.status != FindingStatus.CLOSED))
    return _to_plain([
        FindingResponse(
            finding_id=f.finding_id,
            title=f.title,
            severity=f.severity,
            status=f.status,
        )
        for f in result.scalars()
    ])


async def get_personnel_exceptions(session: AsyncSession) -> dict[str, Any]:
    return _to_plain(await run_personnel_check(session))


async def run_personnel_check_tool(session: AsyncSession) -> dict[str, Any]:
    return _to_plain(await run_personnel_check(session))


async def get_obligations_due(session: AsyncSession, days: int) -> list[dict[str, Any]]:
    if days < 0:
        raise ValueError("days must be >= 0")
    result = await session.execute(select(Obligation))
    obligations = []
    for obligation in result.scalars():
        obligations.append(
            ObligationResponse(
                obligation_id=obligation.obligation_id,
                source=obligation.source,
                due_date=obligation.due_date,
                status=obligation.status,
            )
        )
    return _to_plain(obligations)


async def list_obligations(session: AsyncSession, status: str | None = None) -> list[dict[str, Any]]:
    result = await session.execute(select(Obligation))
    obligations = []
    for obligation in result.scalars():
        if status and obligation.status.value != status:
            continue
        if "[SOFT_DELETED]" in (obligation.notes or ""):
            continue
        obligations.append(
            ObligationResponse(
                obligation_id=obligation.obligation_id,
                source=obligation.source,
                due_date=obligation.due_date,
                status=obligation.status,
            )
        )
    return _to_plain(obligations)


async def create_obligation(
    session: AsyncSession,
    source: str,
    description: str,
    owner: str | None,
    due_date: str | None,
    cadence: str | None,
    status: str = "current",
    conversation_id: int | None = None,
) -> dict[str, Any]:
    latest = (await session.execute(select(Obligation).order_by(Obligation.id.desc()))).scalars().first()
    next_num = (latest.id + 1) if latest else 1
    obligation = Obligation(
        obligation_id=f"OBL-{next_num:03d}",
        source=source,
        description=description,
        owner=owner,
        due_date=due_date,
        cadence=cadence,
        status=ObligationStatus(status),
    )
    session.add(obligation)
    await _log_write(
        session,
        "create_obligation",
        {
            "source": source,
            "description": description,
            "owner": owner,
            "due_date": due_date,
            "cadence": cadence,
            "status": status,
        },
        f"Created obligation {obligation.obligation_id}",
        conversation_id,
    )
    await session.commit()
    return _to_plain(ObligationResponse(
        obligation_id=obligation.obligation_id,
        source=obligation.source,
        due_date=obligation.due_date,
        status=obligation.status,
    ))


async def get_framework_detail(session: AsyncSession, framework: str) -> dict[str, Any]:
    framework_rows = list(
        (
            await session.execute(select(Framework).where(Framework.short_name == framework))
        ).scalars()
    )
    if not framework_rows:
        raise ValueError("Framework not found")
    framework_ids = [row.id for row in framework_rows]
    controls = list(
        (
            await session.execute(select(Control).where(Control.framework_id.in_(framework_ids)))
        ).scalars()
    )
    dedup_controls = {str(control.control_id or "").strip(): control for control in controls if str(control.control_id or "").strip()}
    framework_obj = framework_rows[0]
    display_name = _FRAMEWORK_NAME_OVERRIDES.get(framework_obj.short_name, framework_obj.name)
    return {
        "framework": framework_obj.short_name,
        "name": display_name,
        "controls": len(dedup_controls),
        "evidenced": len([c for c in dedup_controls.values() if c.status == ControlStatus.EVIDENCED]),
        "in_progress": len([c for c in dedup_controls.values() if c.status == ControlStatus.IN_PROGRESS]),
        "not_started": len([c for c in dedup_controls.values() if c.status == ControlStatus.NOT_STARTED]),
    }


async def get_import_history(session: AsyncSession, limit: int = 20) -> list[dict[str, Any]]:
    result = await session.execute(select(DataImport).order_by(DataImport.created_at.desc()).limit(limit))
    return [
        {
            "import_id": item.id,
            "filename": item.filename,
            "source_system": item.source_system,
            "status": item.status.value,
            "summary": item.identified_summary,
            "proposed_updates": item.proposed_updates or [],
            "created_at": item.created_at.isoformat(),
        }
        for item in result.scalars()
    ]


async def get_auditor_checklist(session: AsyncSession, checklist_id: int) -> dict[str, Any]:
    checklist = (
        await session.execute(select(AuditorChecklist).where(AuditorChecklist.id == checklist_id))
    ).scalar_one_or_none()
    if checklist is None:
        raise ValueError("Checklist not found")
    items = list(
        (
            await session.execute(
                select(AuditorChecklistItem)
                .where(AuditorChecklistItem.checklist_id == checklist_id)
                .order_by(AuditorChecklistItem.id.asc())
            )
        ).scalars()
    )
    return _to_plain(AuditorChecklistResponse(
        id=checklist.id,
        name=checklist.name,
        audit_date=checklist.audit_date,
        auditor_name=checklist.auditor_name,
        framework=checklist.framework,
        status=checklist.status.value,
        source_import_id=checklist.source_import_id,
        items=[
            AuditorChecklistItemResponse(
                id=item.id,
                item_number=item.item_number,
                description=item.description,
                control_ids=item.control_ids or [],
                status=item.status.value,
                our_response=item.our_response,
                evidence_ids=item.evidence_ids or [],
                due_date=item.due_date,
                auditor_notes=item.auditor_notes,
                priority=item.priority.value,
            )
            for item in items
        ],
    ))


async def update_checklist_item(
    session: AsyncSession,
    item_id: int,
    status: str,
    response: str,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    item = (
        await session.execute(select(AuditorChecklistItem).where(AuditorChecklistItem.id == item_id))
    ).scalar_one_or_none()
    if item is None:
        raise ValueError("Checklist item not found")
    item.status = AuditorItemStatus(status)
    item.our_response = response
    await _log_write(
        session,
        "update_checklist_item",
        {"item_id": item_id, "status": status, "response": response},
        f"Updated checklist item {item_id}",
        conversation_id,
    )
    await session.commit()
    return {"item_id": item.id, "status": item.status.value}


async def get_unsatisfied_auditor_items(session: AsyncSession) -> list[dict[str, Any]]:
    active_ids = [
        checklist.id
        for checklist in (
            await session.execute(
                select(AuditorChecklist).where(
                    AuditorChecklist.status == AuditorChecklistStatus.ACTIVE
                )
            )
        ).scalars()
    ]
    if not active_ids:
        return []
    rows = list(
        (
            await session.execute(
                select(AuditorChecklistItem).where(
                    AuditorChecklistItem.checklist_id.in_(active_ids),
                    AuditorChecklistItem.status.in_(
                        [AuditorItemStatus.OPEN, AuditorItemStatus.IN_PROGRESS]
                    ),
                )
            )
        ).scalars()
    )
    return [
        {
            "item_id": item.id,
            "checklist_id": item.checklist_id,
            "item_number": item.item_number,
            "description": item.description,
            "status": item.status.value,
            "priority": item.priority.value,
            "control_ids": item.control_ids or [],
        }
        for item in rows
    ]


async def update_control_status(
    session: AsyncSession,
    control_id: str,
    framework: str,
    status: str,
    notes: str,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    parsed_status = ControlStatus(status)
    result = await session.execute(
        select(Control, Framework)
        .join(Framework, Control.framework_id == Framework.id)
        .where(Control.control_id == control_id, Framework.short_name == framework)
    )
    row = result.first()
    if row is None:
        raise ValueError("Control not found")
    control, framework_obj = row
    control.status = parsed_status
    control.status_notes = notes
    await _log_write(
        session,
        "update_control_status",
        {"control_id": control_id, "framework": framework, "status": status, "notes": notes},
        f"Updated {control_id} to {status}",
        conversation_id,
    )
    await session.commit()
    return _to_plain(ControlResponse(
        control_id=control.control_id,
        framework=framework_obj.short_name,
        title=control.title,
        status=control.status,
        notes=control.status_notes,
    ))


async def add_evidence(
    session: AsyncSession,
    control_ids: list[str],
    filename: str,
    evidence_type: str,
    description: str,
    entity: str | None,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    if not control_ids or not filename.strip():
        raise ValueError("control_ids and filename are required")
    parsed_type = EvidenceType(evidence_type)
    item = EvidenceItem(
        filename=filename,
        evidence_type=parsed_type,
        description=description,
        entity=entity,
        collected_date=datetime.now(timezone.utc).date().isoformat(),
        status=EvidenceStatus.CURRENT,
    )
    session.add(item)
    controls_result = await session.execute(select(Control).where(Control.control_id.in_(control_ids)))
    controls = list(controls_result.scalars())
    if not controls:
        raise ValueError("No controls found for provided control_ids")
    item.controls = controls
    await _log_write(
        session,
        "add_evidence",
        {
            "control_ids": control_ids,
            "filename": filename,
            "evidence_type": evidence_type,
            "description": description,
            "entity": entity,
        },
        f"Added evidence {filename}",
        conversation_id,
    )
    await session.commit()
    return {"id": item.id, "filename": item.filename, "controls": [c.control_id for c in controls]}


async def create_finding(
    session: AsyncSession,
    control_ids: list[str],
    framework: str,
    title: str,
    description: str,
    severity: str,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    if not title.strip() or not control_ids:
        raise ValueError("title and control_ids are required")
    framework_result = await session.execute(select(Framework).where(Framework.short_name == framework))
    framework_obj = framework_result.scalar_one_or_none()
    if framework_obj is None:
        raise ValueError("Framework not found")
    finding = Finding(
        finding_id=f"FD-{int(datetime.now(timezone.utc).timestamp())}",
        framework_id=framework_obj.id,
        title=title,
        description=description,
        severity=FindingSeverity(severity),
        status=FindingStatus.OPEN,
        discovered_date=datetime.now(timezone.utc).date().isoformat(),
    )
    controls_result = await session.execute(select(Control).where(Control.control_id.in_(control_ids)))
    finding.controls = list(controls_result.scalars())
    session.add(finding)
    await _log_write(
        session,
        "create_finding",
        {
            "control_ids": control_ids,
            "framework": framework,
            "title": title,
            "description": description,
            "severity": severity,
        },
        f"Created finding {finding.finding_id}",
        conversation_id,
    )
    await session.commit()
    return _to_plain(FindingResponse(
        finding_id=finding.finding_id,
        title=finding.title,
        severity=finding.severity,
        status=finding.status,
    ))


async def update_finding(
    session: AsyncSession,
    finding_id: str,
    status: str,
    notes: str,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    result = await session.execute(select(Finding).where(Finding.finding_id == finding_id))
    finding = result.scalar_one_or_none()
    if finding is None:
        raise ValueError("Finding not found")
    finding.status = FindingStatus(status)
    finding.description = notes if notes else finding.description
    await _log_write(
        session,
        "update_finding",
        {"finding_id": finding_id, "status": status, "notes": notes},
        f"Updated finding {finding_id} to {status}",
        conversation_id,
    )
    await session.commit()
    return _to_plain(FindingResponse(
        finding_id=finding.finding_id,
        title=finding.title,
        severity=finding.severity,
        status=finding.status,
    ))


async def add_corrective_action(
    session: AsyncSession,
    finding_id: str,
    description: str,
    owner: str,
    due_date: str,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    result = await session.execute(select(Finding).where(Finding.finding_id == finding_id))
    finding = result.scalar_one_or_none()
    if finding is None:
        raise ValueError("Finding not found")
    action = CorrectiveAction(
        finding_id=finding.id,
        description=description,
        owner=owner,
        due_date=due_date,
        status=FindingStatus.OPEN,
    )
    session.add(action)
    await _log_write(
        session,
        "add_corrective_action",
        {"finding_id": finding_id, "description": description, "owner": owner, "due_date": due_date},
        f"Added corrective action for {finding_id}",
        conversation_id,
    )
    await session.commit()
    return {"id": action.id, "finding_id": finding_id, "owner": owner, "due_date": due_date}


async def update_obligation(
    session: AsyncSession,
    obligation_id: str,
    status: str,
    notes: str,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    result = await session.execute(select(Obligation).where(Obligation.obligation_id == obligation_id))
    obligation = result.scalar_one_or_none()
    if obligation is None:
        raise ValueError("Obligation not found")
    obligation.status = ObligationStatus(status)
    obligation.notes = notes
    await _log_write(
        session,
        "update_obligation",
        {"obligation_id": obligation_id, "status": status, "notes": notes},
        f"Updated obligation {obligation_id}",
        conversation_id,
    )
    await session.commit()
    return _to_plain(ObligationResponse(
        obligation_id=obligation.obligation_id,
        source=obligation.source,
        due_date=obligation.due_date,
        status=obligation.status,
    ))


async def ingest_notion_page(session: AsyncSession, page_url: str) -> dict[str, Any]:
    return _to_plain(IngestionResult(
        source_label=page_url,
        summary="Notion live integration is intentionally not implemented. Use /import/text or /import/file.",
        extracted_actions=[],
    ))


async def ingest_text(session: AsyncSession, content: str, source_label: str) -> dict[str, Any]:
    if not content.strip():
        raise ValueError("content is required")
    actions: list[str] = []
    lowered = content.lower()
    if "finding" in lowered:
        actions.append("Detected possible finding-related update")
    if "evidence" in lowered:
        actions.append("Detected evidence-related note")
    if "control" in lowered:
        actions.append("Detected control status context")
    if not actions:
        actions.append("No direct compliance action detected; stored for semantic retrieval")
    return _to_plain(IngestionResult(
        source_label=source_label,
        summary="Text ingested and analyzed for compliance actions",
        extracted_actions=actions,
    ))


async def generate_gap_report(session: AsyncSession, framework: str, format: str) -> dict[str, Any]:
    output = await generate_gap_report_file(framework, session)
    return _to_plain(ReportResult(
        name=output["filename"],
        format=format,
        summary=f"Gap report generated: {output['download_url']}",
    ))


async def generate_scorecard(session: AsyncSession) -> dict[str, Any]:
    output = await generate_scorecard_file(session)
    return _to_plain(ReportResult(
        name=output["filename"],
        format="pdf",
        summary=f"Scorecard generated: {output['download_url']}",
    ))


async def generate_audit_package(session: AsyncSession, framework: str) -> dict[str, Any]:
    output = await generate_audit_package_index_file(framework, session)
    return _to_plain(ReportResult(
        name=output["filename"],
        format="docx",
        summary=f"Audit package index generated: {output['download_url']}",
    ))


async def generate_corrective_action_report(session: AsyncSession) -> dict[str, Any]:
    output = await generate_corrective_action_report_file(session)
    return _to_plain(ReportResult(
        name=output["filename"],
        format="docx",
        summary=f"Corrective action report generated: {output['download_url']}",
    ))


# ---------------------------------------------------------------------------
# Workforce alignment tools
# ---------------------------------------------------------------------------


async def get_staffing_gaps(
    session: AsyncSession,
    pursuit_id: int | None = None,
    include_canaide: bool = False,
) -> dict[str, Any]:
    """Read tool: run gap analysis for one pursuit, or all pursuits if pursuit_id is omitted.

    Defaults to Apprio-only staff. Set include_canaide=True for cross-entity visibility.
    """
    from models.workforce import WorkforcePursuit
    from services.workforce_alignment import analyze_pursuit_gaps

    if pursuit_id is not None:
        return await analyze_pursuit_gaps(
            session, pursuit_id, include_canaide=include_canaide
        )

    pursuits = list((await session.execute(select(WorkforcePursuit))).scalars())
    results = []
    for pursuit in pursuits:
        results.append(
            await analyze_pursuit_gaps(session, pursuit.id, include_canaide=include_canaide)
        )
    return {
        "pursuit_count": len(results),
        "total_gaps": sum(int(item.get("gap_count", 0)) for item in results),
        "include_canaide": include_canaide,
        "pursuits": results,
    }


async def check_overcommitment(
    session: AsyncSession,
    include_canaide: bool = False,
) -> dict[str, Any]:
    """Read tool: flag staff whose proposed/committed assignment totals exceed 100%.

    Defaults to Apprio-only staff. Set include_canaide=True for cross-entity visibility.
    """
    from services.workforce_alignment import check_overcommitment as run_overcommitment_check

    return await run_overcommitment_check(session, include_canaide=include_canaide)


async def flag_staffing_gap(
    session: AsyncSession,
    pursuit_id: int,
    labor_category: str,
    clearance_required: str | None = None,
    notes: str | None = None,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    """Write tool: create a WorkforceGap row and log to agent_action_log."""
    from models.workforce import ClearanceLevel, GapStatus, WorkforceGap, WorkforcePursuit

    pursuit = (
        await session.execute(select(WorkforcePursuit).where(WorkforcePursuit.id == pursuit_id))
    ).scalar_one_or_none()
    if pursuit is None:
        raise ValueError(f"Pursuit {pursuit_id} not found")

    clearance = ClearanceLevel(clearance_required) if clearance_required else pursuit.required_clearance_level
    gap = WorkforceGap(
        pursuit_id=pursuit_id,
        labor_category=labor_category,
        clearance_required=clearance,
        status=GapStatus.OPEN,
        notes=notes,
    )
    session.add(gap)
    await session.flush()
    await _log_write(
        session,
        "flag_staffing_gap",
        {
            "pursuit_id": pursuit_id,
            "labor_category": labor_category,
            "clearance_required": clearance.value if clearance else None,
            "notes": notes,
        },
        f"Flagged staffing gap id={gap.id} for pursuit {pursuit_id}: {labor_category}",
        conversation_id,
    )
    await session.commit()
    await session.refresh(gap)
    return {
        "id": gap.id,
        "pursuit_id": gap.pursuit_id,
        "labor_category": gap.labor_category,
        "clearance_required": gap.clearance_required.value if gap.clearance_required else None,
        "status": gap.status.value,
        "notes": gap.notes,
    }


async def assign_staff(
    session: AsyncSession,
    staff_id: int,
    pursuit_id: int,
    role: str | None = None,
    commitment_pct: float = 100.0,
    status: str = "proposed",
    conversation_id: int | None = None,
) -> dict[str, Any]:
    """Write tool: create a WorkforceAssignment and log to agent_action_log."""
    from models.workforce import (
        AssignmentStatus,
        WorkforceAssignment,
        WorkforcePursuit,
        WorkforceStaff,
    )

    staff = (
        await session.execute(select(WorkforceStaff).where(WorkforceStaff.id == staff_id))
    ).scalar_one_or_none()
    if staff is None:
        raise ValueError(f"Staff {staff_id} not found")
    pursuit = (
        await session.execute(select(WorkforcePursuit).where(WorkforcePursuit.id == pursuit_id))
    ).scalar_one_or_none()
    if pursuit is None:
        raise ValueError(f"Pursuit {pursuit_id} not found")

    assignment = WorkforceAssignment(
        staff_id=staff_id,
        pursuit_id=pursuit_id,
        role=role,
        commitment_pct=commitment_pct,
        status=AssignmentStatus(status),
    )
    session.add(assignment)
    await session.flush()
    await _log_write(
        session,
        "assign_staff",
        {
            "staff_id": staff_id,
            "pursuit_id": pursuit_id,
            "role": role,
            "commitment_pct": commitment_pct,
            "status": status,
        },
        f"Assigned staff {staff_id} to pursuit {pursuit_id} at {commitment_pct}% ({status})",
        conversation_id,
    )
    await session.commit()
    await session.refresh(assignment)
    return {
        "id": assignment.id,
        "staff_id": assignment.staff_id,
        "pursuit_id": assignment.pursuit_id,
        "role": assignment.role,
        "commitment_pct": assignment.commitment_pct,
        "status": assignment.status.value,
    }

