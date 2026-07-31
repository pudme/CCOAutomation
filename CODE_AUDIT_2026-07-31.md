# CCOA Code Audit — 2026-07-31

Read-only diagnosis. No remediations applied in this pass.

**Scope:** `/Users/michaelduplantis/Documents/CCOA/CCOAutomation`  
**Method:** Model/migration diff, YAML inventory, greps across `backend/` + `frontend/`, OpenAPI vs `frontend/lib/api.ts`, live OpenAPI sample, cross-check against bugs already found this project (stale model ID, MissingGreenlet, CMMC ID mislabels, watch-ingest event-loop blocking).

**Severity guide**
- **Critical** — data leak, auth hole, or outage class with high likelihood
- **High** — wrong data / silent failure / architecture lie that will bite on a real path
- **Medium** — real defect or drift; hit under specific conditions
- **Low** — hygiene, docs, or defensive gap

---

## 1. Schema / migration drift

Migrations are hand-rolled `ALTER TABLE IF EXISTS` in `backend/main.py` (`_ensure_auditor_schema_columns`), not Alembic. `init_db()` uses `Base.metadata.create_all` (`backend/database.py:24-29`) which creates **missing tables only**, never new columns on existing tables.

| Severity | Location | What's wrong | Why it matters |
|----------|----------|--------------|----------------|
| **High** | `backend/models/auditor.py:45` vs `backend/main.py:209-215` | Model defines `auditor_checklists.source_import_id` (FK → `data_imports`); **no ALTER** adds it | Existing DBs that created `auditor_checklists` before this column lack it → INSERT/SELECT failures when the field is used. Sibling checklist columns *do* have ALTERs. |
| **High** | `backend/main.py:215` vs `backend/models/auditor.py:57` | ALTER adds `auditor_checklist_items.source_import_id INTEGER` without `REFERENCES` or index | Model expects FK + `index=True`. Upgraded DBs get a bare column: no RI, no index. |
| **High** | `backend/cli.py:96-102` | `init-db` calls `init_db()` only — never `_ensure_auditor_schema_columns` | Operators following README CLI path skip all column ALTERs. |
| **High** | `backend/database.py:24-29` (systemic) | Sole evolution path for new columns is incomplete hand SQL | Same class of bug that already hit evidence/display_name/etc. Will recur for every future model change. |
| **Medium** | `backend/main.py:194` vs `backend/models/compliance.py` (`DataImport.batch_id` index) | ALTER adds `batch_id` without `CREATE INDEX` | Fresh DBs get index via `create_all`; upgraded DBs do not → slower batch lookups. |
| **Medium** | `backend/main.py:244-265` | `CREATE TABLE IF NOT EXISTS background_jobs` / `change_log` omit model indexes (`job_type`, `status`, `category`) | Tables created via ensure DDL never get those indexes; `create_all` will not add them later. |
| **Medium** | `backend/main.py:223` (`evidence_control.display_name`) | Column ALTER only; does not reconcile `ON DELETE CASCADE` on association FKs | Delete behavior can differ between fresh and upgraded DBs. |
| **Low** | Workforce tables (`backend/models/workforce.py`) | No `_ensure` ALTERs; rely on `create_all` for whole tables | OK today for new tables; future column adds will drift without ALTERs. |
| **Low** | `backend/main.py:240` | `DROP NOT NULL` on `evidence_corrections.evidence_id` | Matches current model; one-way historical fix, not active drift. |

**Dead ALTERs (no matching model field):** none found.

---

## 2. Framework YAML integrity

`cmmc_level2.yaml` was fixed this session (RM→RA, L1→L2). Audit of the other eight loaded YAMLs:

