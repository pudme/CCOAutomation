from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import click
import httpx
from minio import Minio
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import get_settings
from database import AsyncSessionLocal, init_db
from models.compliance import (
    AgentActionLog,
    AppSetting,
    Control,
    ControlStatus,
    Conversation,
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
    ImportStatus,
    Message,
    Obligation,
    ObligationStatus,
    PersonnelRecord,
    evidence_control_association,
    finding_control_association,
)
from models.auditor import AuditorChecklist, AuditorChecklistItem
from routers.settings import seed_audit_date_settings
from services.framework_loader import load_framework
from services.import_pipeline import (
    backfill_evidence_links,
    fix_compliance_doc_embeddings,
    reanalyze_all_evidence_imports,
    reparse_checklist_control_mappings,
)
REANALYZE_STATUS_KEY = "reanalyze_status"


async def _update_reanalyze_status(payload: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(AppSetting).where(AppSetting.key == REANALYZE_STATUS_KEY))
        ).scalars().first()
        current: dict = {}
        if row is not None and row.value:
            try:
                current = json.loads(row.value)
            except json.JSONDecodeError:
                current = {}
        merged = {
            "running": False,
            "completed": 0,
            "total": 0,
            "new_links": 0,
            "started_at": None,
            "finished_at": None,
            "message": "",
            **current,
            **payload,
        }
        value = json.dumps(merged, separators=(",", ":"))
        if row is None:
            row = AppSetting(key=REANALYZE_STATUS_KEY, value=value, updated_at=now)
            session.add(row)
        else:
            row.value = value
            row.updated_at = now
        await session.commit()




settings = get_settings()


@click.group()
def cli() -> None:
    pass


@cli.command("init-db")
def init_db_cmd() -> None:
    async def _run() -> None:
        await init_db()
        async with AsyncSessionLocal() as session:
            await seed_audit_date_settings(session)
        click.echo("Database initialized.")

    asyncio.run(_run())


@cli.command("load-framework")
@click.argument("path_or_short_name", type=str)
def load_framework_cmd(path_or_short_name: str) -> None:
    async def _run() -> None:
        raw = Path(path_or_short_name)
        framework_path = raw
        if not raw.exists():
            frameworks_dir = Path(__file__).parent / "config" / "frameworks"
            candidate = frameworks_dir / f"{path_or_short_name}.yaml"
            if not candidate.exists():
                raise click.ClickException(f"Framework YAML not found: {path_or_short_name}")
            framework_path = candidate
        async with AsyncSessionLocal() as session:
            summary = await load_framework(framework_path, session)
            click.echo(summary)

    asyncio.run(_run())


@cli.command("load-all-frameworks")
def load_all_frameworks_cmd() -> None:
    async def _run() -> None:
        frameworks_dir = Path(__file__).parent / "config" / "frameworks"
        yaml_files = sorted(frameworks_dir.glob("*.yaml"))
        if not yaml_files:
            click.echo(f"No framework YAML files found in {frameworks_dir}")
            return
        async with AsyncSessionLocal() as session:
            for yaml_file in yaml_files:
                summary = await load_framework(yaml_file, session)
                click.echo(f"{yaml_file.name}: {summary}")

    asyncio.run(_run())


