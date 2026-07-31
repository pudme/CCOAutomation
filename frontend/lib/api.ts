/* eslint-disable @typescript-eslint/no-explicit-any */
import { DashboardSummary } from "@/lib/types";

const API_BASE =
  typeof window === "undefined"
    ? (process.env.API_BASE_URL || "http://backend:8010")
    : (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8010");

async function readJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    throw new Error(`Request failed for ${path}`);
  }
  return (await response.json()) as T;
}

export async function getDashboardSummary(): Promise<DashboardSummary> {
  return readJson<DashboardSummary>("/dashboard/summary");
}

export async function getFindings(): Promise<any[]> {
  return readJson<any[]>("/findings");
}

export async function getFinding(findingId: string): Promise<any> {
  return readJson<any>(`/findings/${findingId}`);
}

export async function patchFinding(findingId: string, payload: Record<string, unknown>): Promise<any> {
  return readJson<any>(`/findings/${findingId}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export async function addCorrectiveAction(findingId: string, payload: Record<string, unknown>): Promise<any> {
  return readJson<any>(`/findings/${findingId}/corrective-actions`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function patchCorrectiveAction(
  findingId: string,
  correctiveActionId: number,
  payload: Record<string, unknown>,
): Promise<any> {
  return readJson<any>(`/findings/${findingId}/corrective-actions/${correctiveActionId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function getObligations(): Promise<any[]> {
  return readJson<any[]>("/obligations");
}

export async function createObligation(payload: Record<string, unknown>): Promise<any> {
  return readJson<any>("/obligations", { method: "POST", body: JSON.stringify(payload) });
}

export async function patchObligation(id: string, payload: Record<string, unknown>): Promise<any> {
  return readJson<any>(`/obligations/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export async function deleteObligation(id: string): Promise<any> {
  return readJson<any>(`/obligations/${id}`, { method: "DELETE" });
}

export async function getPersonnelReport(): Promise<any> {
  return readJson<any>("/personnel/compliance-report");
}

export async function getDocuments(library = "main"): Promise<any[]> {
  return readJson<any[]>(`/documents?library=${encodeURIComponent(library)}`);
}

export type FolderSyncPreview = {
  total_scanned: number;
  new: number;
  modified: number;
  unchanged: number;
  skipped: number;
  new_files: string[];
  modified_files: string[];
  errors: string[];
  main_library_collisions?: string[];
  up_to_date?: boolean;
};

export type FolderSyncStreamEvent =
  | { event: "start"; data: { total_scanned: number; total_to_import: number; summary: FolderSyncPreview; message?: string } }
  | { event: "file"; data: { filename: string; mode: "new" | "modified"; completed: number; total: number } }
  | { event: "complete"; data: FolderSyncPreview }
  | { event: "error"; data: { message: string; type?: string; estimate?: BatchCostEstimate } };

export type DuplicateRecord = {
  import_id: number;
  filename: string;
  duplicate_status: "suspected" | "confirmed_duplicate" | "unique" | "false_positive";
  duplicate_of_id: number | null;
  duplicate_of_filename: string | null;
  confidence: string | null;
  reason: string | null;
  created_at: string | null;
};

export type ChangeLogEntry = {
  id: number;
  timestamp: string;
  category: string;
  action: string;
  subject: string | null;
  detail: string | null;
  triggered_by: string;
};

export async function previewFolderSync(files: File[], library = "main"): Promise<FolderSyncPreview> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file, file.name);
  }
  form.append("library", library);
  const response = await fetch(`${API_BASE}/documents/sync-preview`, {
    method: "POST",
    cache: "no-store",
    body: form,
  });
  const parsed = (await response.json()) as FolderSyncPreview;
  if (!response.ok) {
    throw new Error("Sync preview failed");
  }
  return parsed;
}

export async function streamFolderSync(
  files: File[],
  payload: { bypass_limit?: boolean; library?: string },
  onEvent: (event: FolderSyncStreamEvent) => void | Promise<void>,
  options?: { signal?: AbortSignal },
): Promise<void> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file, file.name);
  }
  form.append("bypass_limit", payload.bypass_limit ? "true" : "false");
  form.append("library", payload.library || "main");
  const response = await fetch(`${API_BASE}/documents/sync`, {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "text/event-stream" },
    body: form,
    signal: options?.signal,
  });
  if (!response.ok) {
    throw new Error("Folder sync failed");
  }
  if (!response.body) throw new Error("Missing stream body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r/g, "");
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const lines = chunk
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      if (lines.length === 0) continue;
      let eventName = "message";
      const dataLines: string[] = [];
      for (const line of lines) {
        if (line.startsWith("event:")) eventName = line.replace("event:", "").trim();
        if (line.startsWith("data:")) dataLines.push(line.replace("data:", "").trim());
      }
      if (dataLines.length === 0) continue;
      const dataText = dataLines.join("\n");
      let data: any = {};
      try {
        data = JSON.parse(dataText);
      } catch {
        data = { message: dataText };
      }
      await onEvent({ event: eventName as FolderSyncStreamEvent["event"], data } as FolderSyncStreamEvent);
    }
  }
}

export async function getDuplicateDocuments(library = "main"): Promise<DuplicateRecord[]> {
  return readJson<DuplicateRecord[]>(`/documents/duplicates?library=${encodeURIComponent(library)}`);
}

export async function dismissDuplicateFlag(importId: number): Promise<DuplicateRecord> {
  return readJson<DuplicateRecord>(`/documents/duplicates/${importId}/dismiss`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function confirmDuplicateFlag(importId: number): Promise<DuplicateRecord> {
  return readJson<DuplicateRecord>(`/documents/duplicates/${importId}/confirm`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function getHistoryEntries(params?: {
  category?: string;
  start_date?: string;
  end_date?: string;
  limit?: number;
}): Promise<{ entries: ChangeLogEntry[]; limit: number }> {
  const query = new URLSearchParams();
  if (params?.category) query.set("category", params.category);
  if (params?.start_date) query.set("start_date", params.start_date);
  if (params?.end_date) query.set("end_date", params.end_date);
  if (params?.limit) query.set("limit", String(params.limit));
  return readJson<{ entries: ChangeLogEntry[]; limit: number }>(`/history${query.toString() ? `?${query.toString()}` : ""}`);
}

export type ApiUsage = {
  today_count: number;
  daily_limit: number;
  remaining: number;
  enabled: boolean;
  reset_at: string;
  estimated_cost_today: number;
};

export type BatchCostEstimate = {
  estimated_calls: number;
  estimated_cost_usd: number;
  current_today: number;
  daily_limit: number;
  remaining: number;
  will_exceed_limit: boolean;
  overage_calls: number;
  overage_cost_usd: number;
};

export type ReanalyzeBatchResponse = {
  job_id: number;
  status: "queued";
};

export type ReanalyzeBatchPreview = {
  limit: number;
  documents: string[];
  total_unanalyzed: number;
  remaining_unanalyzed: number;
  api_usage: ApiUsage;
};

export async function reanalyzeBatch(payload: {
  limit?: number;
  bypass_limit?: boolean;
}): Promise<ReanalyzeBatchResponse> {
  const response = await fetch(`${API_BASE}/documents/reanalyze-batch-background`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  const parsed = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const error = new Error("Request failed") as Error & { status?: number; detail?: unknown };
    error.status = response.status;
    error.detail = (parsed as { detail?: unknown }).detail ?? parsed;
    throw error;
  }
  return parsed as ReanalyzeBatchResponse;
}

export type ReanalyzeStreamEvent =
  | { event: "start"; data: { total: number; documents: string[]; message?: string; api_usage?: ApiUsage } }
  | { event: "progress"; data: { completed: number; total: number; new_links: number; message?: string; last_document?: string | null } }
  | { event: "document"; data: { filename: string; status: "complete" | "error" | "skipped"; completed: number; total: number; new_links_total?: number; new_links?: number; reason?: string } }
  | { event: "cancelled"; data: { completed: number; total: number; new_links: number; errors: number; elapsed_seconds: number; api_usage?: ApiUsage } }
  | { event: "complete"; data: { completed: number; total: number; new_links: number; errors: number; elapsed_seconds: number; stopped_reason?: string | null; api_usage?: ApiUsage } }
  | { event: "error"; data: { message: string; type?: string } };

export async function streamReanalyzeBatch(
  payload: { bypass_limit?: boolean; resume_only?: boolean },
  onEvent: (event: ReanalyzeStreamEvent) => void | Promise<void>,
  options?: { signal?: AbortSignal },
): Promise<void> {
  const response = await fetch(`${API_BASE}/documents/reanalyze-batch`, {
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(payload),
    signal: options?.signal,
  });

  if (!response.ok) {
    const text = await response.text();
    let parsed: any = {};
    try {
      parsed = text ? JSON.parse(text) : {};
    } catch {
      parsed = {};
    }
    const error = new Error("Request failed") as Error & { status?: number; detail?: unknown };
    error.status = response.status;
    error.detail = parsed?.detail ?? parsed;
    throw error;
  }
  if (!response.body) {
    throw new Error("Missing stream body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r/g, "");
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const lines = chunk
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      if (lines.length === 0) continue;
      let eventName = "message";
      const dataLines: string[] = [];
      for (const line of lines) {
        if (line.startsWith("event:")) eventName = line.replace("event:", "").trim();
        if (line.startsWith("data:")) dataLines.push(line.replace("data:", "").trim());
      }
      if (dataLines.length === 0) continue;
      const dataText = dataLines.join("\n");
      let data: any = {};
      try {
        data = JSON.parse(dataText);
      } catch {
        data = { message: dataText };
      }
      await onEvent({ event: eventName as ReanalyzeStreamEvent["event"], data } as ReanalyzeStreamEvent);
    }
  }
}

export async function getReanalyzeBatchPreview(limit = 10): Promise<ReanalyzeBatchPreview> {
  return readJson<ReanalyzeBatchPreview>(`/documents/reanalyze-batch-preview?limit=${limit}`);
}

export async function queueReanalyzeEvidence(): Promise<{ queued: boolean; total_documents: number }> {
  const result = await reanalyzeBatch({ limit: 10, bypass_limit: false });
  return { queued: result.status === "queued", total_documents: 0 };
}

export async function cancelReanalyzeBatch(): Promise<{ cancel_requested: boolean }> {
  return readJson<{ cancel_requested: boolean }>("/documents/reanalyze-cancel", { method: "POST" });
}

export type ReanalyzeStatusResponse = {
  running: boolean;
  queued?: boolean;
  job_id?: number | null;
  completed: number;
  total: number;
  new_links: number;
  last_document?: string | null;
  remaining_unanalyzed?: number;
  stopped_reason?: string | null;
  api_usage?: ApiUsage;
  started_at: string | null;
  finished_at: string | null;
  message?: string;
};

export async function getReanalyzeStatus(): Promise<ReanalyzeStatusResponse> {
  return readJson<ReanalyzeStatusResponse>("/documents/reanalyze-status");
}

export async function processImportById(
  importId: number,
  payload: { bypass_limit?: boolean } = {},
): Promise<any> {
  const response = await fetch(`${API_BASE}/import/${importId}/process`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  const parsed = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const error = new Error("Request failed") as Error & { status?: number; detail?: unknown };
    error.status = response.status;
    error.detail = (parsed as { detail?: unknown }).detail ?? parsed;
    throw error;
  }
  return parsed;
}

export async function importSingleFile(payload: {
  file: File;
  data_date: string;
  notes?: string;
  library?: string;
}): Promise<{ import_id: number; filename: string; status: string }> {
  const form = new FormData();
  form.append("file", payload.file, payload.file.name);
  form.append("source_system", "Manual/Other");
  form.append("data_date", payload.data_date);
  if (payload.notes?.trim()) form.append("notes", payload.notes.trim());
  form.append("library", payload.library || "main");
  const response = await fetch(`${API_BASE}/import/file`, {
    method: "POST",
    cache: "no-store",
    body: form,
  });
  const text = await response.text();
  const parsed = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error("File import failed");
  }
  return parsed as { import_id: number; filename: string; status: string };
}

export async function importTextPayload(payload: {
  filename: string;
  content: string;
  data_date: string;
  notes?: string;
  library?: string;
}): Promise<{ import_id: number; filename: string; status: string }> {
  const response = await fetch(`${API_BASE}/import/text`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      filename: payload.filename,
      content: payload.content,
      source_system: "Manual/Other",
      data_date: payload.data_date,
      control_ids: [],
      framework: null,
      notes: payload.notes?.trim() || null,
      library: payload.library || "main",
    }),
  });
  const text = await response.text();
  const parsed = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error("Text import failed");
  }
  return parsed as { import_id: number; filename: string; status: string };
}

export async function getApiUsage(): Promise<ApiUsage> {
  return readJson<ApiUsage>("/settings/api-usage");
}

export async function getAllSettings(): Promise<Record<string, string>> {
  return readJson<Record<string, string>>("/settings/all");
}

export async function patchApiLimit(payload: { daily_limit: number }): Promise<ApiUsage> {
  return readJson<ApiUsage>("/settings/api-limit", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function patchApiEnabled(payload: { enabled: boolean }): Promise<ApiUsage> {
  return readJson<ApiUsage>("/settings/api-enabled", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function estimateBatchCost(payload: { num_calls: number }): Promise<BatchCostEstimate> {
  return readJson<BatchCostEstimate>("/settings/estimate-batch-cost", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function refreshEvidenceLinks(): Promise<{
  updated_controls: number;
  evidenced: number;
  reset: number;
}> {
  return readJson("/documents/refresh-links", { method: "POST" });
}

export async function searchDocuments(query: string): Promise<any[]> {
  return readJson<any[]>("/documents/search", { method: "POST", body: JSON.stringify({ query }) });
}

export type AuditProgramKey = "iso" | "cmmc" | "dpa" | "ato";

export type AuditProgramInfo = {
  audit_date: string | null;
  days_remaining: number | null;
  label: string;
  frameworks: string[];
  enabled: boolean;
};

export type AuditInfoPayload = {
  iso: AuditProgramInfo;
  cmmc: AuditProgramInfo;
  dpa: AuditProgramInfo;
  ato: AuditProgramInfo;
};

export async function getAuditInfo(): Promise<AuditInfoPayload> {
  return readJson("/settings/audit-info");
}

export async function patchAuditDates(payload: {
  iso_audit_date: string;
  cmmc_audit_date: string;
  dpa_audit_date?: string | null;
  ato_audit_date?: string | null;
}): Promise<AuditInfoPayload> {
  return readJson("/settings/audit-dates", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function patchAuditEnabled(payload: {
  audit: AuditProgramKey;
  enabled: boolean;
}): Promise<AuditInfoPayload> {
  return readJson("/settings/audit-enabled", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteDocument(documentId: string): Promise<{ status: string }> {
  return readJson(`/documents/${encodeURIComponent(documentId)}`, {
    method: "DELETE",
  });
}

export async function deleteDocumentWithOptions(
  documentId: string,
  payload: { force?: boolean },
): Promise<{ status: string; filename: string }> {
  const response = await fetch(`${API_BASE}/documents/${encodeURIComponent(documentId)}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  const raw = await response.text();
  let parsed: Record<string, unknown> = {};
  if (raw) {
    try {
      parsed = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      parsed = {};
    }
  }
  if (!response.ok) {
    const detail = parsed.detail;
    const detailMessage =
      typeof detail === "object" && detail !== null && "message" in detail && typeof (detail as { message?: unknown }).message === "string"
        ? ((detail as { message: string }).message)
        : undefined;
    const message =
      detailMessage ||
      (typeof detail === "string" ? detail : undefined) ||
      "Delete failed";
    const err = new Error(message) as Error & { status?: number; detail?: unknown };
    err.status = response.status;
    err.detail = detail;
    throw err;
  }
  return parsed as { status: string; filename: string };
}

export async function bulkDeleteDocuments(documentIds: string[]): Promise<{ deleted: number; failed: number }> {
  return readJson("/documents/bulk", {
    method: "DELETE",
    body: JSON.stringify({ document_ids: documentIds }),
  });
}

export async function getDocumentPreview(documentId: string): Promise<{
  id: string;
  filename: string;
  preview_text: string;
  doc_type: string;
  framework: string | null;
  control_ids: string[];
}> {
  return readJson(`/documents/${encodeURIComponent(documentId)}/preview`);
}

export async function getFrameworks(): Promise<any[]> {
  return readJson<any[]>("/frameworks");
}

export async function getFrameworkDetail(frameworkId: string): Promise<any> {
  return readJson<any>(`/frameworks/${frameworkId}`);
}

export async function getFrameworkControls(frameworkId: string): Promise<any[]> {
  return readJson<any[]>(`/frameworks/${frameworkId}/controls`);
}

export async function getControl(controlId: string): Promise<any> {
  return readJson<any>(`/controls/${controlId}`);
}

export async function patchControl(controlId: string, payload: Record<string, unknown>): Promise<any> {
  return readJson<any>(`/controls/${controlId}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export async function getAuditorChecklists(library = "main"): Promise<any[]> {
  return readJson<any[]>(`/auditor/checklists?library=${encodeURIComponent(library)}`);
}

export async function getAuditorChecklist(id: number, library = "main"): Promise<any> {
  return readJson<any>(`/auditor/checklists/${id}?library=${encodeURIComponent(library)}`);
}

export async function getAuditorChecklistSummary(id: number): Promise<any> {
  return readJson<any>(`/auditor/checklists/${id}/summary`);
}

export async function createAuditorChecklist(payload: Record<string, unknown>): Promise<any> {
  return readJson<any>("/auditor/checklists", { method: "POST", body: JSON.stringify(payload) });
}

export async function createAuditorChecklistItem(
  checklistId: number,
  payload: Record<string, unknown>,
): Promise<any> {
  return readJson<any>(`/auditor/checklists/${checklistId}/items`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function patchAuditorChecklistItem(
  checklistId: number,
  itemId: number,
  payload: Record<string, unknown>,
): Promise<any> {
  return readJson<any>(`/auditor/checklists/${checklistId}/items/${itemId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function generateAuditorItemResponse(
  checklistId: number,
  itemId: number,
  payload: { bypass_limit?: boolean } = {},
): Promise<any> {
  const response = await fetch(`${API_BASE}/auditor/checklists/${checklistId}/items/${itemId}/generate-response`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  const parsed = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const error = new Error("Request failed") as Error & { status?: number; detail?: unknown };
    error.status = response.status;
    error.detail = (parsed as { detail?: unknown }).detail ?? parsed;
    throw error;
  }
  return parsed;
}

export async function refreshAuditorEvidence(
  checklistId: number,
  payload: { bypass_limit?: boolean } = {},
): Promise<any> {
  const response = await fetch(`${API_BASE}/auditor/checklists/${checklistId}/refresh-evidence`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  const parsed = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const error = new Error("Request failed") as Error & { status?: number; detail?: unknown };
    error.status = response.status;
    error.detail = (parsed as { detail?: unknown }).detail ?? parsed;
    throw error;
  }
  return parsed;
}

export async function matchAuditorEvidence(
  checklistId: number,
  payload: { bypass_limit?: boolean } = {},
): Promise<any> {
  const response = await fetch(`${API_BASE}/auditor/checklists/${checklistId}/match-evidence`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  const parsed = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const error = new Error("Request failed") as Error & { status?: number; detail?: unknown };
    error.status = response.status;
    error.detail = (parsed as { detail?: unknown }).detail ?? parsed;
    throw error;
  }
  return parsed;
}

// --- Workforce ---

export async function getWorkforceStaff(): Promise<any[]> {
  return readJson<any[]>("/workforce/staff");
}

export async function createWorkforceStaff(payload: Record<string, unknown>): Promise<any> {
  return readJson<any>("/workforce/staff", { method: "POST", body: JSON.stringify(payload) });
}

export async function patchWorkforceStaff(staffId: number, payload: Record<string, unknown>): Promise<any> {
  return readJson<any>(`/workforce/staff/${staffId}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export async function deleteWorkforceStaff(staffId: number): Promise<any> {
  return readJson<any>(`/workforce/staff/${staffId}`, { method: "DELETE" });
}

export async function getWorkforcePursuits(): Promise<any[]> {
  return readJson<any[]>("/workforce/pursuits");
}

export async function getWorkforcePursuit(pursuitId: number): Promise<any> {
  return readJson<any>(`/workforce/pursuits/${pursuitId}`);
}

export async function createWorkforcePursuit(payload: Record<string, unknown>): Promise<any> {
  return readJson<any>("/workforce/pursuits", { method: "POST", body: JSON.stringify(payload) });
}

export async function patchWorkforcePursuit(pursuitId: number, payload: Record<string, unknown>): Promise<any> {
  return readJson<any>(`/workforce/pursuits/${pursuitId}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export async function deleteWorkforcePursuit(pursuitId: number): Promise<any> {
  return readJson<any>(`/workforce/pursuits/${pursuitId}`, { method: "DELETE" });
}

export async function runWorkforceGapAnalysis(
  pursuitId: number,
  includeCanaide = false,
): Promise<any> {
  const q = includeCanaide ? "?include_canaide=true" : "?include_canaide=false";
  return readJson<any>(`/workforce/pursuits/${pursuitId}/gap-analysis${q}`, { method: "POST" });
}

export async function getWorkforceAssignments(): Promise<any[]> {
  return readJson<any[]>("/workforce/assignments");
}

export async function createWorkforceAssignment(payload: Record<string, unknown>): Promise<any> {
  return readJson<any>("/workforce/assignments", { method: "POST", body: JSON.stringify(payload) });
}

export async function patchWorkforceAssignment(
  assignmentId: number,
  payload: Record<string, unknown>,
): Promise<any> {
  return readJson<any>(`/workforce/assignments/${assignmentId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteWorkforceAssignment(assignmentId: number): Promise<any> {
  return readJson<any>(`/workforce/assignments/${assignmentId}`, { method: "DELETE" });
}

export async function getWorkforceGaps(): Promise<any[]> {
  return readJson<any[]>("/workforce/gaps");
}

export async function createWorkforceGap(payload: Record<string, unknown>): Promise<any> {
  return readJson<any>("/workforce/gaps", { method: "POST", body: JSON.stringify(payload) });
}

export async function patchWorkforceGap(gapId: number, payload: Record<string, unknown>): Promise<any> {
  return readJson<any>(`/workforce/gaps/${gapId}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export async function deleteWorkforceGap(gapId: number): Promise<any> {
  return readJson<any>(`/workforce/gaps/${gapId}`, { method: "DELETE" });
}

export async function getWorkforceOvercommitment(includeCanaide = false): Promise<any> {
  const q = includeCanaide ? "?include_canaide=true" : "?include_canaide=false";
  return readJson<any>(`/workforce/overcommitment${q}`);
}

// --- Evidence ---

export async function getEvidenceList(params?: {
  page?: number;
  page_size?: number;
  framework?: string;
  control_id?: string;
}): Promise<{ page: number; page_size: number; total: number; items: any[] }> {
  const q = new URLSearchParams();
  if (params?.page) q.set("page", String(params.page));
  if (params?.page_size) q.set("page_size", String(params.page_size));
  if (params?.framework) q.set("framework", params.framework);
  if (params?.control_id) q.set("control_id", params.control_id);
  const suffix = q.toString() ? `?${q.toString()}` : "";
  return readJson(`/evidence${suffix}`);
}

export async function getEvidence(evidenceId: number, params?: { framework?: string; control_id?: string }): Promise<any> {
  const q = new URLSearchParams();
  if (params?.framework) q.set("framework", params.framework);
  if (params?.control_id) q.set("control_id", params.control_id);
  const suffix = q.toString() ? `?${q.toString()}` : "";
  return readJson<any>(`/evidence/${evidenceId}${suffix}`);
}

export async function patchEvidence(evidenceId: number, payload: Record<string, unknown>): Promise<any> {
  return readJson<any>(`/evidence/${evidenceId}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export async function patchEvidenceControl(
  evidenceId: number,
  controlId: string,
  payload: { display_name?: string; remove?: boolean },
): Promise<any> {
  return readJson<any>(`/evidence/${evidenceId}/controls/${controlId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function getEvidenceCorrections(evidenceId: number): Promise<{ evidence_id: number; items: any[] }> {
  return readJson(`/evidence/${evidenceId}/corrections`);
}