| File | short_name | Count | Dup IDs | Empty titles | Canonical check / notes |
|------|------------|------:|---------|--------------|-------------------------|
| `iso27001_2022.yaml` | `iso27001` | **112** | none | none | Annex A = **93** present; **+19** `Cl.*` ISMS clauses. Header “All 93 Annex A” understates contents. Missing some clauses (`Cl.4.4`, `Cl.6.3`, `Cl.8.2`, `Cl.8.3`) vs a fuller clause set. |
| `iso20000_2018.yaml` | `iso20000` | 24 | none | none | High-level SMS subset (not a fixed statutory count). Gaps vs typical 4–10 set: `4.2`, `4.4`, `6.3`, `7.6`. |
| `iso9001_2015.yaml` | `iso9001` | 21 | none | none | QMS subset. Gaps vs typical: `4.2–4.4`, `6.3`, `8.3`, `8.6`, `10.3`. |
| `nist_800_53_moderate.yaml` | `nist_800_53` | **420** | none | none | Header claims **~900**; actual 420. IDs match `FAMILY-N` / enhancement form. |
| `nist_csf_2.yaml` | `nist_csf_2` | **22** | none | none | CSF 2.0 has ~**108 outcomes**; file stores **22 categories** (`CSF.GV.OC` style), not outcome IDs. |
| `hipaa_security.yaml` | `hipaa_security` | 16 | none | none | Coarse AS/PS/TS buckets — not full Security Rule impl-spec set. Custom IDs, not §164.xxx. |
| `dpa_attachment_c.yaml` | `dpa_attachment_c` | 23 | none | none | Matches claimed 23 (`DPA.1`–`DPA.23`). |
| `obligations.yaml` | `obligations` | **0** | n/a | n/a | `controls: []` by design (register is DB `obligations` table). |

| Severity | Location | What's wrong | Why it matters |
|----------|----------|--------------|----------------|
| **Medium** | `backend/config/frameworks/iso27001_2022.yaml` (header + 112 controls) | Count ≠ “93 Annex A” claim; mixes Annex A + clause controls | Matching/readiness math and operator expectations diverge from published Annex A count. |
| **Medium** | `backend/config/frameworks/nist_csf_2.yaml` (22 controls) | Category grain, not ~108 outcomes | Gap analysis against CSF outcomes will systematically under-cover. |
| **Medium** | `backend/config/frameworks/nist_800_53_moderate.yaml` (header) | Header “~900” vs 420 loaded | Same class of catalog-trust bug as CMMC mislabels — operators trust the header. |
| **Low** | `iso20000_2018.yaml`, `iso9001_2015.yaml`, `hipaa_security.yaml` | Incomplete vs full published sets (by design / subset) | Fine if intentional; document as subsets to avoid false “full coverage” reads. |
| **Low** | `obligations.yaml` | Zero controls | Expected; framework row exists for UI exclusion lists. |

No in-file duplicate `id` values found in the eight files. No empty titles. No wrong-domain-prefix bugs of the `RM`/`RA` class found outside CMMC (already fixed).

---

## 3. Async / greenlet safety

`expire_on_commit=False` (`backend/database.py`) reduces post-commit column risk. Relationship / association-proxy lazy IO under `AsyncSession` remains the MissingGreenlet class.

| Severity | Location | What's wrong | Why it matters |
|----------|----------|--------------|----------------|
| **High** | `backend/services/import_pipeline.py:2031-2041` | `selectinload(EvidenceItem.control_links)` then reads `existing_evidence.controls` (association_proxy → `link.control`) without loading `.control` | Same bug class as the prior MissingGreenlet fix; hits reanalyze skip path when links exist. |
| **Medium** | `backend/services/import_pipeline.py:1892-1905` | Same incomplete `control_links`-only load pattern | Safe today if only link columns used; any later `.controls` / `link.control` access breaks. |
| **Medium** | `backend/routers/chat.py:99-104` | Request-scoped `session` captured into SSE generator / `run_agent` | Session lifecycle vs stream duration can yield greenlet / closed-session errors under load. |
| **Low** | `backend/services/evidence_watch.py:172-198` | ORM `existing` from a closed session kept in `actions` (only `.id` used today) | Fine with `expire_on_commit=False` for scalars; relationship access would fail. |
| **Low** | Various routers using `.controls` proxy after nested `selectinload` | Pattern is correct but brittle | Regression risk if load options are trimmed. |

---

## 4. Event-loop blocking

Claude calls go through `AsyncAnthropic` / `await call_claude()` — good. **Zero** uses of `asyncio.to_thread` / `run_in_executor` under `backend/`. Sync MinIO, ChromaDB, and `Path` I/O run on the shared asyncio loop (request handlers, `BackgroundTasks`, `create_task` workers, evidence watch, BackgroundJob worker).