@cli.command("health")
def health_cmd() -> None:
    async def _run() -> None:
        report: dict[str, str | int] = {"db": "down", "redis": "down", "minio": "down", "chromadb": "down"}

        async with AsyncSessionLocal() as session:
            try:
                await session.execute(text("SELECT 1"))
                report["db"] = "ok"
                framework_count = (
                    await session.execute(
                        select(func.count(Framework.id)).where(
                            Framework.short_name.in_(["iso27001", "cmmc_l2"])
                        )
                    )
                ).scalar_one()
                control_count = (
                    await session.execute(
                        select(func.count(Control.id))
                        .join(Framework, Control.framework_id == Framework.id)
                        .where(Framework.short_name.in_(["iso27001", "cmmc_l2"]))
                    )
                ).scalar_one()
                report["framework_count"] = framework_count
                report["control_count"] = control_count
            except Exception as exc:  # noqa: BLE001
                report["db"] = f"error: {exc}"

        try:
            import redis

            redis.Redis.from_url(settings.redis_url).ping()
            report["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            report["redis"] = f"error: {exc}"

        try:
            minio_client = Minio(
                endpoint=settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=False,
            )
            minio_client.bucket_exists(settings.minio_bucket)
            report["minio"] = "ok"
        except Exception as exc:  # noqa: BLE001
            report["minio"] = f"error: {exc}"

        try:
            chroma_url = f"http://{settings.chroma_host}:{settings.chroma_port}/api/v2/heartbeat"
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(chroma_url)
            report["chromadb"] = "ok" if response.is_success else f"http {response.status_code}"
        except Exception as exc:  # noqa: BLE001
            report["chromadb"] = f"error: {exc}"

        click.echo(report)

    asyncio.run(_run())


@cli.command("reset-db")
@click.argument("confirmation", type=str)
def reset_db_cmd(confirmation: str) -> None:
    if confirmation != "CONFIRM":
        raise click.ClickException("reset-db requires literal argument: CONFIRM")

    async def _run() -> None:
        deletion_order = [
            ("AgentActionLog", AgentActionLog),
            ("Message", Message),
            ("Conversation", Conversation),
            ("CorrectiveAction", CorrectiveAction),
            ("Finding", Finding),
            ("evidence_control", evidence_control_association),
            ("finding_control", finding_control_association),
            ("EvidenceItem", EvidenceItem),
            ("EvidenceRequirement", EvidenceRequirement),
            ("PersonnelRecord", PersonnelRecord),
            ("Obligation", Obligation),
            ("DataImport", DataImport),
            ("app_settings", AppSetting),
        ]
        execution_order = [
            "AgentActionLog",
            "Message",
            "Conversation",
            "CorrectiveAction",
            "evidence_control",
            "finding_control",
            "Finding",
            "EvidenceItem",
            "EvidenceRequirement",
            "PersonnelRecord",
            "Obligation",
            "DataImport",
            "app_settings",
        ]
        tables_by_name = {name: table_or_model for name, table_or_model in deletion_order}
        counts: dict[str, int] = {}
        async with AsyncSessionLocal() as session:
            for name, table_or_model in deletion_order:
                count = (
                    await session.execute(select(func.count()).select_from(table_or_model))
                ).scalar_one()
                counts[name] = int(count)
            for name in execution_order:
                table_or_model = tables_by_name[name]
                await session.execute(delete(table_or_model))
            await session.execute(
                text(
                    "UPDATE controls SET status = 'NOT_STARTED', status_notes = NULL, last_reviewed = NULL"
                )
            )
            await session.commit()

        for key, value in counts.items():
            click.echo(f"{key}: {value} deleted")
        click.echo("Control statuses reset to NOT_STARTED.")
        click.echo("Database reset complete. Framework definitions preserved.")

    asyncio.run(_run())


@cli.command("seed-demo")
def seed_demo_cmd() -> None:
    async def _run() -> None:
        evidenced_controls = ["A.5.1", "A.6.3", "AC.L1-3.1.1", "AU.L2-3.3.1"]
        in_progress_controls = ["A.8.5", "SC.L2-3.13.8"]
        missing_controls = ["A.7.4", "IR.L2-3.6.2"]

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Control)
                .options(selectinload(Control.evidence_items))
                .where(
                    Control.control_id.in_(
                        evidenced_controls + in_progress_controls + missing_controls
                    )
                )
            )
            controls = {control.control_id: control for control in result.scalars()}
            if not controls:
                click.echo("No matching controls found. Load frameworks first.")
                return

            created_evidence = 0
            linked_evidence = 0

            for control_id in evidenced_controls:
                control = controls.get(control_id)
                if control is None:
                    continue
                control.status = ControlStatus.EVIDENCED
                control.status_notes = "Seed demo: evidence currently available."

                filename = f"seed_demo_{control_id.replace('.', '_')}.txt"
                evidence_result = await session.execute(
                    select(EvidenceItem).where(EvidenceItem.filename == filename)
                )
                evidence = evidence_result.scalar_one_or_none()
                if evidence is None:
                    evidence = EvidenceItem(
                        filename=filename,
                        file_path=f"imports/{filename}",
                        evidence_type=EvidenceType.RECORD,
                        description=f"Seed demo evidence for {control_id}",
                        entity="Apprio",
                        collected_date=datetime.now(timezone.utc).date().isoformat(),
                        review_date=None,
                        status=EvidenceStatus.CURRENT,
                        notes="Generated by seed-demo command.",
                    )
                    session.add(evidence)
                    await session.flush()
                    created_evidence += 1
                else:
                    evidence.status = EvidenceStatus.CURRENT

                if evidence not in control.evidence_items:
                    control.evidence_items.append(evidence)
                    linked_evidence += 1

            for control_id in in_progress_controls:
                control = controls.get(control_id)
                if control is None:
                    continue
                control.status = ControlStatus.IN_PROGRESS
                control.status_notes = "Seed demo: remediation in progress."

            for control_id in missing_controls:
                control = controls.get(control_id)
                if control is None:
                    continue
                control.status = ControlStatus.NOT_STARTED
                control.status_notes = "Seed demo: evidence missing."

            await session.commit()

            findings_count, actions_count, obligations_count = await _seed_known_findings_and_obligations(session)
            click.echo(
                {
                    "controls_updated": len(controls),
                    "evidence_created": created_evidence,
                    "evidence_links_added": linked_evidence,
                    "findings_seeded": findings_count,
                    "corrective_actions_seeded": actions_count,
                    "obligations_seeded": obligations_count,
                    "seed_profile": {
                        "evidenced": evidenced_controls,
                        "in_progress": in_progress_controls,
                        "missing": missing_controls,
                    },
                }
            )

    asyncio.run(_run())


