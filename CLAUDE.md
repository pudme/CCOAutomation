# Compliance Platform — Cursor Project Instructions (v2)

## What This Is

An AI-powered compliance management platform for Apprio Inc., operated by the Chief
Compliance Officer. It manages all active compliance frameworks (ISO 27001, ISO 20000,
ISO 9001, CMMC Level 2, probationary obligations), stores and searches compliance
documents, tracks evidence and findings, and provides an intelligent chat interface
that understands compliance context and can update records from natural language input.

The AI agent is the core of the platform. It can ingest unstructured input — Notion
notes, meeting summaries, emails, uploaded files — understand what they mean in a
compliance context, and autonomously update controls, evidence records, findings, and
corrective actions. It surfaces gaps, answers questions about audit readiness, and
generates reports on demand.

This is a full-stack web application that runs locally on a Mac.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    NEXT.JS FRONTEND                      │
│  Dashboard │ Framework Views │ Documents │ Chat Interface │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP / SSE (streaming)
┌────────────────────────▼────────────────────────────────┐
│                    FASTAPI BACKEND                       │
│  REST API │ SSE Streaming │ Optional X-CCOA-Dev-Key write gate │
│  BackgroundJob asyncio worker (not Celery)                      │
└──────┬──────────────────────────┬───────────────────────┘
       │                          │
┌──────▼──────┐          ┌────────▼────────┐
│  AI ENGINE  │          │   INTEGRATIONS  │
│  Claude API │          │  Stub package   │
│  Tool Use   │          │  (not wired)    │
│  Agents     │          │                 │
└──────┬──────┘          └─────────────────┘
       │
