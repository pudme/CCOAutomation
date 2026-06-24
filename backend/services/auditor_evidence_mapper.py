from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any

import chromadb
from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai.gateway import MODEL_HAIKU, call_claude, is_daily_limit_exception
from config import get_settings
from database import AsyncSessionLocal
from models.auditor import AuditorChecklist, AuditorChecklistItem, AuditorItemStatus
from models.compliance import DataImport, EvidenceItem
from services.change_log import log_change

settings = get_settings()
_RUNNING_CHECKLISTS: set[int] = set()
_RATE_LIMIT_MESSAGE = "Evidence refresh paused — API rate limit reached. This will retry automatically in a few minutes."
_STOPWORDS = {
    "and",
    "the",
    "for",
    "with",
    "from",
    "that",
    "this",
    "these",
    "those",
    "submit",
    "provide",
    "supply",
    "records",
    "documentation",
    "document",
    "evidence",
    "program",
    "process",
    "processes",
}
_DPA_CONTROL_PATTERN = re.compile(r"\bDPA\.(\d{1,2})\b", re.IGNORECASE)
_DPA_REQUEST_PATTERN = re.compile(r"\bDPA[-_\s]*0*(\d{1,2})\b", re.IGNORECASE)
_DPA_INFRA_TOKENS = {
    "intune",
    "ninjaone",
    "mfa",
    "offboarding",
    "azure rbac",
    "rbac",
    "entra",
    "iam users",
    "iam roles",
    "device inventory",
}
_DPA_INFRA_BLOCKED_CONTROLS = {"DPA.1", "DPA.10", "DPA.12", "DPA.15", "DPA.23"}
_DPA_OBLIGATION_TOPICS = {
    "DPA.1": ["leadership", "commitment", "management", "board", "executive", "tone", "compliance"],
    "DPA.2": ["culture", "ethics", "management", "compliance", "employees"],
    "DPA.3": ["risk", "assessment", "bribery", "fraud", "identify"],
    "DPA.4": ["risk", "program", "compliance", "modify", "design"],
    "DPA.5": ["policy", "anti-fraud", "anti-bribery", "code of conduct", "written"],
    "DPA.6": ["policy", "personnel", "employees", "agents", "partners", "distribution"],
    "DPA.7": ["internal controls", "books", "records", "financial", "accuracy", "segregation"],
    "DPA.8": ["policy", "review", "update", "version", "periodic"],
    "DPA.9": ["oversight", "executive", "cco", "board", "autonomy", "authority"],
    "DPA.10": ["training", "awareness", "anti-fraud", "curriculum", "completion"],
    "DPA.11": ["guidance", "advice", "helpline", "counsel", "escalation"],
    "DPA.12": ["reporting", "whistleblower", "hotline", "confidential", "retaliation"],
    "DPA.13": ["investigation", "misconduct", "allegations", "process", "resources"],
    "DPA.14": ["compensation", "bonus", "incentive", "performance", "criteria"],
    "DPA.15": ["disciplinary", "discipline", "termination", "consequences", "violations"],
    "DPA.16": ["due diligence", "third-party", "vendor", "partners", "agents", "oversight"],
    "DPA.17": ["monitoring", "third-party", "certification", "ongoing", "annual"],
    "DPA.18": ["contract", "agreement", "provisions", "audit rights", "termination"],
    "DPA.19": ["merger", "acquisition", "m&a", "due diligence", "new entity"],
    "DPA.20": ["integration", "acquired", "merged", "onboarding", "erp"],
    "DPA.21": ["testing", "review", "audit", "effectiveness", "program"],
    "DPA.22": ["data access", "monitoring", "transactions", "compliance personnel"],
    "DPA.23": ["root cause", "remediation", "misconduct", "bribery", "investigation", "britt", "usaid"],
}


def _normalize_library(value: str | None) -> str:
    return (value or "main").strip().lower() or "main"


def _is_nonempty_text(value: str | None) -> bool:
    return bool(value and value.strip())


def _readable_summary_from_filename(filename: str) -> str:
    base = re.sub(r"\.[A-Za-z0-9]+$", "", str(filename or "").strip())
    normalized = re.sub(r"[_\-]+", " ", base)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or "Compliance evidence document"