@cli.command("seed-findings")
def seed_findings_cmd() -> None:
    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            findings_count, actions_count, obligations_count = await _seed_known_findings_and_obligations(session)
            click.echo(
                {
                    "findings_seeded": findings_count,
                    "corrective_actions_seeded": actions_count,
                    "obligations_seeded": obligations_count,
                }
            )

    asyncio.run(_run())


@cli.command("clear-documents")
@click.argument("confirmation", type=str)
def clear_documents_cmd(confirmation: str) -> None:
    if confirmation != "CONFIRM":
        raise click.ClickException("clear-documents requires literal argument: CONFIRM")

    async def _run() -> None:
        minio_client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
        )
        evidence_paths: list[str] = []
        import_paths: list[str] = []

        async with AsyncSessionLocal() as session:
            evidence_rows = list(
                (
                    await session.execute(
                        select(EvidenceItem.id, EvidenceItem.file_path)
                    )
                ).all()
            )
            import_rows = list(
                (
                    await session.execute(
                        select(DataImport.id, DataImport.minio_path)
                    )
                ).all()
            )

            evidence_ids = [int(row[0]) for row in evidence_rows]
            import_ids = [int(row[0]) for row in import_rows]
            evidence_paths = [str(row[1]) for row in evidence_rows if row[1]]
            import_paths = [str(row[1]) for row in import_rows if row[1]]

            if import_ids:
                await session.execute(
                    update(AuditorChecklist)
                    .where(AuditorChecklist.source_import_id.in_(import_ids))
                    .values(source_import_id=None)
                )
                await session.execute(
                    update(AuditorChecklistItem)
                    .where(AuditorChecklistItem.source_import_id.in_(import_ids))
                    .values(source_import_id=None)
                )

            if evidence_ids:
                await session.execute(
                    delete(evidence_control_association).where(
                        evidence_control_association.c.evidence_id.in_(evidence_ids)
                    )
                )

            await session.execute(delete(EvidenceItem))
            await session.execute(delete(DataImport))
            await session.commit()

        minio_deleted = 0
        minio_failed = 0
        for object_path in sorted(set(evidence_paths + import_paths)):
            try:
                minio_client.remove_object(settings.minio_bucket, object_path)
                minio_deleted += 1
            except Exception:  # noqa: BLE001
                minio_failed += 1

        click.echo(
            {
                "evidence_deleted": len(evidence_rows),
                "imports_deleted": len(import_rows),
                "minio_deleted": minio_deleted,
                "minio_failed": minio_failed,
                "status": "documents_cleared",
            }
        )

    asyncio.run(_run())