| Severity | Location | What's wrong | Why it matters |
|----------|----------|--------------|----------------|
| **High** | `backend/routers/import.py:133-142,190-231,249-261,299-308,503-508` | Sync MinIO + Chroma embed; `POST /import/{id}/process` awaits full pipeline on request | Stalls entire API during upload/process — same class as watch-inline Claude. |
| **High** | `backend/routers/documents.py:253-255,402-422,477+,859-930,968+,1019+,1188` | Sync Chroma search, MinIO sync/download/preview/delete on request path | Search/download/delete freeze the event loop. |
| **High** | `backend/routers/reports.py:70-74` + `backend/services/doc_generator.py:34-46` | Sync MinIO upload/download on report routes | Report generation blocks API. |
| **High** | `backend/ai/tools.py:166-168` | Sync Chroma query inside chat tool on SSE request | Chat tool use can freeze the loop mid-stream. |
| **High** | `backend/services/evidence_watch.py:159` + `ingest_local_file` MinIO path | Sync `read_bytes` every scan; ingest still does sync MinIO on worker loop | Watch cycle + BackgroundJob share the API event loop — health can stall during heavy ingest (mitigated vs inline Claude, not eliminated). |
| **High** | `backend/services/import_pipeline.py:343-351,227-252,2403+` | Sync MinIO get + Chroma embed inside `async def` pipeline | Called from request, BackgroundTasks, and worker alike. |
| **High** | `backend/services/auditor_evidence_mapper.py` (`_search_chroma` ~230-235) | Sync Chroma inside `create_task` mapping job | Mapping jobs block the API loop. |
| **Medium** | `backend/ai/agent.py:147-149` | Sync `.env` `read_text` before Claude on chat path | Small but unnecessary sync I/O on every chat. |
| **Medium** | `backend/main.py:100,141-145,382-391` | Sync MinIO/Chroma/YAML on startup | Delays readiness (less critical once up). |

---

## 5. Hardcoded values that should be config

| Severity | Location | What's wrong | Why it matters |
|----------|----------|--------------|----------------|
| **High** | `backend/main.py:52` | CORS origins hardcoded `localhost:3000` / `3001` | Deployed hosts / alternate ports require code change; `:3000` may be unused. |
| **High** | `backend/services/doc_generator.py:46` | Download URL hardcoded `http://localhost:8010/reports/download/...` | Broken links in any non-local deploy. |
| **Medium** | `backend/config.py:20,27-28` | Default DB URL / MinIO secret `password` | Weak defaults if env not set in a shared environment. |
| **Medium** | `backend/ai/gateway.py:23-24` | Duplicate hardcoded Claude model fallbacks beside `config.py` defaults | Dual source of truth — same class as the stale Sonnet ID bug. |
| **Medium** | Framework short_name sets duplicated in `readiness_mode.py:11-12`, `settings.py:26-29`, `ai/tools.py:46-50`, `frameworks.py:19`, `dashboard.py:30`, `personnel_checker.py:179` | Magic strings drift independently | One rename breaks some paths silently. |
| **Low** | `backend/config.py:38-42` | Default model IDs in Settings | Acceptable defaults; env-overridable (fixed this session). |
| **Info** | No `sk-ant-…` or live API key literals in repo | Secrets not committed | Keep it that way. |

---

## 6. Documentation vs. actual architecture