┌──────▼───────────────────────────────────┐
│              DATA LAYER                   │
│  PostgreSQL  │  ChromaDB  │  MinIO        │
│  (records)   │  (vectors) │  (documents)  │
│  Redis present in compose but idle        │
└───────────────────────────────────────────┘
```

---

## Tech Stack

### Frontend
| Component     | Choice                  | Purpose                                         |
|---------------|-------------------------|-------------------------------------------------|
| Framework     | Next.js 16 (App Router) | Full-stack React, file-based routing            |
| Styling       | Tailwind CSS + shadcn/ui| Clean, consistent UI components                 |
| Chat UI       | Custom SSE client       | Streaming chat with Claude, message history     |
| State         | Zustand                 | Lightweight global state                        |
| Data fetching | `lib/api.ts` fetch helpers | Typed REST client (no TanStack Query in use) |
| Charts        | Recharts                | Compliance scorecard, gap charts                |
| Icons         | Lucide React            | Consistent iconography                          |

### Backend
| Component     | Choice                  | Purpose                                         |
|---------------|-------------------------|-------------------------------------------------|
| Framework     | FastAPI                 | Async Python API, SSE support                   |
| ORM           | SQLAlchemy 2.x (async)  | DB abstraction                                  |
| Task queue    | asyncio + `background_jobs` table | In-process worker: reanalyze, evidence-watch ingest |
| Auth (local)  | Optional `CCOA_DEV_KEY` | Write methods only (`X-CCOA-Dev-Key`); no Cognito yet |
| Validation    | Pydantic v2             | Request/response schemas                        |
| Logging       | Loguru                  | Structured logging                              |

### AI Engine
| Component     | Choice                  | Purpose                                         |
|---------------|-------------------------|-------------------------------------------------|
| Model         | claude-sonnet-4-5-20250929| Primary reasoning, ingestion, chat              |
| Interface     | Anthropic Python SDK    | Tool use, streaming, multi-turn conversations   |
| Pattern       | Agentic tool use loop   | Agent decides what to read/write/update         |

### Data Layer
| Component     | Choice                  | Purpose                                         |
|---------------|-------------------------|-------------------------------------------------|
| Primary DB    | PostgreSQL              | All structured records                          |
| Vector DB     | ChromaDB (local)        | Semantic document search, note ingestion        |
| Document store| MinIO (local S3)        | Binary document storage (PDF, DOCX, XLSX, PNG)  |
| Redis         | Present in compose      | Idle today (CLI health ping only; not a broker) |

Start all infrastructure with: `docker-compose up -d`

---

## Project Structure

```
compliance_platform/
├── CLAUDE.md                          # This file — always read first
├── docker-compose.yml                 # Local infrastructure
├── .env                               # Secrets — gitignored
├── .env.example                       # Documented variable names, no values
│
├── frontend/                          # Next.js 16 application
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── dashboard/page.tsx
│   │   ├── frameworks/
│   │   ├── documents/page.tsx
│   │   ├── findings/page.tsx
│   │   ├── evidence/page.tsx          # Framework-filtered evidence + corrections
│   │   ├── evidence/[id]/page.tsx
│   │   ├── obligations/page.tsx
│   │   ├── personnel/page.tsx
│   │   ├── workforce/page.tsx         # Staff / pursuits / assignments / gaps
│   │   ├── workforce/pursuits/[id]/page.tsx
│   │   └── chat/page.tsx
│   ├── components/
│   │   ├── layout/                    # Sidebar, TopBar
│   │   ├── chat/                      # Custom SSE chat UI (not useChat)
│   │   └── shared/
│   └── lib/
│       ├── api.ts                     # Typed fetch helpers (no TanStack Query)
│       ├── types.ts
│       └── utils.ts
│
├── backend/                           # FastAPI application
│   ├── main.py                        # Entrypoint, startup ensures, BackgroundJob + watch loops
│   ├── config.py                      # Settings from .env via pydantic-settings
│   ├── database.py
│   ├── models/                        # compliance, personnel, auditor, workforce
│   ├── routers/                       # frameworks, controls, evidence, findings,
│   │                                  # obligations, personnel, workforce, documents,
│   │                                  # reports, import, chat, auditor, …
│   ├── ai/                            # agent.py, tools.py, gateway.py
│   ├── integrations/                  # Stub package only (not implemented)
│   ├── services/
│   │   ├── background_jobs.py         # asyncio worker (reanalyze + evidence_watch_ingest)
│   │   ├── evidence_watch.py          # Drop-folder scanner → enqueue BackgroundJob
│   │   ├── workforce_alignment.py
│   │   ├── personnel_checker.py
│   │   ├── import_pipeline.py
│   │   └── …
│   ├── workers/
│   │   ├── __init__.py
│   │   └── sync_tasks.py              # Intentionally disabled stub
│   └── config/frameworks/             # YAML catalogs (loaded on startup)
│
└── templates/
    └── apprio_base.docx
```

---

## AI Agent — Tools

The agent (backend/ai/agent.py) runs Claude claude-sonnet-4-5-20250929 with tool use.
It loops until the task is complete, streaming each action and response back to the
frontend via SSE. All tool calls that modify data are logged to agent_action_log.

```python
# backend/ai/tools.py — define all of these as structured tool schemas

# READ TOOLS
search_documents(query: str) -> list[DocumentChunk]
# Semantic search across ChromaDB — all policies, evidence, reports, notes

get_control(control_id: str, framework: str) -> Control
# Fetch control with evidence list and status

get_framework_gaps(framework_short_name: str) -> GapReport
# All controls with missing or stale evidence

get_open_findings() -> list[Finding]
# All open findings and corrective actions with status

get_personnel_exceptions() -> PersonnelComplianceReport
# Current flags: training gaps, MFA, NDA, terminated accounts

get_obligations_due(days: int) -> list[Obligation]
# Obligations due within N days

# WRITE TOOLS (require user confirmation unless auto-apply is on)
update_control_status(control_id: str, framework: str,
                      status: str, notes: str) -> Control

add_evidence(control_ids: list[str], filename: str,
             evidence_type: str, description: str,
             entity: str | None) -> EvidenceItem

create_finding(control_ids: list[str], framework: str,
               title: str, description: str, severity: str) -> Finding

update_finding(finding_id: str, status: str, notes: str) -> Finding

add_corrective_action(finding_id: str, description: str,
                      owner: str, due_date: str) -> CorrectiveAction

