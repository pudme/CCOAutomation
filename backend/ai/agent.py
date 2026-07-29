from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai.gateway import MODEL_SONNET, call_claude
from ai.prompts import build_system_prompt
from ai import tools as agent_tools
from config import get_settings
from models.compliance import Conversation, Message

ToolFn = Callable[..., Any]


TOOL_REGISTRY: dict[str, ToolFn] = {
    "search_documents": agent_tools.search_documents,
    "get_control": agent_tools.get_control,
    "get_framework_gaps": agent_tools.get_framework_gaps,
    "get_open_findings": agent_tools.get_open_findings,
    "get_personnel_exceptions": agent_tools.get_personnel_exceptions,
    "run_personnel_check": agent_tools.run_personnel_check_tool,
    "get_obligations_due": agent_tools.get_obligations_due,
    "list_obligations": agent_tools.list_obligations,
    "create_obligation": agent_tools.create_obligation,
    "get_framework_detail": agent_tools.get_framework_detail,
    "get_import_history": agent_tools.get_import_history,
    "get_auditor_checklist": agent_tools.get_auditor_checklist,
    "update_checklist_item": agent_tools.update_checklist_item,
    "get_unsatisfied_auditor_items": agent_tools.get_unsatisfied_auditor_items,
    "update_control_status": agent_tools.update_control_status,
    "add_evidence": agent_tools.add_evidence,
    "create_finding": agent_tools.create_finding,
    "update_finding": agent_tools.update_finding,
    "add_corrective_action": agent_tools.add_corrective_action,
    "update_obligation": agent_tools.update_obligation,
    "ingest_notion_page": agent_tools.ingest_notion_page,
    "ingest_text": agent_tools.ingest_text,
    "generate_gap_report": agent_tools.generate_gap_report,
    "generate_scorecard": agent_tools.generate_scorecard,
    "generate_audit_package": agent_tools.generate_audit_package,
    "generate_corrective_action_report": agent_tools.generate_corrective_action_report,
    "get_staffing_gaps": agent_tools.get_staffing_gaps,
    "check_overcommitment": agent_tools.check_overcommitment,
    "flag_staffing_gap": agent_tools.flag_staffing_gap,
    "assign_staff": agent_tools.assign_staff,
}


