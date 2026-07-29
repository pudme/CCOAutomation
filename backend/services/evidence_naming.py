from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.compliance import Control, EvidenceControlLink, EvidenceItem, EvidenceType, Framework


def _slugify(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[_\s]+", " ", text)
    text = re.sub(r"[^a-z0-9.\- ]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "document"


def build_evidence_display_name(
    *,
    id_token: str,
    evidence_type: EvidenceType | str,
    collected_date: str | None,
    slug_source: str,
    original_filename: str,
) -> str:
    """Format: {id_token} {evidence_type} {collected_date} {slug}.ext — spaces, no underscores."""
    type_value = evidence_type.value if isinstance(evidence_type, EvidenceType) else str(evidence_type)
    type_token = _slugify(type_value.replace("_", " "))
    date_token = (collected_date or "undated").strip() or "undated"
    slug = _slugify(slug_source)
    ext = Path(original_filename or "").suffix.lower() or ".bin"
    id_clean = str(id_token or "unknown").replace("_", " ").strip()
    return f"{id_clean} {type_token} {date_token} {slug}{ext}"


def _collision_suffix(base_name: str, existing: set[str], *, force_numbered: bool = False) -> str:
    """Append ' (2)', ' (3)', … when base collides — or whenever force_numbered (prior type+date name exists)."""
    existing_lower = {name.lower() for name in existing}
    if not force_numbered and base_name.lower() not in existing_lower:
        return base_name
    stem = Path(base_name).stem
    ext = Path(base_name).suffix
    n = 2
    while True:
        candidate = f"{stem} ({n}){ext}"
        if candidate.lower() not in existing_lower:
            return candidate
        n += 1


async def _existing_display_names_for_control(
    session: AsyncSession,
    control_db_id: int,
    *,
    evidence_type: EvidenceType,
    collected_date: str | None,
) -> set[str]:
    """Names already used on this control for the same evidence_type + collected_date."""
    rows = list(
        (
            await session.execute(
                select(EvidenceControlLink)
                .join(EvidenceItem, EvidenceControlLink.evidence_id == EvidenceItem.id)
                .where(EvidenceControlLink.control_id == control_db_id)
            )
        ).scalars()
    )
    names: set[str] = set()
    date_token = (collected_date or "undated").strip() or "undated"
    type_token = _slugify(evidence_type.value.replace("_", " "))
    for link in rows:
        name = (link.display_name or "").strip()
        if not name:
            continue
        # Same type+date collision space: name contains " {type} {date} "
        needle = f" {type_token} {date_token} "
        if needle.lower() in f" {name.lower()} ":
            names.add(name)
    return names


async def assign_display_names_after_link(
    session: AsyncSession,
    evidence: EvidenceItem,
    *,
    subject_name: str | None,
    summary: str | None,
    library: str,
) -> list[dict[str, Any]]:
    """Generate per-control display_name on EvidenceControlLink rows; fallback to EvidenceItem.display_name."""
    await session.refresh(evidence, attribute_names=["control_links"])
    links = list(evidence.control_links or [])
    slug_source = (subject_name or summary or Path(evidence.filename).stem or "document").strip()
    results: list[dict[str, Any]] = []

    if links:
        evidence.display_name = None
        for link in links:
            control = (
                await session.execute(
                    select(Control)
                    .options(selectinload(Control.framework))
                    .where(Control.id == link.control_id)
                )
            ).scalar_one_or_none()
            if control is None:
                continue
            base = build_evidence_display_name(
                id_token=control.control_id,
                evidence_type=evidence.evidence_type,
                collected_date=evidence.collected_date,
                slug_source=slug_source,
                original_filename=evidence.filename,
            )
            existing = await _existing_display_names_for_control(
                session,
                control.id,
                evidence_type=evidence.evidence_type,
                collected_date=evidence.collected_date,
            )
            # Exclude this link's current name from collision set when regenerating
            if link.display_name:
                existing.discard(link.display_name)
            # Spec: any prior display_name on this control for same type+date → (2)/(3)
            final_name = _collision_suffix(base, existing, force_numbered=bool(existing))
            link.display_name = final_name
            results.append(
                {
                    "control_id": control.control_id,
                    "framework": control.framework.short_name if control.framework else None,
                    "display_name": final_name,
                }
            )
        await session.flush()
        return results

    # Zero controls matched — name on EvidenceItem.display_name (keep filename for DataImport match)
    id_token = (library or "main").strip() or "main"
    base = build_evidence_display_name(
        id_token=id_token,
        evidence_type=evidence.evidence_type,
        collected_date=evidence.collected_date,
        slug_source=slug_source,
        original_filename=evidence.filename,
    )
    other_names = {
        str(name)
        for name in (
            await session.execute(
                select(EvidenceItem.display_name).where(
                    EvidenceItem.display_name.is_not(None),
                    EvidenceItem.id != evidence.id,
                )
            )
        ).scalars()
        if name
    }
    evidence.display_name = _collision_suffix(base, other_names)
    await session.flush()
    results.append({"control_id": None, "framework": id_token, "display_name": evidence.display_name})
    return results