update_obligation(obligation_id: str, status: str, notes: str) -> Obligation

# INGEST TOOLS
ingest_notion_page(page_url: str) -> IngestionResult
# Pull Notion page via API, embed, extract actions, propose DB updates

ingest_text(content: str, source_label: str) -> IngestionResult
# Process raw pasted text: meeting notes, emails, etc.

# GENERATE TOOLS
generate_gap_report(framework: str, format: str) -> ReportResult
generate_scorecard() -> ReportResult
generate_audit_package(framework: str) -> ReportResult
```

---

## Chat Interface Behavior

1. **Streaming.** Claude responses stream token by token via SSE. Tool calls display
   inline as they execute with a status indicator:
   "🔍 Searching documents...", "✏️ Updating A.6.3...", "📄 Adding evidence record..."

2. **Notion ingestion.** User pastes a Notion URL or page content into chat.
   Agent fetches it, processes it, and proposes specific compliance updates.

3. **File drop.** User drags a file onto the chat window. It uploads to MinIO,
   gets embedded in ChromaDB, and the agent immediately processes it for
   compliance relevance and proposes evidence tagging.

4. **Suggested prompts** when chat is empty:
   - "What are my biggest gaps before the May audit?"
   - "Ingest my latest Notion meeting notes: [url]"
   - "Show me everything open for CMMC"
   - "Generate a management scorecard for Todd"
   - "What corrective actions are overdue?"
   - "Run a personnel compliance check"

5. **Confirmation cards.** Write operations surface a confirmation card before
   executing. User clicks Approve or Reject per proposed change. Auto-apply mode
   is a toggle in settings that bypasses confirmations.

6. **Conversation history.** All conversations stored in Postgres. Left panel in
   chat view shows conversation history. Users can resume any prior conversation.

---

## Document Store

### MinIO Bucket Layout
```
compliance-documents/
├── evidence/           # Tagged evidence files
├── policies/           # Policy PDFs and Word docs
├── reports/            # Generated scorecards, gap reports
├── imports/            # Raw Notion exports, uploaded notes
└── generated/          # AI-generated documents awaiting review
```

### ChromaDB Collections
```
compliance_docs         # All documents, chunked (~512 tokens), embedded
  metadata: {
    doc_type, filename, framework, control_ids[],
    entity, collected_date, minio_path
  }

meeting_notes           # All ingested notes and summaries
```

---

## Notion Ingestion Flow

**Not implemented.** `ingest_notion_page` returns a stub result directing operators to
`/import/text` or `/import/file`. There is no live Notion API client and no scheduled
sync worker. External sync tasks under `backend/workers/sync_tasks.py` are intentionally
disabled.

---

## Dashboard Layout

- **Audit Countdown** — days to external audit, always visible in top bar
- **Framework Readiness Rings** — one per framework, % controls evidenced
  (Green ≥ 90%, Yellow 70–89%, Red < 70%)
- **Gap Heat Map** — by control domain
- **Open Findings** — AF-01 through AF-06, PF-01, status and owner
- **Obligations Due** — next 30 days, color-coded
- **Recent Agent Activity** — last 10 agent actions with timestamps
- **Personnel Exceptions** — training gap count, MFA gaps, NDA gaps

---

## Core Design Rules

1. **AI is the primary interface.** The chat is how the operator acts on compliance
   data. The GUI views are for visibility; the chat is for action.

2. **Every agent write is logged.** All tool calls that modify data write to
   `agent_action_log` with: timestamp, tool, parameters, result, conversation_id.
   Non-negotiable audit trail.

3. **Framework-agnostic engine.** No framework-specific logic in any module.
   Frameworks live in YAML files under backend/config/frameworks/. The engine
   reads them. Never hardcode control IDs in Python or TypeScript.

4. **Single evidence record, multiple framework tags.** Evidence stored once in
   MinIO + ChromaDB, tagged to all applicable controls via the join table.

5. **Cross-mapping is automatic.** When agent adds evidence to an ISO 27001
   control, it checks cross_map table and tags the same evidence to all mapped
   controls in other frameworks simultaneously.

6. **No secrets in code.** All credentials in .env. Loaded via pydantic-settings
   in config.py. Never hardcode API keys anywhere.

7. **Stream everything.** All AI responses stream via SSE. No waiting for
   complete responses. FastAPI SSE → custom chat client in Next.js (not `useChat`).

8. **Fail loudly.** Descriptive exceptions. Agent reads DB state before acting —
   never assumes or hallucinates current state.

9. **Type everything.** Full type hints in Python. Full TypeScript types in
   frontend. Pydantic schemas as the contract between frontend and backend.

10. **Never write test fixtures into live compliance data.** Sample files, mock
   records, and fixture imports must only run against a dedicated test database
   or mocks. Production code paths must not insert test or demo records into
   the live `compliance_db`.

---

## Environment Variables (.env)

```bash
# Database
DATABASE_URL=postgresql+asyncpg://compliance:password@localhost:5433/compliance_db
REDIS_URL=redis://localhost:6379/0
CCOA_DEV_KEY=