ANTHROPIC_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"name": "search_documents", "description": "Semantic search across compliance documents", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "get_control", "description": "Get control details", "input_schema": {"type": "object", "properties": {"control_id": {"type": "string"}, "framework": {"type": "string"}}, "required": ["control_id", "framework"]}},
    {"name": "get_framework_gaps", "description": "Get framework gaps", "input_schema": {"type": "object", "properties": {"framework_short_name": {"type": "string"}}, "required": ["framework_short_name"]}},
    {"name": "get_open_findings", "description": "List open findings", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_personnel_exceptions", "description": "List personnel exceptions", "input_schema": {"type": "object", "properties": {}}},
    {"name": "run_personnel_check", "description": "Run personnel compliance checks", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_obligations_due", "description": "Obligations due within N days", "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}}, "required": ["days"]}},
    {"name": "list_obligations", "description": "List obligations", "input_schema": {"type": "object", "properties": {"status": {"type": "string"}}}},
    {"name": "create_obligation", "description": "Create obligation", "input_schema": {"type": "object", "properties": {"source": {"type": "string"}, "description": {"type": "string"}, "owner": {"type": ["string", "null"]}, "due_date": {"type": ["string", "null"]}, "cadence": {"type": ["string", "null"]}, "status": {"type": "string"}}, "required": ["source", "description"]}},
    {"name": "get_framework_detail", "description": "Get framework detail rollup", "input_schema": {"type": "object", "properties": {"framework": {"type": "string"}}, "required": ["framework"]}},
    {"name": "get_import_history", "description": "Get recent import history", "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    {"name": "get_auditor_checklist", "description": "Get full auditor checklist details", "input_schema": {"type": "object", "properties": {"checklist_id": {"type": "integer"}}, "required": ["checklist_id"]}},
    {"name": "update_checklist_item", "description": "Update auditor checklist item status/response", "input_schema": {"type": "object", "properties": {"item_id": {"type": "integer"}, "status": {"type": "string"}, "response": {"type": "string"}}, "required": ["item_id", "status", "response"]}},
    {"name": "get_unsatisfied_auditor_items", "description": "List all open/in-progress auditor items", "input_schema": {"type": "object", "properties": {}}},
    {"name": "update_control_status", "description": "Update control status", "input_schema": {"type": "object", "properties": {"control_id": {"type": "string"}, "framework": {"type": "string"}, "status": {"type": "string"}, "notes": {"type": "string"}}, "required": ["control_id", "framework", "status", "notes"]}},
    {"name": "add_evidence", "description": "Add evidence to controls", "input_schema": {"type": "object", "properties": {"control_ids": {"type": "array", "items": {"type": "string"}}, "filename": {"type": "string"}, "evidence_type": {"type": "string"}, "description": {"type": "string"}, "entity": {"type": ["string", "null"]}}, "required": ["control_ids", "filename", "evidence_type", "description"]}},
    {"name": "create_finding", "description": "Create a finding", "input_schema": {"type": "object", "properties": {"control_ids": {"type": "array", "items": {"type": "string"}}, "framework": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, "severity": {"type": "string"}}, "required": ["control_ids", "framework", "title", "description", "severity"]}},
    {"name": "update_finding", "description": "Update a finding", "input_schema": {"type": "object", "properties": {"finding_id": {"type": "string"}, "status": {"type": "string"}, "notes": {"type": "string"}}, "required": ["finding_id", "status", "notes"]}},
    {"name": "add_corrective_action", "description": "Add corrective action", "input_schema": {"type": "object", "properties": {"finding_id": {"type": "string"}, "description": {"type": "string"}, "owner": {"type": "string"}, "due_date": {"type": "string"}}, "required": ["finding_id", "description", "owner", "due_date"]}},
    {"name": "update_obligation", "description": "Update obligation", "input_schema": {"type": "object", "properties": {"obligation_id": {"type": "string"}, "status": {"type": "string"}, "notes": {"type": "string"}}, "required": ["obligation_id", "status", "notes"]}},
    {"name": "ingest_notion_page", "description": "Ingest a Notion page", "input_schema": {"type": "object", "properties": {"page_url": {"type": "string"}}, "required": ["page_url"]}},
    {"name": "ingest_text", "description": "Ingest free-form text", "input_schema": {"type": "object", "properties": {"content": {"type": "string"}, "source_label": {"type": "string"}}, "required": ["content", "source_label"]}},
    {"name": "generate_gap_report", "description": "Generate gap report", "input_schema": {"type": "object", "properties": {"framework": {"type": "string"}, "format": {"type": "string"}}, "required": ["framework", "format"]}},
    {"name": "generate_scorecard", "description": "Generate management scorecard", "input_schema": {"type": "object", "properties": {}}},
    {"name": "generate_audit_package", "description": "Generate audit package", "input_schema": {"type": "object", "properties": {"framework": {"type": "string"}}, "required": ["framework"]}},
    {"name": "generate_corrective_action_report", "description": "Generate corrective action report", "input_schema": {"type": "object", "properties": {}}},
    {
        "name": "get_staffing_gaps",
        "description": "Run workforce gap analysis for a pursuit (or all pursuits). Matches required labor categories and clearance against Apprio staff with utilization under 80%. Defaults to Apprio-only; set include_canaide=true to include Canaide/other entities.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pursuit_id": {"type": ["integer", "null"], "description": "Optional pursuit id; omit to analyze all pursuits"},
                "include_canaide": {
                    "type": "boolean",
                    "description": "If true, include non-Apprio entities (e.g. Canaide). Default false (Apprio-only).",
                    "default": False,
                },
            },
        },
    },
    {
        "name": "check_overcommitment",
        "description": "Check workforce staffing overcommitment. Sums proposed/committed assignment commitment_pct per staff and flags totals over 100%. Defaults to Apprio-only; set include_canaide=true for cross-entity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "include_canaide": {
                    "type": "boolean",
                    "description": "If true, include non-Apprio entities (e.g. Canaide). Default false (Apprio-only).",
                    "default": False,
                },
            },
        },
    },
    {
        "name": "flag_staffing_gap",
        "description": "Create a staffing gap record for a pursuit labor category",
        "input_schema": {
            "type": "object",
            "properties": {
                "pursuit_id": {"type": "integer"},
                "labor_category": {"type": "string"},
                "clearance_required": {"type": ["string", "null"]},
                "notes": {"type": ["string", "null"]},
            },
            "required": ["pursuit_id", "labor_category"],
        },
    },
    {
        "name": "assign_staff",
        "description": "Assign a workforce staff member to a pursuit with a commitment percentage",
        "input_schema": {
            "type": "object",
            "properties": {
                "staff_id": {"type": "integer"},
                "pursuit_id": {"type": "integer"},
                "role": {"type": ["string", "null"]},
                "commitment_pct": {"type": "number"},
                "status": {"type": "string"},
            },
            "required": ["staff_id", "pursuit_id"],
        },
    },
]


