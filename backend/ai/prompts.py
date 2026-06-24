from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.compliance import AppSetting, Finding, FindingStatus, Framework
from services.readiness_mode import get_framework_readiness_with_mode

APPRIO_CONTEXT = """
- Org: Apprio Inc. (federal) + Canaide (commercial, divesting)
- Operator: Michael DuPlantis — Chief Compliance Officer, Sr. Cybersecurity Architect
- CEO: Todd Traver | Incoming CTO: Pete | Outgoing CISO: Sri Krishnan (departing with Canaide)
- Infrastructure: AWS GovCloud us-gov-west-1 (Account 455490517765), Entra ID, Intune, NinjaOne, CrowdStrike, ADP
- Active audit cycle: May 2025 – May 2026 | External audit: May 2026
- CMMC assessment target: Summer 2026
- Open findings: AF-01, AF-02, AF-03, AF-04, AF-05, AF-06, PF-01
- Evidence naming: [ControlID]_[Description]_[Entity_optional]_[YYYYMMDD].[ext]
- Word doc metadata: creator and lastModifiedBy = "Michael DuPlantis"
- Apprio logo in all generated document headers
""".strip()

PERSONAL_ASSISTANT_DIRECTIVE = """
You are Michael DuPlantis's personal compliance assistant. You are not a
general-purpose tool - you are purpose-built for Apprio's compliance program.
You know the full context of every active framework, every open finding, every
evidence gap, and every upcoming obligation. You think like a compliance officer
and a cybersecurity architect simultaneously.

Your job is to help Michael work faster and more accurately. When he gives you
data - a CSV export, a set of meeting notes, a scan report, a policy document -
you understand what it means in the context of his compliance program and you
take action on it. You don't ask unnecessary questions. You tell him what you
found, what you did, and what still needs his attention.

When he asks you a question, answer it directly and completely. When he gives
you something to process, process it fully and report back. When you see a gap
or a risk he hasn't mentioned, surface it proactively.

You have access to all compliance records, all uploaded documents, and all
historical data in this system. Read from the database before answering any
question about current state. Never assume or hallucinate compliance status.
""".strip()


async def build_system_prompt(session: AsyncSession, operator_name: str = "Michael DuPlantis") -> str:
    framework_rows = list(
        (
            await session.execute(
                select(Framework).where(Framework.active.is_(True)).order_by(Framework.short_name.asc())
            )
        ).scalars()
    )
    deduped_frameworks: dict[str, Framework] = {}
    for framework in framework_rows:
        key = (framework.short_name or "").strip()
        if key and key not in deduped_frameworks:
            deduped_frameworks[key] = framework
    frameworks = sorted(deduped_frameworks.keys())

    open_findings_result = await session.execute(
        select(func.count(Finding.id)).where(Finding.status.not_in([FindingStatus.CLOSED, FindingStatus.VERIFIED]))
    )
    open_findings = int(open_findings_result.scalar_one() or 0)

    today = date.today()
    iso_setting = (
        await session.execute(select(AppSetting).where(AppSetting.key == "audit_date_iso"))
    ).scalars().first()
    cmmc_setting = (
        await session.execute(select(AppSetting).where(AppSetting.key == "audit_date_cmmc"))
    ).scalars().first()
    dpa_setting = (
        await session.execute(select(AppSetting).where(AppSetting.key == "audit_date_dpa"))
    ).scalars().first()
    ato_setting = (
        await session.execute(select(AppSetting).where(AppSetting.key == "audit_date_ato"))
    ).scalars().first()

    iso_audit_date_str = (iso_setting.value if iso_setting and iso_setting.value else "2026-05-15").strip()
    cmmc_audit_date_str = (cmmc_setting.value if cmmc_setting and cmmc_setting.value else "2026-09-01").strip()
    try:
        iso_audit_date = date.fromisoformat(iso_audit_date_str)
    except ValueError:
        iso_audit_date = date.fromisoformat("2026-05-15")
    try:
        cmmc_audit_date = date.fromisoformat(cmmc_audit_date_str)
    except ValueError:
        cmmc_audit_date = date.fromisoformat("2026-09-01")
    dpa_date_str = (dpa_setting.value if dpa_setting and dpa_setting.value else "").strip()
    ato_date_str = (ato_setting.value if ato_setting and ato_setting.value else "").strip()
    dpa_audit_line = ""
    ato_audit_line = ""
    if "dpa_attachment_c" in frameworks and dpa_date_str:
        try:
            dpa_date = date.fromisoformat(dpa_date_str)
            dpa_days_to_review = (dpa_date - today).days
            dpa_audit_line = f"DPA Follow-up review (Attachment C): {dpa_date.isoformat()} ({dpa_days_to_review} days)\n"
        except ValueError:
            dpa_audit_line = ""
    if "nist_800_53" in frameworks and ato_date_str:
        try:
            ato_date = date.fromisoformat(ato_date_str)
            ato_days_to_review = (ato_date - today).days
            ato_audit_line = (
                f"ATO readiness review (NIST 800-53 Moderate): {ato_date.isoformat()} "
                f"({ato_days_to_review} days)\n"
            )
        except ValueError:
            ato_audit_line = ""

    iso_days_to_audit = (iso_audit_date - today).days
    cmmc_days_to_audit = (cmmc_audit_date - today).days
    readiness_lines: list[str] = []
    for framework in deduped_frameworks.values():
        readiness = await get_framework_readiness_with_mode(session, framework)
        if readiness["mode"] == "auditor":
            readiness_lines.append(
                f"- {framework.name}: AUDITOR MODE — {readiness['satisfied']}/{readiness['total']} auditor items satisfied ({readiness['checklist_name']})"
            )
        else:
            readiness_lines.append(
                f"- {framework.name}: FRAMEWORK MODE — {readiness['evidenced_controls']}/{readiness['total_controls']} controls evidenced"
            )
    readiness_block = "\n".join(readiness_lines) if readiness_lines else "- No active frameworks loaded."

    return (
        f"{PERSONAL_ASSISTANT_DIRECTIVE}\n\n"
        "Always read current DB state before any write. Stream results and clearly separate analysis from action.\n\n"
        f"Current date: {today.isoformat()}\n"
        f"Operator: {operator_name}\n"
        f"Active frameworks: {', '.join(frameworks) if frameworks else 'None loaded'}\n"
        f"Open findings count: {open_findings}\n"
        f"ISO frameworks audit (27001 / 20000 / 9001): {iso_audit_date.isoformat()} ({iso_days_to_audit} days)\n"
        f"CMMC Level 2 assessment: {cmmc_audit_date.isoformat()} ({cmmc_days_to_audit} days)\n\n"
        f"{dpa_audit_line}"
        f"{ato_audit_line}\n"
        "Current readiness mode per framework:\n"
        f"{readiness_block}\n\n"
        "Apprio context:\n"
        f"{APPRIO_CONTEXT}"
    )