def _clean_claude_json_response(raw_text: str) -> str:
    cleaned = str(raw_text or "").strip()
    # Strip markdown code fences if present.
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 1)[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    # Also handle closing fence.
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
    return cleaned


def _normalize_generated_response(raw_text: str) -> str:
    text = str(raw_text or "").replace("\r", "\n").strip()
    text = re.sub(r"```[a-zA-Z0-9_-]*", "", text)
    text = text.replace("```", "")
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"\1", text)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned_lines: list[str] = []
    for line in lines:
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"^>\s*", "", line)
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"^\d+[\.\)]\s+", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        cleaned_lines.append(line)
    normalized = " ".join(cleaned_lines)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


async def _claude_rank_evidence(
    description: str,
    candidates: list[dict[str, Any]],
    *,
    bypass_limit: bool = False,
) -> list[dict[str, Any]]:
    candidate_lines = "\n".join(
        [
            f"- {str(candidate.get('filename') or '')} | {str(candidate.get('summary') or candidate.get('description') or '')[:220]}"
            for candidate in candidates
        ]
    )

    prompt = (
        f"An auditor is requesting: {description}\n\n"
        "These documents are in our compliance system for a surveillance audit covering ISO 27001, ISO 20000, and ISO 9001.\n"
        "Be generous - if a document provides ANY evidence relevant to this request, even partially, mark it partial or yes.\n\n"
        "Documents:\n"
        f"{candidate_lines}\n\n"
        'Return ONLY a valid JSON object with no markdown, no code fences, no explanation. Use this exact format:\n'
        '{"results": [{"filename": "exact filename here", "relevance": "yes", "reason": "why it matches"}]}\n'
        "relevance must be exactly one of: yes, partial, no"
    )
    response = await call_claude(
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
        model=MODEL_HAIKU,
        bypass_limit=bypass_limit,
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    text = _clean_claude_json_response(text)

    try:
        payload = json.loads(text)
        results = payload.get("results") or []
        return results
    except json.JSONDecodeError as exc:
        logger.error(
            "JSON parse failed in _claude_rank_evidence: {} | raw: {}",
            exc,
            text[:200],
        )
        return []


async def _claude_generate_auditor_response(
    description: str,
    matched_documents: list[dict[str, str]],
    *,
    bypass_limit: bool = False,
) -> str:
    if not matched_documents:
        return ""
    document_lines = "\n".join(
        [
            f"- {str(doc.get('filename') or '')} | {str(doc.get('summary') or '')[:260]}"
            for doc in matched_documents
        ]
    )
    prompt = (
        f"An auditor requested: {description}\n"
        f"We have identified the following documents as evidence:\n{document_lines}\n\n"
        "Write a concise professional response (3-5 sentences) to the auditor explaining how these documents satisfy their request. "
        "Write in present tense - describe what the documents demonstrate and establish, not what was provided or submitted. "
        "Use first person as the compliance officer. Do not use bullet points. "
        "Do not use past tense phrases like 'we have provided', 'we submitted', or 'we have demonstrated' - instead use present tense like "
        "'our policy establishes', 'this document demonstrates', 'the evidence shows'. Return plain text only (no markdown headings)."
    )
    response = await call_claude(
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
        model=MODEL_HAIKU,
        bypass_limit=bypass_limit,
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return _normalize_generated_response(text)


def _is_rate_limit_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "429" in message or "rate limit" in message or "ratelimit" in message


def _is_daily_limit_error(exc: Exception) -> bool:
    return is_daily_limit_exception(exc)


def _search_chroma(query: str, *, n_results: int = 20) -> list[dict[str, Any]]:
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    candidates: list[dict[str, Any]] = []
    try:
        collection = client.get_or_create_collection("compliance_docs")
        result = collection.query(query_texts=[query], n_results=n_results)
        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        for idx, doc_id in enumerate(ids):
            meta = metas[idx] if idx < len(metas) and isinstance(metas[idx], dict) else {}
            filename = str(meta.get("filename") or doc_id)
            summary = str(meta.get("analysis_summary") or meta.get("summary") or "")[:300]
            description = str(docs[idx])[:200] if idx < len(docs) else ""
            candidates.append(
                {
                    "filename": filename,
                    "summary": summary or description,
                    "document_id": str(doc_id),
                }
            )
    except Exception:  # noqa: BLE001
        return []
    dedup: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        key = str(candidate.get("filename") or "").strip().lower()
        if key and key not in dedup:
            dedup[key] = candidate
    return list(dedup.values())[:n_results]


def _extract_item_title(description: str) -> str:
    text = str(description or "").strip()
    if ":" in text:
        head = text.split(":", 1)[0].strip()
        if 3 <= len(head) <= 80:
            return head
    return " ".join(text.split()[:8]).strip()


def _extract_keyword_query(description: str) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{2,}", str(description or "").lower())
    terms: list[str] = []
    for word in words:
        if word in _STOPWORDS:
            continue
        if word not in terms:
            terms.append(word)
        if len(terms) >= 6:
            break
    return " ".join(terms)


def _dedupe_candidates(candidates: list[dict[str, Any]], *, limit: int = 40) -> list[dict[str, Any]]:
    dedup: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        key = str(candidate.get("filename") or "").strip().lower()
        if not key:
            continue
        if key not in dedup:
            dedup[key] = candidate
            continue
        # Prefer richer summary if duplicate appears from another retrieval method.
        existing_summary = str(dedup[key].get("summary") or "")
        incoming_summary = str(candidate.get("summary") or "")
        if len(incoming_summary) > len(existing_summary):
            dedup[key] = candidate
    return list(dedup.values())[:limit]


def _title_keywords(title: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{1,}", title.lower())
    tokens: list[str] = []
    for token in raw_tokens:
        if token in _STOPWORDS or len(token) < 3:
            continue
        if token not in tokens:
            tokens.append(token)
    if "internal" in title.lower() and "audit" in title.lower() and "r17" not in tokens:
        tokens.append("r17")
    return tokens[:8]


async def _search_evidence_by_filename_keywords(
    session: AsyncSession,
    description: str,
    *,
    library: str,
    limit: int = 40,
) -> list[dict[str, Any]]:
    title = _extract_item_title(description)
    keywords = _title_keywords(title)
    if not keywords:
        return []
    clauses = [func.lower(EvidenceItem.filename).ilike(f"%{keyword.lower()}%") for keyword in keywords]
    rows = list(
        (
            await session.execute(
                select(EvidenceItem)
                .where(or_(*clauses))
                .where(EvidenceItem.library == library)
                .order_by(EvidenceItem.id.desc())
                .limit(limit)
            )
        ).scalars()
    )
    return [
        {
            "filename": row.filename,
            "summary": str(row.analysis_summary or "")[:300],
            "document_id": str(row.id),
        }
        for row in rows
    ]


async def _hydrate_candidate_summaries(
    session: AsyncSession,
    candidates: list[dict[str, Any]],
    *,
    library: str,
) -> list[dict[str, Any]]:
    filenames = [
        str(candidate.get("filename") or "").strip()
        for candidate in candidates
        if str(candidate.get("filename") or "").strip()
    ]
    if not filenames:
        return []
    lower_filenames = sorted({name.lower() for name in filenames})

    evidence_rows = (
        await session.execute(
            select(EvidenceItem.filename, EvidenceItem.analysis_summary).where(
                func.lower(EvidenceItem.filename).in_(lower_filenames),
                EvidenceItem.library == library,
            )
        )
    ).all()
    evidence_summary_by_filename: dict[str, str] = {}
    for filename, analysis_summary in evidence_rows:
        key = str(filename or "").strip().lower()
        summary = str(analysis_summary or "").strip()
        if key and _is_nonempty_text(summary):
            evidence_summary_by_filename[key] = summary

    import_rows = (
        await session.execute(
            select(DataImport.filename, DataImport.identified_summary).where(
                func.lower(DataImport.filename).in_(lower_filenames),
                DataImport.library == library,
            )
        )
    ).all()
    import_summary_by_filename: dict[str, str] = {}
    for filename, identified_summary in import_rows:
        key = str(filename or "").strip().lower()
        summary = str(identified_summary or "").strip()
        if key and _is_nonempty_text(summary):
            import_summary_by_filename[key] = summary

    hydrated: list[dict[str, Any]] = []
    for candidate in candidates:
        filename = str(candidate.get("filename") or "").strip()
        if not filename:
            continue
        key = filename.lower()
        summary = (
            evidence_summary_by_filename.get(key)
            or import_summary_by_filename.get(key)
            or _readable_summary_from_filename(filename)
        )
        hydrated.append(
            {
                **candidate,
                "filename": filename,
                "summary": summary,
            }
        )
    return hydrated


async def _collect_candidates_for_item(
    session: AsyncSession,
    description: str,
    *,
    library: str,
    n_results: int = 40,
) -> list[dict[str, Any]]:
    title = _extract_item_title(description)
    query_1 = str(description or "").strip()[:100]
    query_2 = title
    query_3 = _extract_keyword_query(description)
    semantic_candidates: list[dict[str, Any]] = []
    for query in [query_1, query_2, query_3]:
        if not query:
            continue
        semantic_candidates.extend(_search_chroma(query, n_results=n_results))
    filename_candidates = await _search_evidence_by_filename_keywords(
        session,
        description,
        library=library,
        limit=n_results,
    )
    deduped = _dedupe_candidates(semantic_candidates + filename_candidates, limit=n_results)
    return await _hydrate_candidate_summaries(session, deduped, library=library)


async def _resolve_evidence_id_by_filename(
    session: AsyncSession,
    filename: str,
    *,
    library: str,
) -> int | None:
    row = (
        await session.execute(
            select(EvidenceItem.id)
            .where(
                func.lower(EvidenceItem.filename) == filename.strip().lower(),
                EvidenceItem.library == library,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return int(row) if row is not None else None


async def _checklist_context(session: AsyncSession, checklist: AuditorChecklist) -> tuple[str, bool]:
    library = "main"
    if checklist.source_import_id is not None:
        source_import = (
            await session.execute(select(DataImport).where(DataImport.id == checklist.source_import_id))
        ).scalars().first()
        if source_import is not None:
            library = _normalize_library(source_import.library)
    audit_type_text = str(checklist.audit_type or "").lower()
    framework_text = str(checklist.framework or "").lower()
    name_text = str(checklist.name or "").lower()
    is_dpa_context = library == "dpa" or "dpa" in audit_type_text or "dpa" in framework_text or "dpa" in name_text
    return library, is_dpa_context


def _extract_dpa_control_for_item(item: AuditorChecklistItem) -> str | None:
    for value in item.control_ids or []:
        control_id = str(value or "").strip().upper()
        if control_id.startswith("DPA."):
            return control_id
    combined_text = f"{item.item_number or ''} {item.description or ''}"
    request_match = _DPA_REQUEST_PATTERN.search(combined_text)
    if request_match:
        number = int(request_match.group(1))
        if 1 <= number <= 23:
            return f"DPA.{number}"
    control_match = _DPA_CONTROL_PATTERN.search(combined_text)
    if control_match:
        number = int(control_match.group(1))
        if 1 <= number <= 23:
            return f"DPA.{number}"
    return None


def _candidate_matches_dpa_topics(candidate: dict[str, Any], topic_keywords: list[str]) -> bool:
    if not topic_keywords:
        return True
    haystack = (
        f"{str(candidate.get('filename') or '')} "
        f"{str(candidate.get('summary') or candidate.get('description') or '')}"
    ).lower()
    return any(keyword.lower() in haystack for keyword in topic_keywords)


def _candidate_looks_like_it_infra(candidate: dict[str, Any]) -> bool:
    haystack = (
        f"{str(candidate.get('filename') or '')} "
        f"{str(candidate.get('summary') or candidate.get('description') or '')}"
    ).lower()
    return any(token in haystack for token in _DPA_INFRA_TOKENS)


def _filter_dpa_candidates_for_item(
    item: AuditorChecklistItem,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    control_id = _extract_dpa_control_for_item(item)
    topic_keywords = _DPA_OBLIGATION_TOPICS.get(str(control_id or "").upper(), [])
    filtered: list[dict[str, Any]] = []
    for candidate in candidates:
        if not _candidate_matches_dpa_topics(candidate, topic_keywords):
            continue
        if control_id in _DPA_INFRA_BLOCKED_CONTROLS and _candidate_looks_like_it_infra(candidate):
            continue
        filtered.append(candidate)
    return filtered


def _dedupe_documents_for_response(documents: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for doc in documents:
        filename = str(doc.get("filename") or "").strip()
        if not filename:
            continue
        key = filename.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(
            {
                "filename": filename,
                "summary": str(doc.get("summary") or "").strip(),
            }
        )
    return deduped


async def _generate_our_response_for_item(
    session: AsyncSession,
    item: AuditorChecklistItem,
    matched_documents: list[dict[str, str]],
    *,
    library: str,
    bypass_limit: bool = False,
) -> str:
    hydrated = await _hydrate_candidate_summaries(session, matched_documents, library=library)
    deduped = _dedupe_documents_for_response(hydrated)
    if not deduped:
        return ""
    response_text = await _claude_generate_auditor_response(
        str(item.description or ""),
        deduped,
        bypass_limit=bypass_limit,
    )
    return response_text.strip()


async def generate_response_for_single_item(
    checklist_id: int,
    item_id: int,
    session: AsyncSession,
    *,
    bypass_limit: bool = False,
) -> dict[str, Any]:
    checklist = (
        await session.execute(select(AuditorChecklist).where(AuditorChecklist.id == checklist_id))
    ).scalars().first()
    if checklist is None:
        raise ValueError("Checklist not found")
    checklist_library, is_dpa_context = await _checklist_context(session, checklist)
    response_library = "dpa" if is_dpa_context else checklist_library

    item = (
        await session.execute(
            select(AuditorChecklistItem).where(
                AuditorChecklistItem.checklist_id == checklist_id,
                AuditorChecklistItem.id == item_id,
            )
        )
    ).scalars().first()
    if item is None:
        raise ValueError("Checklist item not found")

    evidence_ids = [int(value) for value in (item.evidence_ids or []) if isinstance(value, int)]
    if not evidence_ids:
        return {"updated": False, "reason": "No evidence_ids on item", "item_id": item_id}

    evidence_rows = list(
        (
            await session.execute(
                select(EvidenceItem).where(EvidenceItem.id.in_(evidence_ids))
            )
        ).scalars()
    )
    documents = [
        {
            "filename": row.filename,
            "summary": str(row.analysis_summary or row.description or ""),
        }
        for row in evidence_rows
    ]
    response_text = await _generate_our_response_for_item(
        session,
        item,
        documents,
        library=response_library,
        bypass_limit=bypass_limit,
    )
    if not response_text:
        return {"updated": False, "reason": "Unable to generate response", "item_id": item_id}

    item.our_response = response_text
    await session.commit()
    return {"updated": True, "item_id": item_id, "our_response": response_text}


async def _semantic_match_checklist(
    checklist_id: int,
    session: AsyncSession,
    *,
    bypass_limit: bool = False,
) -> dict[str, int]:
    checklist = (
        await session.execute(select(AuditorChecklist).where(AuditorChecklist.id == checklist_id))
    ).scalars().first()
    if checklist is None:
        return {"requests_with_evidence": 0, "total_items": 0}
    checklist_library, is_dpa_context = await _checklist_context(session, checklist)
    checklist.evidence_refresh_status = "in_progress"
    checklist.evidence_refresh_error = None
    await session.commit()
    items = list(
        (
            await session.execute(
                select(AuditorChecklistItem).where(
                    AuditorChecklistItem.checklist_id == checklist_id,
                    AuditorChecklistItem.status.in_(
                        [AuditorItemStatus.OPEN, AuditorItemStatus.IN_PROGRESS]
                    ),
                )
            )
        ).scalars()
    )
    requests_with_evidence = 0
    try:
        for item in items:
            description = str(item.description or "").strip()
            primary_library = "dpa" if is_dpa_context else checklist_library
            candidates = await _collect_candidates_for_item(
                session,
                description,
                library=primary_library,
                n_results=40,
            )
            if is_dpa_context:
                candidates = _filter_dpa_candidates_for_item(item, candidates)
            if not candidates and not (is_dpa_context and primary_library == "dpa"):
                item.evidence_ids = []
                item.evidence_mapping = {"results": []}
                item.status = AuditorItemStatus.OPEN
                continue
            ranking = (
                await _claude_rank_evidence(
                    description,
                    candidates,
                    bypass_limit=bypass_limit,
                )
                if candidates
                else []
            )
            yes_ids: list[int] = []
            partial_ids: list[int] = []
            normalized_results: list[dict[str, Any]] = []
            matched_documents: list[dict[str, str]] = []
            for result in ranking:
                filename = str(result.get("filename") or "").strip()
                relevance = str(result.get("relevance") or "no").lower().strip()
                reason = str(result.get("reason") or "")
                evidence_id = None
                if relevance in {"yes", "partial"} and filename:
                    evidence_id = await _resolve_evidence_id_by_filename(
                        session,
                        filename,
                        library=primary_library,
                    )
                    if evidence_id is not None:
                        matched_summary = next(
                            (
                                str(candidate.get("summary") or "")
                                for candidate in candidates
                                if str(candidate.get("filename") or "").strip().lower() == filename.lower()
                            ),
                            "",
                        )
                        matched_documents.append(
                            {
                                "filename": filename,
                                "summary": matched_summary,
                                "library_source": primary_library,
                            }
                        )
                        if relevance == "yes":
                            yes_ids.append(evidence_id)
                        else:
                            partial_ids.append(evidence_id)
                normalized_results.append(
                    {
                        "filename": filename,
                        "relevance": relevance,
                        "reason": reason,
                        "evidence_id": evidence_id,
                        "library_source": primary_library,
                        "source_label": "DPA Evidence" if primary_library == "dpa" else "Main Library",
                    }
                )
            has_primary_match = bool(yes_ids or partial_ids)
            if is_dpa_context and not has_primary_match:
                supplementary_candidates = await _collect_candidates_for_item(
                    session,
                    description,
                    library="main",
                    n_results=40,
                )
                supplementary_candidates = _filter_dpa_candidates_for_item(item, supplementary_candidates)
                if supplementary_candidates:
                    supplementary_ranking = await _claude_rank_evidence(
                        description,
                        supplementary_candidates,
                        bypass_limit=bypass_limit,
                    )
                    for result in supplementary_ranking:
                        filename = str(result.get("filename") or "").strip()
                        relevance = str(result.get("relevance") or "no").lower().strip()
                        reason = str(result.get("reason") or "")
                        evidence_id = None
                        if relevance in {"yes", "partial"} and filename:
                            evidence_id = await _resolve_evidence_id_by_filename(
                                session,
                                filename,
                                library="main",
                            )
                            if evidence_id is not None:
                                matched_summary = next(
                                    (
                                        str(candidate.get("summary") or "")
                                        for candidate in supplementary_candidates
                                        if str(candidate.get("filename") or "").strip().lower() == filename.lower()
                                    ),
                                    "",
                                )
                                matched_documents.append(
                                    {
                                        "filename": filename,
                                        "summary": matched_summary,
                                        "library_source": "main",
                                    }
                                )
                                if relevance == "yes":
                                    yes_ids.append(evidence_id)
                                else:
                                    partial_ids.append(evidence_id)
                        normalized_results.append(
                            {
                                "filename": filename,
                                "relevance": relevance,
                                "reason": reason,
                                "evidence_id": evidence_id,
                                "library_source": "main",
                                "source_label": "Supplementary (Main Library)",
                            }
                        )
            item.evidence_mapping = {"results": normalized_results}
            new_evidence_ids = sorted(set(yes_ids + partial_ids))
            if yes_ids:
                new_status = AuditorItemStatus.EVIDENCE_SUBMITTED
                requests_with_evidence += 1
            elif partial_ids:
                new_status = AuditorItemStatus.IN_PROGRESS
                requests_with_evidence += 1
            else:
                new_status = AuditorItemStatus.OPEN
            item.evidence_ids = new_evidence_ids
            item.status = new_status
            if new_status in {AuditorItemStatus.EVIDENCE_SUBMITTED, AuditorItemStatus.IN_PROGRESS}:
                response_text = await _generate_our_response_for_item(
                    session,
                    item,
                    matched_documents,
                    library=primary_library,
                    bypass_limit=bypass_limit,
                )
                if response_text:
                    item.our_response = response_text

        response_items = list(
            (
                await session.execute(
                    select(AuditorChecklistItem).where(
                        AuditorChecklistItem.checklist_id == checklist_id,
                        AuditorChecklistItem.status.in_(
                            [AuditorItemStatus.EVIDENCE_SUBMITTED, AuditorItemStatus.IN_PROGRESS]
                        ),
                    )
                )
            ).scalars()
        )
        for response_item in response_items:
            if _is_nonempty_text(response_item.our_response):
                cleaned_existing = _normalize_generated_response(str(response_item.our_response or ""))
                if cleaned_existing and cleaned_existing != str(response_item.our_response or "").strip():
                    response_item.our_response = cleaned_existing
                continue
            evidence_ids = [
                int(value)
                for value in (response_item.evidence_ids or [])
                if isinstance(value, int)
            ]
            if not evidence_ids:
                continue
            evidence_rows = list(
                (
                    await session.execute(
                        select(EvidenceItem).where(EvidenceItem.id.in_(evidence_ids))
                    )
                ).scalars()
            )
            matched_documents = [
                {
                    "filename": row.filename,
                    "summary": str(row.analysis_summary or row.description or ""),
                }
                for row in evidence_rows
            ]
            response_text = await _generate_our_response_for_item(
                session,
                response_item,
                matched_documents,
                library=primary_library,
                bypass_limit=bypass_limit,
            )
            if response_text:
                response_item.our_response = response_text

        checklist.last_evidence_refresh = datetime.now(timezone.utc).isoformat()
        checklist.evidence_refresh_status = "complete"
        checklist.evidence_refresh_error = None
        await log_change(
            session,
            category="auditor",
            action="Evidence match run",
            subject=checklist.name,
            detail=f"Evidence matched: {requests_with_evidence}/{len(items)} auditor requests satisfied",
        )
        await session.commit()
        return {"requests_with_evidence": requests_with_evidence, "total_items": len(items)}
    except Exception as exc:  # noqa: BLE001
        if _is_rate_limit_error(exc) or _is_daily_limit_error(exc):
            checklist.evidence_refresh_status = "rate_limited"
            if _is_daily_limit_error(exc):
                checklist.evidence_refresh_error = (
                    "Evidence matching paused — daily API limit reached. "
                    "Use Run Anyway to continue."
                )
            else:
                checklist.evidence_refresh_error = _RATE_LIMIT_MESSAGE
        else:
            checklist.evidence_refresh_status = "failed"
            checklist.evidence_refresh_error = str(exc)[:1000]
        await session.commit()
        raise


async def _refresh_existing_matches(
    checklist_id: int,
    session: AsyncSession,
    *,
    bypass_limit: bool = False,
) -> dict[str, int]:
    checklist = (
        await session.execute(select(AuditorChecklist).where(AuditorChecklist.id == checklist_id))
    ).scalars().first()
    if checklist is None:
        return {"requests_with_evidence": 0, "total_items": 0}
    checklist_library, is_dpa_context = await _checklist_context(session, checklist)
    checklist.evidence_refresh_status = "in_progress"
    checklist.evidence_refresh_error = None
    await session.commit()
    items = list(
        (
            await session.execute(
                select(AuditorChecklistItem).where(AuditorChecklistItem.checklist_id == checklist_id)
            )
        ).scalars()
    )
    requests_with_evidence = 0
    try:
        for item in items:
            existing_ids = [int(value) for value in (item.evidence_ids or []) if isinstance(value, int)]
            if not existing_ids:
                item.status = AuditorItemStatus.OPEN
                continue
            evidence_rows = list(
                (
                    await session.execute(
                        select(EvidenceItem).where(EvidenceItem.id.in_(existing_ids))
                    )
                ).scalars()
            )
            candidates = [
                {
                    "filename": row.filename,
                    "summary": str(row.analysis_summary or row.description or "")[:220],
                    "document_id": row.id,
                    "library_source": _normalize_library(row.library),
                }
                for row in evidence_rows
            ]
            if is_dpa_context:
                candidates = _filter_dpa_candidates_for_item(item, candidates)
                if not candidates:
                    item.evidence_mapping = {"results": []}
                    item.evidence_ids = []
                    item.status = AuditorItemStatus.OPEN
                    continue
            ranking = await _claude_rank_evidence(
                str(item.description or ""),
                candidates,
                bypass_limit=bypass_limit,
            )
            yes_ids: list[int] = []
            partial_ids: list[int] = []
            normalized_results: list[dict[str, Any]] = []
            for result in ranking:
                filename = str(result.get("filename") or "").strip()
                relevance = str(result.get("relevance") or "no").lower().strip()
                reason = str(result.get("reason") or "")
                evidence_id = None
                library_source = str(
                    result.get("library_source")
                    or ("dpa" if is_dpa_context else checklist_library)
                )
                if relevance in {"yes", "partial"} and filename:
                    evidence_id = await _resolve_evidence_id_by_filename(
                        session,
                        filename,
                        library=_normalize_library(library_source),
                    )
                    if evidence_id is not None and evidence_id in existing_ids:
                        if relevance == "yes":
                            yes_ids.append(evidence_id)
                        else:
                            partial_ids.append(evidence_id)
                normalized_results.append(
                    {
                        "filename": filename,
                        "relevance": relevance,
                        "reason": reason,
                        "evidence_id": evidence_id,
                        "library_source": _normalize_library(library_source),
                        "source_label": (
                            "DPA Evidence" if _normalize_library(library_source) == "dpa" else "Supplementary (Main Library)"
                        ),
                    }
                )
            item.evidence_mapping = {"results": normalized_results}
            item.evidence_ids = sorted(set(yes_ids + partial_ids))
            if yes_ids:
                item.status = AuditorItemStatus.EVIDENCE_SUBMITTED
                requests_with_evidence += 1
            elif partial_ids:
                item.status = AuditorItemStatus.IN_PROGRESS
                requests_with_evidence += 1
            else:
                item.status = AuditorItemStatus.OPEN
        checklist.last_evidence_refresh = datetime.now(timezone.utc).isoformat()
        checklist.evidence_refresh_status = "complete"
        checklist.evidence_refresh_error = None
        await log_change(
            session,
            category="auditor",
            action="Evidence match run",
            subject=checklist.name,
            detail=f"Evidence matched: {requests_with_evidence}/{len(items)} auditor requests satisfied",
        )
        await session.commit()
        return {"requests_with_evidence": requests_with_evidence, "total_items": len(items)}
    except Exception as exc:  # noqa: BLE001
        if _is_rate_limit_error(exc) or _is_daily_limit_error(exc):
            checklist.evidence_refresh_status = "rate_limited"
            if _is_daily_limit_error(exc):
                checklist.evidence_refresh_error = (
                    "Evidence refresh paused — daily API limit reached. "
                    "Use Run Anyway to continue."
                )
            else:
                checklist.evidence_refresh_error = _RATE_LIMIT_MESSAGE
        else:
            checklist.evidence_refresh_status = "failed"
            checklist.evidence_refresh_error = str(exc)[:1000]
        await session.commit()
        raise


async def run_mapping_job(
    checklist_id: int,
    *,
    mode: str = "semantic_match",
    bypass_limit: bool = False,
) -> None:
    if checklist_id in _RUNNING_CHECKLISTS:
        return
    _RUNNING_CHECKLISTS.add(checklist_id)
    try:
        delay_seconds = 60
        for attempt in range(5):
            try:
                async with AsyncSessionLocal() as session:
                    if mode == "refresh_existing":
                        await _refresh_existing_matches(checklist_id, session, bypass_limit=bypass_limit)
                    else:
                        await _semantic_match_checklist(checklist_id, session, bypass_limit=bypass_limit)
                return
            except Exception as exc:  # noqa: BLE001
                if _is_rate_limit_error(exc) and attempt < 4:
                    await asyncio.sleep(delay_seconds)
                    delay_seconds *= 2
                    continue
                raise
    finally:
        _RUNNING_CHECKLISTS.discard(checklist_id)


def trigger_mapping_job(checklist_id: int) -> bool:
    return trigger_mapping_job_with_mode(checklist_id, mode="semantic_match", bypass_limit=False)


def trigger_mapping_job_with_mode(
    checklist_id: int,
    *,
    mode: str,
    bypass_limit: bool,
) -> bool:
    if checklist_id in _RUNNING_CHECKLISTS:
        return False
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    asyncio.create_task(run_mapping_job(checklist_id, mode=mode, bypass_limit=bypass_limit))
    return True