@cli.command("backfill-evidence")
@click.option("--limit", type=int, default=None)
def backfill_evidence_cmd(limit: int | None) -> None:
    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            stats = await backfill_evidence_links(session, limit=limit)
        click.echo(stats)

    asyncio.run(_run())


@cli.command("reanalyze-evidence")
@click.option("--limit", type=int, default=None)
@click.option("--start-from", "start_from", type=int, default=0)
def reanalyze_evidence_cmd(limit: int | None, start_from: int) -> None:
    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            total_result = await session.execute(
                select(func.count(DataImport.id)).where(
                    DataImport.status == ImportStatus.COMPLETE,
                    DataImport.id > int(start_from or 0),
                )
            )
            total = int(total_result.scalar() or 0)
        await _update_reanalyze_status(
            {
                "running": True,
                "completed": 0,
                "total": total,
                "new_links": 0,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "message": "Analyzing documents in background",
            }
        )
        async with AsyncSessionLocal() as session:
            async def _on_progress(update: dict[str, int]) -> None:
                await _update_reanalyze_status(
                    {
                        "running": True,
                        "completed": int(update.get("completed", 0)),
                        "total": int(update.get("total", total)),
                        "new_links": int(update.get("new_links", 0)),
                        "finished_at": None,
                        "message": "Analyzing documents in background",
                    }
                )

            stats = await reanalyze_all_evidence_imports(
                session,
                limit=limit,
                progress_callback=_on_progress,
                start_from=start_from,
            )
        analyzed = (
            int(stats.get("reanalyzed", 0))
            + int(stats.get("skipped_already_analyzed", 0))
            + int(stats.get("errors", 0))
        )
        await _update_reanalyze_status(
            {
                "running": False,
                "completed": analyzed,
                "total": int(stats.get("total_complete_imports", total)),
                "new_links": int(stats.get("new_control_links", 0)),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "message": (
                    f"Reanalysis complete — {analyzed} documents analyzed, "
                    f"{int(stats.get('new_control_links', 0))} new control links created"
                ),
            }
        )
        click.echo(stats)

    asyncio.run(_run())


@cli.command("fix-embeddings")
def fix_embeddings_cmd() -> None:
    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            stats = await fix_compliance_doc_embeddings(session)
        click.echo(stats)

    asyncio.run(_run())


@cli.command("reparse-checklist")
@click.argument("checklist_id", type=int)
@click.option("--bypass-limit", is_flag=True, default=False)
def reparse_checklist_cmd(checklist_id: int, bypass_limit: bool) -> None:
    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            result = await reparse_checklist_control_mappings(
                session,
                checklist_id=checklist_id,
                bypass_limit=bool(bypass_limit),
            )
            click.echo(result)

    asyncio.run(_run())