async def run_agent(message: str, conversation_id: int, session: AsyncSession) -> AsyncGenerator[dict[str, Any], None]:
    try:
        settings = get_settings()
        api_key = settings.anthropic_api_key.strip() if settings.anthropic_api_key else ""
        if not api_key:
            env_file = Path(__file__).resolve().parent.parent.parent / ".env"
            if env_file.exists():
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    if line.startswith("ANTHROPIC_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                        break
        convo_result = await session.execute(select(Conversation).where(Conversation.id == conversation_id))
        conversation = convo_result.scalar_one_or_none()
        if conversation is None:
            raise ValueError("Conversation not found")

        history_result = await session.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(desc(Message.id)).limit(20)
        )
        history = list(reversed(history_result.scalars().all()))
        history_messages: list[dict[str, Any]] = [{"role": m.role, "content": m.content} for m in history]

        user_message = Message(conversation_id=conversation_id, role="user", content=message, tool_calls=None)
        session.add(user_message)
        await session.flush()

        if not api_key:
            fallback = "ANTHROPIC_API_KEY is not configured."
            yield {"type": "text_chunk", "text": fallback}
            session.add(
                Message(conversation_id=conversation_id, role="assistant", content=fallback, tool_calls=[])
            )
            await session.commit()
            return

        system_prompt = await build_system_prompt(session, operator_name=conversation.operator)
        assistant_text = ""
        tool_call_records: list[dict[str, Any]] = []

        anthropic_messages: list[dict[str, Any]] = history_messages + [{"role": "user", "content": message}]

        while True:
            response = await call_claude(
                max_tokens=2000,
                system=system_prompt,
                messages=anthropic_messages,
                tools=ANTHROPIC_TOOL_SCHEMAS,
                model=MODEL_SONNET,
            )

            text_blocks = [block for block in response.content if block.type == "text"]
            for block in text_blocks:
                text = block.text
                assistant_text += text
                for token in text.split(" "):
                    if token:
                        yield {"type": "text_chunk", "text": token + " "}

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                break

            anthropic_messages.append({"role": "assistant", "content": response.content})
            tool_results_content: list[dict[str, Any]] = []
            for tool_use in tool_uses:
                tool_name = tool_use.name
                tool_input = tool_use.input
                yield {"type": "tool_call", "tool_name": tool_name, "status": "started"}
                tool_fn = TOOL_REGISTRY[tool_name]
                try:
                    result = await tool_fn(session, **tool_input, conversation_id=conversation_id)  # type: ignore[misc]
                except TypeError:
                    result = await tool_fn(session, **tool_input)  # type: ignore[misc]
                tool_result_str = result.model_dump_json() if hasattr(result, "model_dump_json") else json.dumps(result)
                tool_call_records.append({"tool": tool_name, "input": tool_input, "result": tool_result_str})
                yield {"type": "tool_call", "tool_name": tool_name, "status": "completed", "summary": tool_result_str}
                tool_results_content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": tool_result_str,
                    }
                )

            anthropic_messages.append({"role": "user", "content": tool_results_content})

        session.add(
            Message(
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_text.strip(),
                tool_calls=tool_call_records,
            )
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        yield {"type": "error", "message": str(exc)}