| Severity | Location | Stale claim | Actual behavior |
|----------|----------|-------------|-----------------|
| **High** | `CLAUDE.md:31` | “Auth (local)” + “Celery Tasks” | No auth middleware/deps anywhere. Jobs = asyncio + `background_jobs` table (`main.py:111-112`, `services/background_jobs.py`). |
| **High** | `CLAUDE.md:69` | “Celery + Redis” task queue | Zero Celery usage in `backend/**/*.py`. Redis only config + CLI health ping. |
| **High** | `CLAUDE.md:86` | Redis “Session cache, Celery broker” | No session cache; broker unused. |
| **High** | `CLAUDE.md:180-183` | `workers/celery_app.py`, `ingest_tasks.py`, `report_tasks.py` | Only `backend/workers/sync_tasks.py` (“intentionally disabled”) + `__init__.py`. |
| **High** | `CLAUDE.md:329-330` | Celery Notion scheduled sync | `ingest_notion_page` returns not-implemented (`ai/tools.py:715-719`); `integrations/` empty by design. |
| **Medium** | `CLAUDE.md:56,101` | Next.js 14 | `frontend/package.json` → Next **16.2.4**. |
| **Medium** | `CLAUDE.md:58-60` | “Vercel AI SDK useChat” + TanStack Query | `@ai-sdk/react`/`ai` present but **no `useChat`**; **no TanStack Query** usage in frontend source. |
| **Medium** | `CLAUDE.md:167-172` | Notion/Entra/CrowdStrike/NinjaOne/AWS modules | Only `integrations/__init__.py` + README. |
| **Medium** | `docker-compose.yml` Redis service + `REDIS_URL` | Implies active broker | Idle for queuing. |
| **Low** | `CLAUDE.md:390` Postgres `localhost:5432` | Compose publishes **5433:5432** | Host port mismatch for local psql. |
| **Low** | `CLAUDE.md` project tree | Missing `/workforce`, `/evidence` app routes | Pages exist post this session. |
| **OK** | Root `README.md` / `frontend/README.md` ports | `localhost:3001` | Aligned with `FRONTEND_HOST_PORT`. |
| **OK** | Model ID Sonnet in CLAUDE.md | Matches current defaults | Accurate; Haiku under-documented. |

No Celery mentions in backend Python comments (docs-only staleness).

---

## 7. Audit trail consistency

**Established split:** agent-tool writes → `agent_action_log`; human/API evidence corrections → `evidence_corrections`; some document/settings paths → `change_log`.

### Agent tools (writes)

| Path | Logs |
|------|------|
| `create_obligation`, `update_obligation`, findings/CA tools, `update_control_status`, `add_evidence`, `flag_staffing_gap`, `assign_staff`, `update_checklist_item` | `agent_action_log` via `_log_write` |
| Read tools / report generators / Notion stub | n/a or no durable write |

### REST write paths missing audit sinks

| Severity | Location | What's wrong | Why it matters |
|----------|----------|--------------|----------------|
| **High** | `backend/routers/workforce.py:156-543` (all POST/PATCH/DELETE) | No `agent_action_log`, `evidence_corrections`, or `change_log` | Staffing mutations are invisible to audit. |
| **High** | `backend/routers/obligations.py:37,70,95` | No audit log | UI obligations CRUD bypasses agent trail. |
| **High** | `backend/routers/controls.py:74` | `PATCH /controls/{id}` — no log | Control status/notes changes untracked when via REST. |
| **High** | `backend/routers/findings.py:102,133` | Corrective-action create/patch — no log | Status patch logs to `change_log` only when status changes (`:64-88`); notes/owner/CA writes silent. |
| **High** | `backend/routers/auditor.py:157,190,245,282,489,509` | Checklist/item CRUD, delete, generate-response — no audit sink | Manual auditor edits untracked (refresh/match go through mapper → `change_log`). |
| **Medium** | `backend/routers/documents.py:540-661,711` | Reanalyze queue/cancel + `refresh-links` | Mutate analysis/control state without `change_log`. |
| **Medium** | `backend/routers/import.py:548,566` | `force-fail` / `override-type` | No audit trail. |
| **OK** | `evidence.py` PATCH paths | `evidence_corrections` | Matches established split. |
| **OK** | Document delete/sync/duplicates, settings audit-dates/api-* | `change_log` | Present. |

---

## 8. Multi-tenant entity scoping

Workforce **analysis** paths default Apprio-only with `include_canaide` opt-in. List/report paths do not.

| Severity | Location | What's wrong | Why it matters |
|----------|----------|--------------|----------------|
| **High** | `backend/routers/workforce.py:127-132` | `GET /workforce/staff` returns all entities | Cross-entity staff roster leak vs gap-analysis default. |
| **High** | `backend/routers/evidence.py:70-86` | `GET /evidence` — no entity filter | Evidence from Apprio + Canaide returned together. |
| **High** | `backend/services/personnel_checker.py:137-176` (+ `/personnel/compliance-report`) | All active/terminated records; no Apprio default | Personnel PII/compliance gaps cross entities. |
| **Medium** | `backend/routers/documents.py:68,168-169` | Default list is all entities; `entity` is opt-in filter | Opposite of workforce analysis default. |
| **Medium** | `backend/routers/documents.py:1266-1284` | Unknown/empty entity displayed as `"Apprio"` | Labeling can hide true entity. |
| **OK** | Gap analysis / overcommitment / agent staffing tools | Apprio-only unless `include_canaide` | Consistent opt-in. |