async def _seed_known_findings_and_obligations(session: AsyncSession) -> tuple[int, int, int]:
    # Add new enum value for existing Postgres environments if needed.
    await session.execute(text("ALTER TYPE obligationstatus ADD VALUE IF NOT EXISTS 'IN_PROGRESS'"))
    await session.commit()

    findings = [
        {
            "finding_id": "AF-01",
            "framework": "iso27001",
            "audit_cycle": "2025-2026",
            "title": "No formal visitor log maintained at Wisconsin Ave office",
            "description": (
                "The Apprio Wisconsin Ave NW office (Suite 245, Washington D.C.) "
                "does not maintain a formal visitor log. Physical entry relies on "
                "key lock only with no documented visitor record."
            ),
            "severity": "observation",
            "status": "in_progress",
            "discovered_date": "2026-03-20",
            "owner": "Facilities / IT",
            "control_ids": ["A.7.1", "A.7.2"],
        },
        {
            "finding_id": "AF-02",
            "framework": "iso27001",
            "audit_cycle": "2025-2026",
            "title": "No security camera coverage at Wisconsin Ave office",
            "description": (
                "The Wisconsin Ave office has no security camera coverage. "
                "Risk formally accepted given low-risk classification of the "
                "facility (lock-and-key, no networked equipment except printer)."
            ),
            "severity": "observation",
            "status": "risk_accepted",
            "discovered_date": "2026-03-20",
            "owner": "Facilities / IT",
            "control_ids": ["A.7.4"],
        },
        {
            "finding_id": "AF-03",
            "framework": "iso27001",
            "audit_cycle": "2025-2026",
            "title": "Physical entry controls relying solely on key lock",
            "description": (
                "Physical entry at Wisconsin Ave relies solely on a key lock "
                "with no electronic access logging or badge system. No audit "
                "trail exists for physical access events."
            ),
            "severity": "observation",
            "status": "in_progress",
            "discovered_date": "2026-03-20",
            "owner": "Facilities / IT",
            "control_ids": ["A.7.2"],
        },
        {
            "finding_id": "AF-04",
            "framework": "iso27001",
            "audit_cycle": "2025-2026",
            "title": "Camera installation at Canaide Orlando office pending",
            "description": (
                "Camera and buzzer installation at the Canaide McGuire Boulevard "
                "Orlando office (Suite 100) is planned but not yet completed. "
                "IT is responsible for installation before the external audit. "
                "Risk formally accepted pending installation."
            ),
            "severity": "observation",
            "status": "risk_accepted",
            "discovered_date": "2026-03-20",
            "owner": "IT",
            "control_ids": ["A.7.4"],
        },
        {
            "finding_id": "AF-05",
            "framework": "iso27001",
            "audit_cycle": "2025-2026",
            "title": "Log retention gaps across multiple systems",
            "description": (
                "Audit log retention periods are insufficient across several "
                "systems: Entra ID retains logs for 30 days, Intune retains "
                "logs for 30 days. NinjaOne had no audit logging configured "
                "at time of discovery. AWS and M365 Admin Center retain 90 days. "
                "ISO 27001 A.8.15 requires retention aligned to the Records "
                "Retention Schedule."
            ),
            "severity": "minor_nc",
            "status": "open",
            "discovered_date": "2026-03-15",
            "owner": "IT",
            "control_ids": ["A.8.15"],
        },
        {
            "finding_id": "AF-06",
            "framework": "iso27001",
            "audit_cycle": "2025-2026",
            "title": "NinjaOne audit logging never enabled",
            "description": (
                "NinjaOne audit logging was never activated since deployment. "
                "No audit log records exist for NinjaOne activity. "
                "Activation with documented activation date is required as "
                "evidence of remediation."
            ),
            "severity": "minor_nc",
            "status": "open",
            "discovered_date": "2026-03-15",
            "owner": "IT",
            "control_ids": ["A.8.15"],
        },
        {
            "finding_id": "PF-01",
            "framework": "iso27001",
            "audit_cycle": "2025-2026",
            "title": "Training completion gaps — prior finding carried forward",
            "description": (
                "Prior finding from 2024-2025 audit cycle. Training gap analysis "
                "identified 36 active employees with no confirmed training completion "
                "for the current cycle. Fall 2025 HITRUST training rollout was the "
                "evidence source. Completion report pending from IT. "
                "80 employees confirmed complete. Quiz-only completions count as valid."
            ),
            "severity": "minor_nc",
            "status": "in_progress",
            "discovered_date": "2025-05-01",
            "owner": "Chief Compliance Officer",
            "control_ids": ["A.6.3"],
        },
    ]

    corrective_actions = [
        {
            "finding_id": "AF-01",
            "description": "Implement and maintain a physical visitor log at Wisconsin Ave office. Robin (office contact) to be designated as responsible party.",
            "owner": "Facilities / IT",
            "due_date": "2026-05-01",
            "status": "in_progress",
        },
        {
            "finding_id": "AF-03",
            "description": "Document physical access control procedure for Wisconsin Ave. Assess feasibility of electronic access logging.",
            "owner": "IT",
            "due_date": "2026-05-01",
            "status": "in_progress",
        },
        {
            "finding_id": "AF-05",
            "description": "Extend log retention periods where technically feasible. Document retention justification in Records Retention Schedule for systems where extension is not possible.",
            "owner": "IT",
            "due_date": "2026-05-15",
            "status": "open",
        },
        {
            "finding_id": "AF-06",
            "description": "Activate NinjaOne audit logging. Document the activation date and provide screenshot as evidence of remediation.",
            "owner": "IT",
            "due_date": "2026-05-01",
            "status": "open",
        },
        {
            "finding_id": "PF-01",
            "description": "Obtain Fall 2025 HITRUST training completion report from IT. Cross-reference against active employee list to confirm 36 gap employees have completed training.",
            "owner": "Chief Compliance Officer",
            "due_date": "2026-05-01",
            "status": "in_progress",
        },
    ]

    obligations = [
        {
            "obligation_id": "OBL-001",
            "source": "ISO Certification Body",
            "description": "Annual management review of the ISMS must be completed and documented before the surveillance audit.",
            "owner": "Chief Compliance Officer",
            "cadence": "Annual",
            "due_date": "2026-04-30",
            "status": "satisfied",
            "last_satisfied": "2026-04-02",
            "notes": "Management review completed April 2, 2026. Attendees: Sri Krishnan, Michael DuPlantis, Todd Traver, George Pope-Reyes. Minutes documented.",
        },
        {
            "obligation_id": "OBL-002",
            "source": "ISO Certification Body",
            "description": "Internal audit of the ISMS must be completed within the certification cycle and reported before the surveillance audit.",
            "owner": "Chief Compliance Officer",
            "cadence": "Annual",
            "due_date": "2026-05-01",
            "status": "satisfied",
            "last_satisfied": "2026-03-15",
            "notes": "Internal audit completed. Report: R17_Internal_Audit_Report_Cyber_Security_2026.docx.",
        },
        {
            "obligation_id": "OBL-003",
            "source": "ISO Certification Body",
            "description": "Evidence package for surveillance audit must be submitted and all Minor NCs from prior cycle must show remediation progress.",
            "owner": "Chief Compliance Officer",
            "cadence": "One-time",
            "due_date": "2026-05-15",
            "status": "in_progress",
            "notes": "AF-05 and AF-06 (log retention/NinjaOne) and PF-01 (training) are open Minor NCs requiring evidence of remediation.",
        },
    ]

    framework_by_short_name = {
        framework.short_name: framework
        for framework in (
            await session.execute(select(Framework))
        ).scalars()
    }
    control_by_key = {
        (framework.short_name, control.control_id): control
        for control, framework in (
            await session.execute(
                select(Control, Framework).join(Framework, Framework.id == Control.framework_id)
            )
        ).all()
    }

    finding_status_map = {
        "open": FindingStatus.OPEN,
        "in_progress": FindingStatus.IN_PROGRESS,
        "resolved": FindingStatus.RESOLVED,
        "verified": FindingStatus.VERIFIED,
        "closed": FindingStatus.CLOSED,
        "risk_accepted": FindingStatus.RISK_ACCEPTED,
    }
    finding_severity_map = {
        "observation": FindingSeverity.OBSERVATION,
        "minor_nc": FindingSeverity.MINOR_NC,
        "major_nc": FindingSeverity.MAJOR_NC,
        "critical": FindingSeverity.CRITICAL,
    }
    obligation_status_map: dict[str, ObligationStatus] = {
        "current": ObligationStatus.CURRENT,
        "in_progress": ObligationStatus.IN_PROGRESS,
        "due_soon": ObligationStatus.DUE_SOON,
        "overdue": ObligationStatus.OVERDUE,
        "satisfied": ObligationStatus.SATISFIED,
        "waived": ObligationStatus.WAIVED,
    }

    finding_rows = 0
    action_rows = 0
    obligation_rows = 0

    for payload in findings:
        framework = framework_by_short_name.get(payload["framework"])
        if framework is None:
            raise click.ClickException(f"Framework not loaded: {payload['framework']}")

        finding = (
            await session.execute(select(Finding).where(Finding.finding_id == payload["finding_id"]))
        ).scalar_one_or_none()

        if finding is None:
            finding = Finding(
                finding_id=payload["finding_id"],
                framework_id=framework.id,
                title=payload["title"],
                description=payload["description"],
                severity=finding_severity_map[payload["severity"]],
                status=finding_status_map[payload["status"]],
                discovered_date=payload["discovered_date"],
                owner=payload["owner"],
            )
            session.add(finding)
            await session.flush()
        else:
            finding.framework_id = framework.id
            finding.title = payload["title"]
            finding.description = payload["description"]
            finding.severity = finding_severity_map[payload["severity"]]
            finding.status = finding_status_map[payload["status"]]
            finding.discovered_date = payload["discovered_date"]
            finding.owner = payload["owner"]

        await session.execute(
            delete(finding_control_association).where(
                finding_control_association.c.finding_id == finding.id
            )
        )
        for control_id in payload["control_ids"]:
            control = control_by_key.get((payload["framework"], control_id))
            if control is not None:
                await session.execute(
                    insert(finding_control_association).values(
                        finding_id=finding.id,
                        control_id=control.id,
                    )
                )

        finding_rows += 1

    await session.flush()

    findings_by_id = {
        finding.finding_id: finding
        for finding in (
            await session.execute(select(Finding))
        ).scalars()
    }

    for payload in corrective_actions:
        finding = findings_by_id.get(payload["finding_id"])
        if finding is None:
            continue

        action = (
            await session.execute(
                select(CorrectiveAction).where(
                    CorrectiveAction.finding_id == finding.id,
                    CorrectiveAction.description == payload["description"],
                )
            )
        ).scalar_one_or_none()

        if action is None:
            action = CorrectiveAction(
                finding_id=finding.id,
                description=payload["description"],
                owner=payload["owner"],
                due_date=payload["due_date"],
                status=finding_status_map[payload["status"]],
            )
            session.add(action)
        else:
            action.owner = payload["owner"]
            action.due_date = payload["due_date"]
            action.status = finding_status_map[payload["status"]]

        action_rows += 1

    for payload in obligations:
        obligation = (
            await session.execute(
                select(Obligation).where(Obligation.obligation_id == payload["obligation_id"])
            )
        ).scalar_one_or_none()

        mapped_status = obligation_status_map[payload["status"]]

        if obligation is None:
            obligation = Obligation(
                obligation_id=payload["obligation_id"],
                source=payload["source"],
                description=payload["description"],
                owner=payload["owner"],
                cadence=payload["cadence"],
                due_date=payload["due_date"],
                status=mapped_status,
                last_satisfied=payload.get("last_satisfied"),
                notes=payload.get("notes"),
            )
            session.add(obligation)
        else:
            obligation.source = payload["source"]
            obligation.description = payload["description"]
            obligation.owner = payload["owner"]
            obligation.cadence = payload["cadence"]
            obligation.due_date = payload["due_date"]
            obligation.status = mapped_status
            obligation.last_satisfied = payload.get("last_satisfied")
            obligation.notes = payload.get("notes")

        obligation_rows += 1

    await session.commit()
    return finding_rows, action_rows, obligation_rows


if __name__ == "__main__":
    cli()

