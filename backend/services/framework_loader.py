from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.compliance import (
    Control,
    EvidenceRequirement,
    EvidenceType,
    Framework,
    control_mappings_association,
)


async def load_framework(yaml_path: str | Path, session: AsyncSession) -> dict[str, int]:
    path = Path(yaml_path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not payload or "framework" not in payload:
        raise ValueError(f"Invalid framework YAML: {path}")

    framework_data = payload["framework"]
    controls_data = payload.get("controls", [])

    framework_result = await session.execute(
        select(Framework).where(Framework.short_name == framework_data["short_name"])
    )
    framework = framework_result.scalar_one_or_none()
    if framework is None:
        framework = Framework(
            name=framework_data["name"],
            version=str(framework_data["version"]),
            short_name=framework_data["short_name"],
            description=framework_data.get("description"),
            active=bool(framework_data.get("active", True)),
            loaded_date=None,
        )
        session.add(framework)
        await session.flush()
    else:
        framework.name = framework_data["name"]
        framework.version = str(framework_data["version"])
        framework.description = framework_data.get("description")
        framework.active = bool(framework_data.get("active", True))

    controls_created = 0
    controls_updated = 0

    for control_data in controls_data:
        control_id = str(control_data["id"])
        control_result = await session.execute(
            select(Control).where(Control.framework_id == framework.id, Control.control_id == control_id)
        )
        control = control_result.scalar_one_or_none()

        if control is None:
            control = Control(
                framework_id=framework.id,
                control_id=control_id,
                title=control_data["title"],
                domain=control_data.get("domain") or control_data.get("section") or "General",
                description=control_data.get("description") or control_data.get("control_description"),
                implementation_guidance=control_data.get("implementation_guidance"),
                owner_default=control_data.get("owner_default"),
                review_cadence_days=int(control_data.get("review_cadence_days", 365)),
                maps_to=control_data.get("maps_to"),
            )
            session.add(control)
            await session.flush()
            controls_created += 1
        else:
            control.title = control_data["title"]
            control.domain = control_data.get("domain") or control_data.get("section") or control.domain
            control.description = control_data.get("description") or control_data.get("control_description")
            control.implementation_guidance = control_data.get("implementation_guidance")
            control.owner_default = control_data.get("owner_default")
            control.review_cadence_days = int(control_data.get("review_cadence_days", 365))
            control.maps_to = control_data.get("maps_to")
            controls_updated += 1

        req_result = await session.execute(
            select(EvidenceRequirement).where(EvidenceRequirement.control_id == control.id)
        )
        existing_reqs = list(req_result.scalars())
        for req in existing_reqs:
            await session.delete(req)
        await session.flush()

        for req_data in control_data.get("evidence_required", []):
            req = EvidenceRequirement(
                control_id=control.id,
                evidence_type=EvidenceType(req_data["type"]),
                description=req_data.get("description"),
                required=bool(req_data.get("required", True)),
            )
            session.add(req)

    await session.flush()
    mappings_created = await _resolve_control_mappings(session)
    await session.commit()

    return {
        "frameworks_loaded": 1,
        "controls_created": controls_created,
        "controls_updated": controls_updated,
        "mappings_created": mappings_created,
    }


async def _resolve_control_mappings(session: AsyncSession) -> int:
    result = await session.execute(select(Control))
    controls = list(result.scalars())
    index: dict[str, list[Control]] = {}
    for control in controls:
        index.setdefault(control.control_id, []).append(control)

    existing_result = await session.execute(
        select(
            control_mappings_association.c.control_id,
            control_mappings_association.c.mapped_control_id,
        )
    )
    existing_pairs = {(row[0], row[1]) for row in existing_result.all()}

    mappings_created = 0
    for control in controls:
        maps_to: dict[str, list[str]] = control.maps_to or {}
        for _, target_ids in maps_to.items():
            for target_control_id in target_ids:
                targets = index.get(target_control_id, [])
                for target in targets:
                    if target.id == control.id:
                        continue
                    pair = (control.id, target.id)
                    if pair in existing_pairs:
                        continue
                    await session.execute(
                        insert(control_mappings_association).values(
                            control_id=control.id,
                            mapped_control_id=target.id,
                        )
                    )
                    existing_pairs.add(pair)
                    mappings_created += 1
    return mappings_created