# Document store
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=compliance
MINIO_SECRET_KEY=password
MINIO_BUCKET=compliance-documents

# Vector DB
CHROMA_HOST=localhost
CHROMA_PORT=8001

# AI
ANTHROPIC_API_KEY=sk-ant-...

# Notion
NOTION_API_KEY=secret_...
NOTION_COMPLIANCE_DB_ID=          # Optional: Notion DB for scheduled sync

# Microsoft Graph (Entra ID)
ENTRA_TENANT_ID=
ENTRA_CLIENT_ID=
ENTRA_CLIENT_SECRET=

# CrowdStrike
CROWDSTRIKE_CLIENT_ID=
CROWDSTRIKE_CLIENT_SECRET=
CROWDSTRIKE_BASE_URL=

# NinjaOne
NINJAONE_CLIENT_ID=
NINJAONE_CLIENT_SECRET=

# AWS GovCloud
AWS_PROFILE=govcloud
AWS_REGION=us-gov-west-1
AWS_ACCOUNT_ID=455490517765

# App
NEXT_PUBLIC_API_URL=http://localhost:8010
APP_ENV=development
```

---

## Docker Compose

```yaml
version: "3.9"
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: compliance_db
      POSTGRES_USER: compliance
      POSTGRES_PASSWORD: password
    ports: ["5433:5432"]
    volumes: ["postgres_data:/var/lib/postgresql/data"]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: compliance
      MINIO_ROOT_PASSWORD: password
    ports: ["9000:9000", "9001:9001"]
    volumes: ["minio_data:/data"]

  chromadb:
    image: chromadb/chroma:latest
    ports: ["8001:8000"]
    volumes: ["chroma_data:/chroma/chroma"]

volumes:
  postgres_data:
  minio_data:
  chroma_data:
```

### Rebuild app images after code changes

The **frontend** image bakes in `next build` output, so `docker compose up -d` alone does not refresh the UI from edited source. After changing frontend or backend code (or pulling updates), rebuild the app services:

`docker compose up -d --build backend frontend`

Windows: `rebuild.bat` — Mac/Linux: `./rebuild.sh`

Backend uses a bind mount plus `--reload`, so `.py` edits often apply live; rebuild anyway when `requirements.txt` or the Dockerfile changes, and always rebuild frontend after UI changes.

---

## Personnel Checker Logic (backend/services/personnel_checker.py)

Five sequential cross-references run against HR and system data:

1. **Training gap** — active employee list vs. training completion records.
   Multi-pass: exact name → last name → first initial + last name → manual flag.
   Quiz-only completions are valid. Flag last-name-only matches with differing
   first names for manual review.

2. **Termination access revocation** — termination tickets vs. Entra ID
   non-active users export + offboarding audit logs. Active Entra account for
   a terminated employee = A.6.5 finding.

3. **NDA/PIIA coverage** — active employees vs. signed PIIA log. Flag any
   active employee without a signed agreement.

4. **MFA enrollment** — active employees vs. MFA enrollment report. Unflagged
   active user without MFA = A.8.5 flag.

5. **CMMC extension** — when CMMC framework is loaded, add: background
   screening status (PS.L2-3.9.1) and user access review cadence (AC.L2-3.1.3).

---

## Workforce Alignment

Workforce Alignment tracks staffing against pursuits (e.g. live contracts and bids):
`WorkforceStaff`, `WorkforcePursuit`, `WorkforceAssignment`, and `WorkforceGap`
under `backend/models/workforce.py`, with CRUD at `/workforce/*` and gap /
overcommitment logic in `backend/services/workforce_alignment.py`.

**Relationship to `personnel_records`:** `WorkforceStaff` is a **standalone roster**
maintained directly via the API/UI. `personnel_id` is an **optional link** to a
compliance `personnel_records` row for cross-reference when both happen to exist —
**not a data source**. `personnel_records` holds compliance-evidence fields only
(MFA, training, NDA, background check, Entra sync); it does not and will not hold
staffing fields (labor category, clearance level, utilization, skills, rates, etc.).

The former `POST /workforce/staff/import-from-personnel` endpoint is **deprecated
and removed**. It only ever copied identity plumbing (`personnel_id`,
`display_name`, `entity`) and added complexity without real staffing value.

**Intended data path:** HR-provided staffing data will be manually requested and
uploaded into CCOA as a document, not synced automatically. No ingestion pipeline
exists for this yet, and none is being built in the current session.

Agent tools (read/write as appropriate): `get_staffing_gaps`,
`check_overcommitment`, `flag_staffing_gap`, `assign_staff`. Gap analysis and
overcommitment default to Apprio-only staff (`include_canaide=False`); set
`include_canaide=True` for cross-entity visibility while Canaide is divesting.

---

## Build Sequence for Cursor

Build in this exact order:

1. `docker-compose.yml`
2. `backend/config.py` + `backend/database.py`
3. `backend/models/compliance.py` (port from original models.py, adapt for async)
4. `backend/main.py` + health check route
5. `backend/ai/tools.py` — define tool schemas and stub implementations
6. `backend/ai/agent.py` — agentic loop with Claude tool use + SSE streaming
7. `backend/routers/chat.py` — SSE endpoint invoking the agent
8. `frontend/` init — `npx create-next-app@latest frontend --typescript --tailwind --app`
9. `frontend/app/chat/page.tsx` + `ChatWindow.tsx` — working streaming chat
10. `backend/routers/frameworks.py` + YAML loader
11. `frontend/app/dashboard/page.tsx` — dashboard pulling real data
12. Import pipeline + evidence watch / BackgroundJob worker (integrations remain stubs)
13. Remaining routers, service modules, and views

**Starting prompt for Cursor:**
"Read CLAUDE.md completely before writing any code. Then implement step 1 and 2
from the Build Sequence: docker-compose.yml and backend/config.py +
backend/database.py with async SQLAlchemy 2.x connected to PostgreSQL."

---

## Apprio Context (injected into agent system prompt at runtime)

- Org: Apprio Inc. (federal) + Canaide (commercial, divesting)
- Operator: Michael DuPlantis — Chief Compliance Officer, Sr. Cybersecurity Architect
- CEO: Todd Traver | Incoming CTO: Pete | Outgoing CISO: Sri Krishnan (departing with Canaide)
- Infrastructure: AWS GovCloud us-gov-west-1 (Account 455490517765), Entra ID,
  Intune, NinjaOne, CrowdStrike, ADP
- Active audit cycle: May 2025 – May 2026 | External audit: May 2026
- CMMC assessment target: Summer 2026
- Open findings: AF-01 (obs/physical), AF-02 (obs/physical), AF-03 (obs/physical),
  AF-04 (risk accepted/cameras), AF-05 (minor NC/logging), AF-06 (minor NC/NinjaOne),
  PF-01 (minor NC/training)
- Evidence naming: [ControlID]_[Description]_[Entity_optional]_[YYYYMMDD].[ext]
- Word doc metadata: creator and lastModifiedBy = "Michael DuPlantis"
- Apprio logo in all generated document headers
