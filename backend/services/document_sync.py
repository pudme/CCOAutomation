from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from models.compliance import DataImport


def normalize_sync_name(value: str) -> str:
    return str(value or "").strip().lower()


def normalize_library(value: str | None) -> str:
    return (value or "main").strip().lower() or "main"


async def classify_sync_files(
    session: AsyncSession,
    *,
    files: list[dict[str, Any]],
    library: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Classify files as new/modified/unchanged against DataImport (filename + content hash/size).

    Shared by documents folder-sync preview/sync and the evidence drop watcher.
    """
    from sqlalchemy import select

    imports_all = list(
        (await session.execute(select(DataImport).order_by(DataImport.created_at.desc()))).scalars()
    )
    library_norm = normalize_library(library)
    imports = [row for row in imports_all if normalize_library(row.library) == library_norm]
    by_name: dict[str, DataImport] = {}
    by_name_main: dict[str, DataImport] = {}
    for row in imports:
        key = normalize_sync_name(row.filename)
        if key and key not in by_name:
            by_name[key] = row
    if library_norm == "dpa":
        for row in imports_all:
            if normalize_library(row.library) != "main":
                continue
            key = normalize_sync_name(row.filename)
            if key and key not in by_name_main:
                by_name_main[key] = row

    actions: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "total_scanned": len(files),
        "new": 0,
        "modified": 0,
        "unchanged": 0,
        "skipped": 0,
        "new_files": [],
        "modified_files": [],
        "new_details": [],
        "modified_details": [],
        "errors": [],
        "main_library_collisions": [],
    }
    for file in files:
        filename = str(file.get("filename") or "").strip()
        if not filename:
            summary["skipped"] += 1
            summary["errors"].append("Missing filename")
            continue
        file_size = int(file.get("size") or 0)
        if library_norm == "dpa" and normalize_sync_name(filename) in by_name_main:
            summary["main_library_collisions"].append(filename)
        incoming_hash = str(file.get("content_hash") or "").strip().lower() or None
        existing = by_name.get(normalize_sync_name(filename))
        if existing is None:
            summary["new"] += 1
            summary["new_files"].append(filename)
            actions.append({"mode": "new", "filename": filename, "size": file_size, "existing": None})
            continue

        existing_hash = (existing.content_hash or "").strip().lower() or None
        if existing_hash and incoming_hash:
            if existing_hash == incoming_hash:
                summary["unchanged"] += 1
                actions.append(
                    {
                        "mode": "unchanged",
                        "filename": filename,
                        "size": file_size,
                        "content_hash": incoming_hash,
                        "existing": existing,
                    }
                )
                continue
        elif existing.file_size is not None and int(existing.file_size) == file_size:
            summary["unchanged"] += 1
            actions.append(
                {"mode": "unchanged", "filename": filename, "size": file_size, "existing": existing}
            )
            continue

        summary["modified"] += 1
        summary["modified_files"].append(filename)
        actions.append(
            {
                "mode": "modified",
                "filename": filename,
                "size": file_size,
                "content_hash": incoming_hash,
                "existing": existing,
            }
        )

    if summary["total_scanned"] > 0 and summary["new"] == 0 and summary["modified"] == 0:
        summary["up_to_date"] = True
    return summary, actions