---

## 9. Error handling

| Severity | Location | What's wrong | Why it matters |
|----------|----------|--------------|----------------|
| **High** | `backend/routers/documents.py:920-930` | Preview catches MinIO errors, returns **200** with `file_bytes = b""` and empty-ish preview | Client sees success for a failed fetch — masks store outages. |
| **Medium** | `backend/routers/documents.py:1187-1190` | `remove_object` failures `except Exception: pass` | Delete reports success while objects remain in MinIO. |
| **Medium** | `backend/services/import_pipeline.py:200-203` | Chroma `delete` failure silently `pass` | Stale vectors can remain after re-embed. |
| **Medium** | Widespread `except Exception` in watch/worker/import | Logged in many places (OK); some continue without surfacing to caller | Partial failures look like success at HTTP layer when backgrounded. |
| **Low** | `backend/main.py:132`, `database.py:10` | `pass` on cancel / abstract | Benign. |

No bare `except:` (no type) found. Many `except Exception` handlers do log — those are not listed unless they swallow without log or return 200 on failure.

---

## 10. Frontend / backend contract drift

OpenAPI: **88** paths. `frontend/lib/api.ts`: ~**52** distinct helpers.

| Severity | Location | What's wrong | Why it matters |
|----------|----------|--------------|----------------|
| **Medium** | `backend/routers/personnel.py:10-12` vs `frontend` personnel page | `GET /personnel` returns `{"status":"ok"}` stub; UI uses `/personnel/compliance-report` | Dead list endpoint; easy to wire wrong. |
| **Medium** | Backend-only (no frontend helper usage needed but unwired UI): many auditor admin edges already wired; **~41** OpenAPI paths lack frontend helpers | Expected for CLI/agent-only routes; notable: raw `GET /controls`, `GET /personnel`, `GET /import/controls` | Not bugs unless UI assumes them. |
| **Low** | Workforce/evidence helpers | Shapes match current routers (`page/items` for evidence, staff/pursuit CRUD, gap-analysis query `include_canaide`) | Recently added; aligned. |
| **Low** | `frontend/lib/api.ts` `readJson` | Throws generic `Error` — loses HTTP status/`detail` except a few auditor helpers | UI error messages stay vague. |
| **Info** | Frontend calls that exist and match OpenAPI | workforce/*, evidence/*, findings, obligations, documents, auditor match/refresh, settings | No phantom endpoint calls found in `api.ts` after normalization. |

---

## 11. Dead code

| Severity | Location | What's wrong | Why it matters |
|----------|----------|--------------|----------------|
| **Medium** | `backend/routers/workforce.py:135-143` | Comment documents removed `POST /workforce/staff/import-from-personnel` | Good; ensure no frontend still calls it — **none found**. |
| **Medium** | `backend/routers/ingest.py:8` | Always `501 Not Implemented` | Dead route still mounted. |
| **Medium** | `backend/workers/sync_tasks.py` + `CLAUDE.md` worker tree | Stub “intentionally disabled”; Celery files absent | Confuses operators looking for Celery entrypoints. |
| **Medium** | `backend/integrations/` | Empty package + README; agent still exposes `ingest_notion_page` | Tool is a no-op stub (`ai/tools.py:715-719`). |
| **Low** | `backend/routers/personnel.py:10-12` | Stub list endpoint | Dead surface. |
| **Low** | Root-level stale `models.py` (if present outside backend) | Parallel schema not imported by API | Confusion only. |
| **OK** | `personnel_records` model + import upsert + compliance report | Still live for MFA/training/NDA evidence — **not** deprecated as a table; only workforce import-from-personnel is deprecated | Do not delete personnel pipeline wholesale. |

---

## 12. CORS / access control

| Severity | Location | What's wrong | Why it matters |
|----------|----------|--------------|----------------|
| **Critical** | `backend/main.py:50-56` + all routers | **No authentication** on any route (`Depends(get_db)` is DB only). No JWT/API key/HTTPBearer. | Any client that can reach `:8010` can read/write workforce, evidence, chat (API spend), deletes, settings. |
| **Critical** | Newer domains (`workforce`, `evidence` corrections, gap-analysis) | Same openness as older routes — no extra checks | Feature adds expanded the unauthenticated write surface. |
| **High** | `backend/main.py:52-55` | `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]` with fixed localhost origins | Fine for local-only; dangerous if API is exposed beyond loopback without auth. |
| **Medium** | CORS still lists `http://localhost:3000` | Host UI default is **3001** | Broader than needed; low risk locally. |

---

## Top-10 prioritized punch list

Ordered by **severity × likelihood of being hit in real use**. Each item is scoped like the recent surgical fix rounds — specific enough to turn into a Cursor prompt.

1. **Critical — Auth gap on entire API**  
   `backend/main.py` + all routers: there is no auth. Add at least a shared-secret/API-key or session gate on write routes (workforce, evidence PATCH, documents DELETE, import, chat, settings) before any network exposure beyond localhost. Verify workforce + evidence corrections use the same dependency as older routes.

2. **High — Event-loop blocking on request path (MinIO/Chroma)**  
   Offload sync MinIO + Chroma in `backend/routers/import.py`, `documents.py` (search/download/preview/delete/sync), `reports.py` / `doc_generator.py`, and `ai/tools.py` Chroma search via `asyncio.to_thread` (or a real worker process). Same class as the watch-ingest fix; highest user-visible stall risk.

3. **High — MissingGreenlet on reanalyze skip path**  
   `backend/services/import_pipeline.py:2031-2041`: stop using association_proxy `.controls` without loading `EvidenceControlLink.control`; use `control_links` length/contents or add nested `selectinload(...).selectinload(EvidenceControlLink.control)`.

4. **High — Entity scoping inconsistent (data leak)**  
   Align `GET /workforce/staff`, `GET /evidence`, and `/personnel/compliance-report` with Apprio-only default + `include_canaide` (or `entity=`) opt-in, matching gap-analysis/overcommitment. Flag: `workforce.py:127-132`, `evidence.py:70-86`, `personnel_checker.py:137-176`.

5. **High — Schema drift: `auditor_checklists.source_import_id`**  
   Add ALTER (+ FK) in `_ensure_auditor_schema_columns`; complete `auditor_checklist_items.source_import_id` ALTER with FK + index; make CLI `init-db` run ensures (`cli.py:96-102`).

6. **High — REST write paths with no audit trail**  
   Log workforce CRUD, obligations CRUD, `PATCH /controls/{id}`, findings CA REST, and auditor item CRUD to `change_log` (or domain tables). Keep agent tools on `agent_action_log`; keep evidence on `evidence_corrections`.

7. **High — Docs claim Celery/auth/integrations that do not exist**  
   Rewrite `CLAUDE.md` §§ architecture/tech stack/workers/integrations: asyncio `BackgroundJob` worker, no Celery, no auth, integrations stubbed, Next 16, ports 3001/5433. Prevents the next session from “fixing” the wrong queue.

8. **Medium — Preview/delete swallow store errors**  
   `documents.py:920-930` must not return 200 with empty bytes on MinIO failure (use 404/502). `documents.py:1187-1190` must not `pass` on `remove_object` failure without surfacing/logging into the delete result.

9. **Medium — Hardcoded report URL + CORS/config centralization**  
   Replace `doc_generator.py:46` localhost download URL with settings base URL; move CORS origins to env; collapse duplicate Claude model fallbacks in `gateway.py` to settings-only; centralize framework short_name constants.

10. **Medium — Framework catalog honesty (non-CMMC)**  
    Fix or document: ISO 27001 header vs 112 (93 Annex A + clauses); NIST CSF 22 categories vs ~108 outcomes; NIST 800-53 header “~900” vs 420. Same trust class as the CMMC ID audit — no fabricated controls, just accurate claims/counts.

---

## Out of scope / explicitly not bugs

- CMMC L2 ID mislabels (RM/L1) — **fixed** this session; third restart showed `mappings_created=0`.
- Anthropic model IDs — env-driven via `config.py` / `gateway.py`.
- Watch ingest Claude on main loop — moved to `BackgroundJob` (sync MinIO/read still blocks; see §4).
- `personnel_records` table itself — still required for compliance checks; only workforce import-from-personnel is deprecated.

---

*End of audit. Diagnosis only — no code changes beyond this report file.*
