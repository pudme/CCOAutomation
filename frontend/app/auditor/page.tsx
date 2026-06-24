"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
/* eslint-disable react-hooks/set-state-in-effect */
/* eslint-disable react-hooks/exhaustive-deps */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { ApiLimitOverrideDialog } from "@/components/shared/ApiLimitOverrideDialog";
import { ButtonSpinner, SkeletonCard, StatusMessage } from "@/components/shared/LoadingStates";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  estimateBatchCost,
  generateAuditorItemResponse,
  getApiUsage,
  getAuditorChecklist,
  getAuditorChecklists,
  getAuditorChecklistSummary,
  processImportById,
  getReanalyzeStatus,
  matchAuditorEvidence,
  patchAuditorChecklistItem,
  queueReanalyzeEvidence,
  refreshAuditorEvidence,
} from "@/lib/api";
import { useAsyncAction } from "@/lib/hooks";
import { formatLabel, formatStatus } from "@/lib/utils";

const STATUS_OPTIONS = ["open", "in_progress", "evidence_submitted", "satisfied", "not_applicable"];
const PRIORITY_OPTIONS = ["high", "medium", "low"];
const AUDIT_TYPE_OPTIONS = [
  "ISO Surveillance",
  "ISO Certification",
  "ISO Recertification",
  "CMMC Level 2 Assessment",
  "ISO 9001 Surveillance",
  "ISO 20000 Surveillance",
  "Other",
];
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8010";
const DEFAULT_HIDDEN_COLUMN_KEYS = new Set([
  "companyname",
  "audittype",
  "ofdocuments",
  "ofdocumentscompliant",
  "ofdocumentsnoncompliant",
  "ofdocumentspendingreview",
  "alertsorwarnings",
  "priority",
]);
const UPLOAD_PROCESSING_MESSAGES = [
  "Reading document and extracting checklist items...",
  "Identifying audit requests and mapped controls...",
  "Preparing checklist...",
];

function normalizeColumnKey(value: string): string {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");
}

export default function AuditorPage() {
  const pathname = usePathname();
  const activeLibrary = pathname.startsWith("/dpa/auditor") ? "dpa" : "main";
  const [checklists, setChecklists] = useState<any[]>([]);
  const [activeChecklistId, setActiveChecklistId] = useState<number | null>(null);
  const [activeChecklist, setActiveChecklist] = useState<any | null>(null);
  const [summary, setSummary] = useState<any | null>(null);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [processingMessage, setProcessingMessage] = useState("");
  const [pendingImportId, setPendingImportId] = useState<number | null>(null);
  const [expandedEvidenceItem, setExpandedEvidenceItem] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [priorityFilter, setPriorityFilter] = useState("all");
  const [controlFilter, setControlFilter] = useState("");
  const [connectionWarning, setConnectionWarning] = useState<string | null>(null);
  const [pollingError, setPollingError] = useState(false);
  const [reanalyzeStatus, setReanalyzeStatus] = useState<{
    running: boolean;
    completed: number;
    total: number;
  }>({ running: false, completed: 0, total: 0 });

  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [auditName, setAuditName] = useState(`Surveillance ${new Date().getFullYear()}`);
  const [auditType, setAuditType] = useState("ISO Surveillance");
  const [certBody, setCertBody] = useState("External Auditor");
  const [auditYear, setAuditYear] = useState(String(new Date().getFullYear()));
  const [mergeWithExisting, setMergeWithExisting] = useState(true);
  const [refreshingEvidence, setRefreshingEvidence] = useState(false);
  const [matchingEvidence, setMatchingEvidence] = useState(false);
  const [evidenceRunInFlight, setEvidenceRunInFlight] = useState<"match" | "refresh" | null>(null);
  const [lastEvidenceRunResult, setLastEvidenceRunResult] = useState<"idle" | "success" | "failed">("idle");
  const [savingItemId, setSavingItemId] = useState<number | null>(null);
  const [generatingResponseItemId, setGeneratingResponseItemId] = useState<number | null>(null);
  const [askClaudePendingId, setAskClaudePendingId] = useState<number | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [showOverrideDialog, setShowOverrideDialog] = useState(false);
  const [overrideEstimate, setOverrideEstimate] = useState<any | null>(null);
  const [overrideMessage, setOverrideMessage] = useState<string | null>(null);
  const [overrideAction, setOverrideAction] = useState<"match" | "refresh" | "upload" | null>(null);
  const [overrideChecklistId, setOverrideChecklistId] = useState<number | null>(null);
  const [overrideImportId, setOverrideImportId] = useState<number | null>(null);
  const [autoMatchStatusMessage, setAutoMatchStatusMessage] = useState<string | null>(null);
  const [columnVisibility, setColumnVisibility] = useState<Record<string, boolean>>({});
  const [showColumnsMenu, setShowColumnsMenu] = useState(false);
  const columnVisibilityHydratedKeyRef = useRef<string | null>(null);
  const dpaReloadTriggeredRef = useRef(false);
  const mutatingAction = useAsyncAction();

  const engagementKey = `${auditName.trim()}|${auditType.trim()}|${auditYear.trim()}`;
  const existingEngagement = useMemo(
    () =>
      checklists.find(
        (checklist) =>
          `${String(checklist.name || "").trim()}|${String(checklist.audit_type || "").trim()}|${String(checklist.audit_period_year || "").trim()}` ===
          engagementKey,
      ),
    [checklists, engagementKey],
  );

  async function refreshChecklists() {
    try {
      const data = await getAuditorChecklists(activeLibrary);
      setChecklists(data);
      setConnectionWarning(null);
      setPollingError(false);
      if (data.length > 0 && !activeChecklistId) {
        setActiveChecklistId(data[0].id);
      }
    } catch {
      setConnectionWarning("Could not connect to the compliance backend. Make sure the server is running on port 8010.");
      setChecklists([]);
    } finally {
      setInitialLoading(false);
    }
  }

  async function refreshActiveChecklist(id: number): Promise<{ detail: any; summaryData: any } | null> {
    try {
      const [detail, summaryData] = await Promise.all([
        getAuditorChecklist(id, activeLibrary),
        getAuditorChecklistSummary(id),
      ]);
      setActiveChecklist(detail);
      setSummary(summaryData);
      setConnectionWarning(null);
      setPollingError(false);
      return { detail, summaryData };
    } catch {
      setConnectionWarning("Could not connect to the compliance backend. Make sure the server is running on port 8010.");
      setActiveChecklist(null);
      setSummary(null);
      setPollingError(true);
      return null;
    }
  }

  async function forceRefreshChecklistData(id: number) {
    await refreshActiveChecklist(id);
    await refreshChecklists();
  }

  useEffect(() => {
    void refreshChecklists();
  }, [activeLibrary]);

  useEffect(() => {
    if (activeChecklistId) {
      void refreshActiveChecklist(activeChecklistId);
    }
  }, [activeChecklistId]);

  useEffect(() => {
    if (!activeChecklistId || !activeChecklist || pollingError) return;
    const status = activeChecklist.evidence_refresh_status;
    if (status !== "queued" && status !== "in_progress" && status !== "rate_limited") return;
    const timer = setInterval(() => {
      void refreshActiveChecklist(activeChecklistId).catch(() => {
        setConnectionWarning("Could not connect to the compliance backend. Make sure the server is running on port 8010.");
        setPollingError(true);
      });
    }, 3000);
    return () => clearInterval(timer);
  }, [activeChecklistId, activeChecklist?.evidence_refresh_status, pollingError]);

  useEffect(() => {
    if (!evidenceRunInFlight || !activeChecklist || !activeChecklistId) return;
    const status = activeChecklist.evidence_refresh_status;
    if (status === "complete") {
      setLastEvidenceRunResult("success");
      setAutoMatchStatusMessage(null);
      setMatchingEvidence(false);
      setActiveChecklist((prev: any | null) => (prev ? { ...prev, evidence_refresh_error: null } : prev));
      void forceRefreshChecklistData(activeChecklistId);
      if (evidenceRunInFlight === "match") {
        const matched = (summary?.counts_by_status?.evidence_submitted || 0) + (summary?.counts_by_status?.in_progress || 0);
        toast.success(`Matching complete - ${matched} requests now have evidence`);
      } else {
        toast.success("Evidence refresh complete.");
      }
      setEvidenceRunInFlight(null);
      return;
    }
    if (status === "failed" || status === "rate_limited") {
      setLastEvidenceRunResult("failed");
      toast.error(activeChecklist.evidence_refresh_error || "Evidence matching did not complete.");
      setAutoMatchStatusMessage(null);
      setMatchingEvidence(false);
      setEvidenceRunInFlight(null);
    }
  }, [activeChecklist, activeChecklistId, evidenceRunInFlight, summary]);

  useEffect(() => {
    if (activeLibrary !== "dpa" || initialLoading || !activeChecklist) return;
    if (dpaReloadTriggeredRef.current) return;
    const storageKey = "dpa_auditor_hard_reload_v1";
    const alreadyReloaded = window.sessionStorage.getItem(storageKey) === "1";
    if (alreadyReloaded) {
      dpaReloadTriggeredRef.current = true;
      return;
    }
    dpaReloadTriggeredRef.current = true;
    window.sessionStorage.setItem(storageKey, "1");
    window.location.reload();
  }, [activeChecklist, activeLibrary, initialLoading]);

  useEffect(() => {
    if (!uploading) {
      return;
    }
    let idx = 0;
    setProcessingMessage(UPLOAD_PROCESSING_MESSAGES[idx]);
    const timer = setInterval(() => {
      idx = (idx + 1) % UPLOAD_PROCESSING_MESSAGES.length;
      setProcessingMessage(UPLOAD_PROCESSING_MESSAGES[idx]);
    }, 5000);
    return () => clearInterval(timer);
  }, [uploading]);

  useEffect(() => {
    if (!uploading && !pendingImportId) {
      setProcessingMessage("");
    }
  }, [uploading, pendingImportId]);

  useEffect(() => {
    if (!pendingImportId) return;
    const startedAt = Date.now();
    const timer = setInterval(async () => {
      if (Date.now() - startedAt > 180000) {
        setUploading(false);
        setPendingImportId(null);
        setUploadError("Processing timed out — the server is taking too long. Please try again.");
        clearInterval(timer);
        return;
      }
      let importRow: any;
      try {
        const response = await fetch(`${API_BASE}/import/${pendingImportId}/status`);
        if (!response.ok) {
          throw new Error(await response.text());
        }
        importRow = (await response.json()) as any;
      } catch {
        setUploading(false);
        setPendingImportId(null);
        setUploadError("Could not connect to the compliance backend. Make sure the server is running on port 8010.");
        clearInterval(timer);
        return;
      }
      if (importRow.status === "failed") {
        setUploading(false);
        setPendingImportId(null);
        setUploadError(importRow.error_message || "Import failed.");
        clearInterval(timer);
        return;
      }
      const importErrorMessage = String(importRow.error_message || "");
      if (
        importRow.status === "queued" &&
        importErrorMessage.toLowerCase().includes("daily api limit")
      ) {
        setUploading(false);
        setPendingImportId(null);
        setOverrideImportId(Number(importRow.import_id || pendingImportId));
        setOverrideAction("upload");
        try {
          const estimate = await estimateBatchCost({ num_calls: 1 });
          setOverrideEstimate(estimate);
        } catch {
          const usage = await getApiUsage();
          setOverrideEstimate({
            estimated_calls: 1,
            estimated_cost_usd: 0.0002,
            current_today: usage.today_count,
            daily_limit: usage.daily_limit,
            remaining: usage.remaining,
            will_exceed_limit: usage.remaining < 1,
            overage_calls: usage.remaining < 1 ? 1 - usage.remaining : 0,
            overage_cost_usd: usage.remaining < 1 ? 0.0002 : 0,
          });
        }
        setOverrideMessage(
          "The document upload requires an AI analysis call which has hit today's API limit. Would you like to proceed anyway?",
        );
        setShowOverrideDialog(true);
        clearInterval(timer);
        return;
      }
      if (importRow.status !== "complete") return;
      if (importRow.detected_type !== "auditor_checklist") {
        setUploading(false);
        setUploadError("Detection returned unknown. You can force override to auditor_checklist and retry.");
        clearInterval(timer);
        return;
      }
      const latestChecklists = await getAuditorChecklists(activeLibrary);
      const linked = latestChecklists.find((checklist) => checklist.source_import_id === pendingImportId);
      const engagementMatch =
        linked ||
        latestChecklists.find(
          (checklist) =>
            String(checklist.name || "").trim() === auditName.trim() &&
            String(checklist.audit_type || "").trim() === auditType.trim() &&
            String(checklist.audit_period_year || "").trim() === auditYear.trim(),
        );
      const targetChecklistId = engagementMatch?.id ?? null;
      if (linked) {
        setChecklists(latestChecklists);
        setActiveChecklistId(linked.id);
      } else {
        await refreshChecklists();
      }
      if (targetChecklistId) {
        await refreshActiveChecklist(targetChecklistId);
      }
      setShowUploadModal(false);
      setUploading(false);
      setPendingImportId(null);
      setProcessingMessage("");
      setSelectedFile(null);
      if (targetChecklistId) {
        setAutoMatchStatusMessage("Checklist uploaded — now matching evidence to requests...");
        await startMatchEvidence(targetChecklistId, false);
      }
      clearInterval(timer);
    }, 2500);
    return () => clearInterval(timer);
  }, [pendingImportId, auditName, auditType, auditYear]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const status = await getReanalyzeStatus();
        if (cancelled) return;
        setReanalyzeStatus({
          running: Boolean(status.running),
          completed: Number(status.completed || 0),
          total: Number(status.total || 0),
        });
      } catch {
        if (!cancelled) {
          setReanalyzeStatus({ running: false, completed: 0, total: 0 });
        }
      }
    };
    void load();
    const timer = setInterval(() => {
      void load();
    }, 10000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  async function uploadAuditorFile() {
    if (!selectedFile) {
      setUploadError("Choose a file first.");
      return;
    }
    if (!auditName.trim()) {
      setUploadError("Audit Name is required.");
      return;
    }
    setUploading(true);
    setUploadError(null);
    setProcessingMessage(UPLOAD_PROCESSING_MESSAGES[0]);
    const form = new FormData();
    form.append("file", selectedFile);
    form.append("source_system", "Auditor");
    form.append("data_date", `${auditYear}-01-01`);
    form.append("auditor_engagement_name", auditName.trim());
    form.append("auditor_engagement_type", auditType.trim());
    form.append("auditor_certification_body", certBody.trim());
    form.append("auditor_period_year", auditYear.trim());
    form.append("auditor_merge_with_existing", String(mergeWithExisting));
    form.append("library", activeLibrary);
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 180000);
    let response: Response;
    try {
      response = await fetch(`${API_BASE}/import/file`, { method: "POST", body: form, signal: controller.signal });
    } catch (error) {
      setUploading(false);
      const maybeAbort = error as { name?: string };
      if (maybeAbort?.name === "AbortError") {
        setUploadError("Upload timed out — the document may be too large or the server is busy. Please try again.");
      } else {
        setUploadError("Upload failed — check your connection and try again.");
      }
      return;
    } finally {
      window.clearTimeout(timeoutId);
    }
    if (!response.ok) {
      setUploading(false);
      const details = (await response.text()) || "Server returned an error while uploading.";
      setUploadError(`Upload failed — ${details}`);
      return;
    }
    const payload = (await response.json()) as { import_id?: number };
    if (!payload.import_id) {
      setUploading(false);
      setUploadError("Upload failed — server response did not include an import ID. Please retry.");
      return;
    }
    setPendingImportId(payload.import_id);
  }

  async function deleteTab(checklist: any) {
    const confirmed = window.confirm(`Delete ${checklist.name} and all its checklist items? This cannot be undone.`);
    if (!confirmed) return;
    const response = await fetch(`${API_BASE}/auditor/checklists/${checklist.id}`, { method: "DELETE" });
    if (!response.ok) {
      setUploadError("Failed to delete checklist tab.");
      return;
    }
    if (activeChecklistId === checklist.id) setActiveChecklistId(null);
    await refreshChecklists();
  }

  async function deleteSourceFile(importId: number, filename: string) {
    if (!activeChecklist) return;
    const confirmed = window.confirm(`Delete source file ${filename} and its checklist items?`);
    if (!confirmed) return;
    const response = await fetch(`${API_BASE}/auditor/checklists/${activeChecklist.id}/source-files/${importId}`, {
      method: "DELETE",
    });
    if (!response.ok) {
      setUploadError("Failed to delete source file items.");
      return;
    }
    await refreshActiveChecklist(activeChecklist.id);
  }

  async function generateResponseForItem(itemId: number) {
    if (!activeChecklist) return;
    setGeneratingResponseItemId(itemId);
    try {
      await generateAuditorItemResponse(activeChecklist.id, itemId);
      await refreshActiveChecklist(activeChecklist.id);
      toast.success("Our Response generated.");
    } catch (error) {
      const typed = error as Error & { detail?: any };
      const detailMessage =
        typeof typed.detail === "string"
          ? typed.detail
          : (typed.detail?.detail as string | undefined);
      toast.error(detailMessage || typed.message || "Failed to generate response.");
    } finally {
      setGeneratingResponseItemId(null);
    }
  }

  async function startRefreshEvidence(checklistId: number, bypass: boolean) {
    setRefreshingEvidence(true);
    setEvidenceRunInFlight("refresh");
    setLastEvidenceRunResult("idle");
    setActiveChecklist((prev: any | null) => (prev ? { ...prev, evidence_refresh_error: null } : prev));
    try {
      await refreshAuditorEvidence(checklistId, { bypass_limit: bypass });
      await forceRefreshChecklistData(checklistId);
      toast.success("Evidence refresh queued.");
    } catch (error) {
      const typed = error as Error & { status?: number; detail?: any };
      if (typed.status === 402 && typed.detail) {
        setEvidenceRunInFlight(null);
        setOverrideEstimate(typed.detail);
        const requests = summary?.total_items ?? activeChecklist?.items?.length ?? 0;
        const usage = await getApiUsage();
        const estimate = await estimateBatchCost({ num_calls: Math.max(requests, 1) });
        setOverrideMessage(
          `This will analyze ${requests} requests using approximately ${requests} API calls at Haiku pricing (~$${estimate.estimated_cost_usd.toFixed(2)}). You have ${usage.remaining} calls remaining today. Proceed?`,
        );
        setOverrideAction("refresh");
        setOverrideChecklistId(checklistId);
        setShowOverrideDialog(true);
        return;
      }
      setLastEvidenceRunResult("failed");
      setEvidenceRunInFlight(null);
      toast.error(typed.message || "Failed to refresh evidence.");
    } finally {
      setRefreshingEvidence(false);
    }
  }

  const filteredItems = useMemo(() => {
    if (!activeChecklist?.items) return [];
    return activeChecklist.items.filter((item: any) => {
      if (statusFilter !== "all" && item.status !== statusFilter) return false;
      if (priorityFilter !== "all" && item.priority !== priorityFilter) return false;
      if (controlFilter.trim() && !(item.control_ids || []).some((id: string) => id.includes(controlFilter.trim()))) {
        return false;
      }
      return true;
    });
  }, [activeChecklist, statusFilter, priorityFilter, controlFilter]);

  const fieldsFound: string[] = activeChecklist?.fields_found || [];
  const normalizedFields = fieldsFound.map((field) => field.toLowerCase());
  const showReference =
    normalizedFields.some((field) => field.includes("reference") || field.includes("rid") || field.includes("id"));
  const showTitle = normalizedFields.some(
    (field) => field.includes("title") || field.includes("category") || field.includes("checklist item"),
  );
  const dynamicRawColumns = fieldsFound.filter((field) => {
    const lower = field.toLowerCase();
    if (lower.includes("reference") || lower.includes("rid") || lower.includes("id")) return false;
    if (lower.includes("description") || lower.includes("information requested")) return false;
    if (lower.includes("title") || lower.includes("category") || lower.includes("checklist item")) return false;
    return true;
  });

  const columnStorageKey = activeChecklist ? `auditor_column_visibility_${activeChecklist.id}` : null;
  const dynamicColumnOptions = useMemo(
    () =>
      dynamicRawColumns.map((field) => ({
        key: `raw:${field}`,
        label: field,
        defaultVisible: !DEFAULT_HIDDEN_COLUMN_KEYS.has(normalizeColumnKey(field)),
      })),
    [dynamicRawColumns],
  );
  const defaultColumnVisibility = useMemo(() => {
    const defaults: Record<string, boolean> = {
      reference_id: true,
      title_category: true,
      priority: false,
    };
    for (const option of dynamicColumnOptions) {
      defaults[option.key] = option.defaultVisible;
    }
    return defaults;
  }, [dynamicColumnOptions]);
  const showReferenceColumn = showReference && (columnVisibility.reference_id ?? true);
  const showTitleColumn = showTitle && (columnVisibility.title_category ?? true);
  const showPriorityColumn = columnVisibility.priority ?? false;
  const visibleDynamicRawColumns = dynamicRawColumns.filter(
    (field) => columnVisibility[`raw:${field}`] ?? !DEFAULT_HIDDEN_COLUMN_KEYS.has(normalizeColumnKey(field)),
  );

  useEffect(() => {
    if (!columnStorageKey) return;
    if (columnVisibilityHydratedKeyRef.current === columnStorageKey) {
      return;
    }
    let next = defaultColumnVisibility;
    const raw = window.localStorage.getItem(columnStorageKey);
    if (raw) {
      try {
        const parsed = JSON.parse(raw) as Record<string, boolean>;
        next = { ...defaultColumnVisibility, ...parsed };
      } catch {
        next = defaultColumnVisibility;
      }
    }
    columnVisibilityHydratedKeyRef.current = columnStorageKey;
    setColumnVisibility(next);
  }, [columnStorageKey, defaultColumnVisibility]);

  useEffect(() => {
    if (!columnStorageKey || Object.keys(columnVisibility).length === 0) return;
    setColumnVisibility((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const [key, defaultValue] of Object.entries(defaultColumnVisibility)) {
        if (next[key] === undefined) {
          next[key] = defaultValue;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [columnStorageKey, defaultColumnVisibility]);

  useEffect(() => {
    if (!columnStorageKey || Object.keys(columnVisibility).length === 0) return;
    window.localStorage.setItem(columnStorageKey, JSON.stringify(columnVisibility));
  }, [columnStorageKey, columnVisibility]);

  function getRawFieldValue(item: any, fieldName: string) {
    const raw = item.raw_fields || {};
    if (raw[fieldName] !== undefined) return raw[fieldName];
    const match = Object.keys(raw).find((key) => key.toLowerCase() === fieldName.toLowerCase());
    return match ? raw[match] : "";
  }

  function getDisplayRawFieldValue(item: any, fieldName: string): string {
    const rawValue = getRawFieldValue(item, fieldName);
    const stripped = String(rawValue ?? "")
      .replace(/<[^>]*>/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (!stripped) return "—";
    if (stripped.toLowerCase() === "no documents submitted") return "—";
    return stripped;
  }

  function getDisplayDescription(item: any): string {
    const raw = item.raw_fields || {};
    const requestText =
      raw["Information Requested"] ||
      raw["information requested"] ||
      raw["description"] ||
      raw["Description"];
    if (requestText) return String(requestText);
    const description = String(item.description || "");
    if (description.includes(" | ")) {
      const parts = description.split(" | ").map((part: string) => part.trim());
      if (parts.length >= 5) return parts[4];
    }
    return description;
  }

  function getDisplayTitle(item: any): string {
    return (
      getRawFieldValue(item, "title") ||
      getRawFieldValue(item, "category") ||
      getRawFieldValue(item, "IR Checklist Item") ||
      "—"
    );
  }

  const evidenceSubmittedOrSatisfied =
    (summary?.counts_by_status?.evidence_submitted || 0) + (summary?.counts_by_status?.satisfied || 0);
  const progressPercent = summary?.total_items ? Math.round((evidenceSubmittedOrSatisfied / summary.total_items) * 100) : 0;
  const allItemsOpen =
    (summary?.total_items ?? 0) > 0 &&
    (summary?.counts_by_status?.open ?? 0) === (summary?.total_items ?? 0);
  const noEvidenceLoaded = (summary?.evidence_item_count ?? 0) === 0;
  const matchingProgressText = `Matching ${summary?.evidence_item_count ?? 0} documents against ${summary?.total_items ?? activeChecklist?.items?.length ?? 0} requests...`;

  async function startMatchEvidence(checklistId: number, bypass: boolean) {
    setMatchingEvidence(true);
    setEvidenceRunInFlight("match");
    setLastEvidenceRunResult("idle");
    setActiveChecklist((prev: any | null) => (prev ? { ...prev, evidence_refresh_error: null } : prev));
    try {
      await matchAuditorEvidence(checklistId, { bypass_limit: bypass });
      await forceRefreshChecklistData(checklistId);
    } catch (error) {
      const typed = error as Error & { status?: number; detail?: any };
      setMatchingEvidence(false);
      if (typed.status === 402 && typed.detail) {
        setEvidenceRunInFlight(null);
        setOverrideEstimate(typed.detail);
        const requests = summary?.total_items ?? activeChecklist?.items?.length ?? 0;
        const usage = await getApiUsage();
        const estimate = await estimateBatchCost({ num_calls: Math.max(requests, 1) });
        setOverrideMessage(
          `This will analyze ${requests} requests using approximately ${requests} API calls at Haiku pricing (~$${estimate.estimated_cost_usd.toFixed(2)}). You have ${usage.remaining} calls remaining today. Proceed?`,
        );
        setOverrideAction("match");
        setOverrideChecklistId(checklistId);
        setShowOverrideDialog(true);
        return;
      }
      setLastEvidenceRunResult("failed");
      setEvidenceRunInFlight(null);
      toast.error(typed.message || "Failed to start evidence matching.");
    }
  }
  const shouldShowRefreshError =
    Boolean(activeChecklist?.evidence_refresh_error) &&
    (lastEvidenceRunResult === "failed" ||
      (lastEvidenceRunResult === "idle" &&
        (activeChecklist?.evidence_refresh_status === "failed" ||
          activeChecklist?.evidence_refresh_status === "rate_limited")));


  async function forceProcessQueuedImport(importId: number, bypass: boolean) {
    await processImportById(importId, { bypass_limit: bypass });
    setUploading(true);
    setPendingImportId(importId);
    setProcessingMessage(UPLOAD_PROCESSING_MESSAGES[0]);
  }

  const uploadForm = (
    <div className="space-y-3">
      <label className="block text-sm">
        <span className="mb-1 block text-xs text-muted-foreground">Audit Name</span>
        <input
          className="w-full rounded border px-3 py-2 text-sm"
          value={auditName}
          onChange={(event) => setAuditName(event.target.value)}
          placeholder="ISO Surveillance 2026"
        />
      </label>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="block text-sm">
          <span className="mb-1 block text-xs text-muted-foreground">Audit Type</span>
          <select
            className="w-full rounded border px-3 py-2 text-sm"
            value={auditType}
            onChange={(event) => setAuditType(event.target.value)}
          >
            {AUDIT_TYPE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-xs text-muted-foreground">Audit Period (Year)</span>
          <input
            type="number"
            min="2000"
            max="2100"
            className="w-full rounded border px-3 py-2 text-sm"
            value={auditYear}
            onChange={(event) => setAuditYear(event.target.value)}
          />
        </label>
      </div>
      <label className="block text-sm">
        <span className="mb-1 block text-xs text-muted-foreground">Certification Body / Auditor</span>
        <input
          className="w-full rounded border px-3 py-2 text-sm"
          value={certBody}
          onChange={(event) => setCertBody(event.target.value)}
          placeholder="BSI"
        />
      </label>
      {existingEngagement ? (
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={mergeWithExisting}
            onChange={(event) => setMergeWithExisting(event.target.checked)}
          />
          Matching engagement exists ({existingEngagement.name}). Merge into existing engagement.
        </label>
      ) : null}
      <div
        className="rounded-lg border-2 border-dashed p-6 text-center"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          const file = event.dataTransfer.files?.[0];
          if (file) setSelectedFile(file);
        }}
      >
        <p className="text-sm font-medium">Upload your auditor&apos;s information request document</p>
        <p className="mt-1 text-xs text-muted-foreground">Any format accepted — CSV, Excel, PDF, Word, or text</p>
        <div className="mt-3">
          <label className="inline-flex cursor-pointer items-center rounded border px-3 py-2 text-sm">
            Choose File
            <input
              type="file"
              className="hidden"
              accept=".csv,.xlsx,.xls,.pdf,.docx,.txt"
              onChange={(event) => setSelectedFile(event.target.files?.[0] || null)}
            />
          </label>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">{selectedFile ? selectedFile.name : "No file selected."}</p>
      </div>
      <div className="flex items-center gap-2">
        <Button onClick={uploadAuditorFile} disabled={uploading}>
          <span className="flex items-center gap-2">
            {uploading ? <ButtonSpinner /> : null}
            {uploading ? "Processing document..." : "Upload"}
          </span>
        </Button>
        {processingMessage ? <p className="text-xs text-muted-foreground">{processingMessage}</p> : null}
      </div>
      {uploadError ? (
        <div className="flex items-center gap-2">
          <p className="text-xs text-red-700">{uploadError}</p>
          <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => void uploadAuditorFile()} disabled={uploading}>
            Retry
          </Button>
        </div>
      ) : null}
    </div>
  );

  if (checklists.length === 0) {
    return (
      <div className="space-y-4">
        {connectionWarning ? (
          <Card className="mx-auto max-w-3xl border-amber-300 bg-amber-50 p-4 text-amber-900">
            {connectionWarning}
          </Card>
        ) : null}
        <Card className="mx-auto max-w-3xl p-8">{initialLoading ? <SkeletonCard lines={4} /> : uploadForm}</Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {connectionWarning ? (
        <Card className="border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">{connectionWarning}</Card>
      ) : null}
      {initialLoading ? <SkeletonCard lines={3} /> : null}
      {autoMatchStatusMessage ? <StatusMessage type="info" message={autoMatchStatusMessage} /> : null}
      {allItemsOpen && noEvidenceLoaded ? (
        <Card className="border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">
          No compliance evidence has been loaded yet. Import your policy documents, audit reports, and evidence files to begin matching against these requests.
        </Card>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-2">
          {checklists.map((checklist) => (
            <div key={checklist.id} className={`flex items-center rounded border ${activeChecklistId === checklist.id ? "bg-primary text-primary-foreground" : ""}`}>
              <button
                onClick={() => setActiveChecklistId(checklist.id)}
                className="px-3 py-2 text-sm"
              >
                {String(checklist.engagement_name || checklist.name || `${formatLabel(checklist.audit_type)} ${checklist.audit_period_year || ""}`)}
              </button>
              <button
                className="px-2 py-2 text-xs opacity-80 hover:opacity-100"
                onClick={() => void deleteTab(checklist)}
                title={`Delete ${checklist.name}`}
              >
                x
              </button>
            </div>
          ))}
        </div>
        <Button onClick={() => setShowUploadModal(true)}>Upload New Checklist</Button>
      </div>

      {activeChecklist ? (
        <>
          <Card className="p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold">{activeChecklist.name}</h2>
                <p className="text-sm text-muted-foreground">
                  Audit Type: {activeChecklist.audit_type || "N/A"} | Period: {activeChecklist.audit_period_year || "N/A"} | Auditor:{" "}
                  {activeChecklist.auditor_name || "N/A"}
                </p>
              </div>
              <div className="min-w-[320px]">
                <p className="text-xs text-muted-foreground">Overall Progress</p>
                <div className="mt-1 h-2 rounded bg-muted">
                  <div className="h-2 rounded bg-green-600" style={{ width: `${progressPercent}%` }} />
                </div>
                <p className="mt-1 text-xs">
                  {progressPercent}% coverage ({summary?.counts_by_status?.satisfied ?? 0} satisfied,{" "}
                  {summary?.counts_by_status?.in_progress ?? 0} in progress, {summary?.counts_by_status?.open ?? 0} open)
                </p>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {STATUS_OPTIONS.map((status) => (
                <span key={status} className="rounded border px-2 py-1 text-xs">
                  {formatStatus(status)}: {summary?.counts_by_status?.[status] ?? 0}
                </span>
              ))}
              <span className="text-xs text-muted-foreground">
                Refresh status: {formatStatus(activeChecklist.evidence_refresh_status || "idle")}
              </span>
              <span className="text-xs text-muted-foreground">
                Last refreshed: {activeChecklist.last_evidence_refresh || "never"}
              </span>
              {shouldShowRefreshError ? (
                <span className="text-xs text-red-700">{activeChecklist.evidence_refresh_error}</span>
              ) : null}
              {matchingEvidence ? (
                <span className="text-xs text-muted-foreground">{matchingProgressText}</span>
              ) : null}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                className="h-7 border-slate-700 px-2 text-xs text-slate-900 hover:bg-slate-100"
                disabled={refreshingEvidence}
                onClick={async () => void startRefreshEvidence(activeChecklist.id, false)}
                title="Fast - no AI required"
              >
                <span className="flex items-center gap-2">
                  {refreshingEvidence ? <ButtonSpinner /> : null}
                  {refreshingEvidence ? "Syncing..." : "Sync Control Status"}
                </span>
              </Button>
              <span className="text-xs text-muted-foreground">Fast — no AI required</span>
              <Button
                size="sm"
                className="h-7 bg-slate-900 px-2 text-xs text-white hover:bg-slate-800"
                disabled={matchingEvidence}
                onClick={async () => {
                  const requests = summary?.total_items ?? activeChecklist.items?.length ?? 0;
                  const usage = await getApiUsage();
                  if (!requests) {
                    toast.error("No checklist requests to match.");
                    return;
                  }
                  if (usage.remaining < requests) {
                    const estimate = await estimateBatchCost({ num_calls: Math.max(requests, 1) });
                    setOverrideEstimate(estimate);
                    setOverrideMessage(
                      `This will analyze ${requests} requests using approximately ${requests} API calls at Haiku pricing (~$${estimate.estimated_cost_usd.toFixed(2)}). You have ${usage.remaining} calls remaining today. Proceed?`,
                    );
                    setShowOverrideDialog(true);
                    return;
                  }
                  await startMatchEvidence(activeChecklist.id, false);
                }}
              >
                <span className="flex items-center gap-2">
                  {matchingEvidence ? <ButtonSpinner /> : null}
                  {matchingEvidence ? "Matching..." : "Match Evidence to Requests"}
                </span>
              </Button>
              <Button
                size="sm"
                variant="secondary"
                className="h-6 bg-slate-100 px-2 text-[11px] text-slate-800 hover:bg-slate-200"
                disabled={reanalyzeStatus.running}
                onClick={async () => {
                  const confirmed = window.confirm(
                    "This will run a full AI content analysis on all imported documents and update their control mappings. This runs in the background and may take 10-15 minutes for large libraries. Continue?",
                  );
                  if (!confirmed) return;
                  const result = await queueReanalyzeEvidence();
                  setReanalyzeStatus((prev) => ({
                    running: true,
                    completed: prev.running ? prev.completed : 0,
                    total: result.total_documents || prev.total,
                  }));
                }}
              >
                {reanalyzeStatus.running ? "Reanalysis Running..." : "Re-Run AI Analysis"}
              </Button>
              <span className="text-xs text-muted-foreground">
                Re-runs Claude analysis on all documents. Uses API credits.
              </span>
              {reanalyzeStatus.running ? (
                <span className="text-xs text-muted-foreground">
                  Analyzing documents in background — {reanalyzeStatus.completed} complete
                </span>
              ) : null}
            </div>
            {activeChecklist.source_files?.length ? (
              <div className="mt-4 space-y-2">
                <p className="text-xs font-medium text-muted-foreground">Source Files</p>
                {activeChecklist.source_files.map((source: any) => (
                  <div key={source.import_id} className="flex items-center justify-between rounded border px-3 py-2">
                    <span className="text-xs">
                      {source.filename} ({source.item_count} items)
                    </span>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 px-2 text-xs"
                      onClick={() => void deleteSourceFile(source.import_id, source.filename)}
                    >
                      Delete File
                    </Button>
                  </div>
                ))}
              </div>
            ) : null}
          </Card>

          <Card className="p-4">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <select className="rounded border px-2 py-1 text-sm" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                <option value="all">All Statuses</option>
                {STATUS_OPTIONS.map((status) => (
                  <option key={status} value={status}>
                    {formatStatus(status)}
                  </option>
                ))}
              </select>
              <select className="rounded border px-2 py-1 text-sm" value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)}>
                <option value="all">All Priorities</option>
                {PRIORITY_OPTIONS.map((priority) => (
                  <option key={priority} value={priority}>
                    {formatStatus(priority)}
                  </option>
                ))}
              </select>
              <input
                className="rounded border px-2 py-1 text-sm"
                placeholder="Filter by control id"
                value={controlFilter}
                onChange={(e) => setControlFilter(e.target.value)}
              />
              <div className="relative ml-auto">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 px-2 text-xs"
                  onClick={() => setShowColumnsMenu((prev) => !prev)}
                >
                  Columns
                </Button>
                {showColumnsMenu ? (
                  <div className="absolute right-0 z-20 mt-2 max-h-80 w-64 overflow-y-auto rounded border bg-white p-3 shadow-md">
                    <p className="mb-2 text-xs font-semibold text-muted-foreground">Visible columns</p>
                    {showReference ? (
                      <label className="mb-1 flex items-center gap-2 text-xs">
                        <input
                          type="checkbox"
                          checked={columnVisibility.reference_id ?? true}
                          onChange={(e) =>
                            setColumnVisibility((prev) => ({ ...prev, reference_id: e.target.checked }))
                          }
                        />
                        Reference ID
                      </label>
                    ) : null}
                    {showTitle ? (
                      <label className="mb-1 flex items-center gap-2 text-xs">
                        <input
                          type="checkbox"
                          checked={columnVisibility.title_category ?? true}
                          onChange={(e) =>
                            setColumnVisibility((prev) => ({ ...prev, title_category: e.target.checked }))
                          }
                        />
                        Title/Category
                      </label>
                    ) : null}
                    {dynamicColumnOptions.map((option) => (
                      <label key={option.key} className="mb-1 flex items-center gap-2 text-xs">
                        <input
                          type="checkbox"
                          checked={columnVisibility[option.key] ?? option.defaultVisible}
                          onChange={(e) =>
                            setColumnVisibility((prev) => ({ ...prev, [option.key]: e.target.checked }))
                          }
                        />
                        {option.label}
                      </label>
                    ))}
                    <label className="mt-1 flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={columnVisibility.priority ?? false}
                        onChange={(e) =>
                          setColumnVisibility((prev) => ({ ...prev, priority: e.target.checked }))
                        }
                      />
                      Priority
                    </label>
                  </div>
                ) : null}
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left">
                    {showReferenceColumn ? <th className="p-2">Reference ID</th> : null}
                    {showTitleColumn ? <th className="p-2">Title/Category</th> : null}
                    <th className="p-2">Description</th>
                    {visibleDynamicRawColumns.map((field) => (
                      <th key={field} className="p-2">
                        {field}
                      </th>
                    ))}
                    <th className="p-2">Mapped Controls</th>
                    <th className="p-2">Evidence Found</th>
                    <th className="p-2">Status</th>
                    <th className="min-w-[300px] p-2">Our Response</th>
                    {showPriorityColumn ? <th className="p-2">Priority</th> : null}
                    <th className="p-2">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredItems.map((item: any) => (
                    <tr key={item.id} className="border-b align-top">
                      {showReferenceColumn ? <td className="p-2">{item.item_number}</td> : null}
                      {showTitleColumn ? <td className="p-2 text-xs">{getDisplayTitle(item)}</td> : null}
                      <td className="p-2">
                        <div className="whitespace-pre-wrap break-words">{getDisplayDescription(item)}</div>
                      </td>
                      {visibleDynamicRawColumns.map((field) => (
                        <td key={`${item.id}-${field}`} className="p-2 text-xs">
                          {getDisplayRawFieldValue(item, field)}
                        </td>
                      ))}
                      <td className="p-2 text-xs">
                        <div className="whitespace-pre-wrap break-words">{(item.control_ids || []).join(", ") || "None"}</div>
                      </td>
                      <td className="p-2">
                        {(item.evidence_ids || []).length > 0 ? (
                          <button
                            className="rounded bg-green-100 px-2 py-1 text-xs text-green-800"
                            onClick={() => setExpandedEvidenceItem((prev) => (prev === item.id ? null : item.id))}
                          >
                            {(item.evidence_ids || []).length} found
                          </button>
                        ) : (
                          <span className="rounded bg-red-100 px-2 py-1 text-xs text-red-800">None</span>
                        )}
                      </td>
                      <td className="p-2">
                        <select
                          className="rounded border px-2 py-1 text-xs"
                          value={item.status}
                          disabled={savingItemId === item.id}
                          onChange={async (e) => {
                            setSavingItemId(item.id);
                            await mutatingAction.execute(
                              async () => {
                                await patchAuditorChecklistItem(activeChecklist.id, item.id, { status: e.target.value });
                                await refreshActiveChecklist(activeChecklist.id);
                              },
                              { loadingMessage: "Saving checklist item...", successMessage: "Checklist item updated.", errorMessage: "Failed to save checklist item." },
                            );
                            setSavingItemId(null);
                          }}
                        >
                          {STATUS_OPTIONS.map((status) => (
                            <option key={status} value={status}>
                              {formatStatus(status)}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="min-w-[300px] p-2">
                        {item.our_response ? (
                          <div className="relative min-h-28 rounded-md border border-slate-200 bg-slate-50 p-4 pr-12 text-sm leading-relaxed text-slate-800 whitespace-pre-wrap break-words">
                            <Button
                              size="sm"
                              variant="outline"
                              className="absolute right-2 top-2 h-7 w-7 p-0 text-xs"
                              disabled={generatingResponseItemId === item.id}
                              onClick={() => void generateResponseForItem(item.id)}
                              title="Regenerate response"
                            >
                              {generatingResponseItemId === item.id ? <ButtonSpinner /> : "↻"}
                            </Button>
                            {String(item.our_response)}
                          </div>
                        ) : (
                          <div className="flex min-h-28 items-center justify-center rounded-md border border-dashed border-slate-300 bg-slate-50 p-4">
                            {generatingResponseItemId === item.id ? (
                              <span className="flex items-center gap-2 text-xs text-muted-foreground">
                                <ButtonSpinner />
                                Generating...
                              </span>
                            ) : (
                              <Button
                                size="sm"
                                variant="secondary"
                                className="h-7 px-3 text-xs"
                                onClick={() => void generateResponseForItem(item.id)}
                              >
                                Generate Response
                              </Button>
                            )}
                          </div>
                        )}
                      </td>
                      {showPriorityColumn ? (
                        <td className="p-2">
                          <span className="rounded border px-2 py-1 text-xs">{formatStatus(item.priority)}</span>
                        </td>
                      ) : null}
                      <td className="p-2">
                        <Link
                          href={`/chat?prefill=${encodeURIComponent(
                            `The auditor is requesting: ${getDisplayDescription(item)}. Based on everything in our compliance system, what satisfies this request and what are we still missing?`,
                          )}`}
                          onClick={() => setAskClaudePendingId(item.id)}
                        >
                          <Button size="sm" variant="outline" className="h-7 px-2 text-xs" disabled={askClaudePendingId === item.id}>
                            <span className="flex items-center gap-2">
                              {askClaudePendingId === item.id ? <ButtonSpinner /> : null}
                              {askClaudePendingId === item.id ? "Opening..." : "Ask Claude"}
                            </span>
                          </Button>
                        </Link>
                      </td>
                    </tr>
                  ))}
                  {filteredItems.map((item: any) =>
                    expandedEvidenceItem === item.id ? (
                      <tr key={`expanded-${item.id}`} className="border-b bg-muted/30">
                        <td
                          className="p-2 text-xs"
                          colSpan={(showReferenceColumn ? 1 : 0) + (showTitleColumn ? 1 : 0) + visibleDynamicRawColumns.length + 6 + (showPriorityColumn ? 1 : 0)}
                        >
                          <div className="space-y-2">
                            {((item.evidence_mapping?.results || []) as any[]).length === 0 ? (
                              <p className="text-muted-foreground">No mapped evidence details available.</p>
                            ) : (
                              (item.evidence_mapping.results as any[]).map((result, idx) => (
                                <div key={`${item.id}-ev-${idx}`} className="rounded border p-2">
                                  <p className="font-medium">
                                    {result.filename || `Document ${result.document_id}`}{" "}
                                    {activeLibrary === "dpa" ? (
                                      <span
                                        className={`ml-1 inline-flex rounded px-1.5 py-0.5 text-[10px] ${
                                          String(result.library_source || "main").toLowerCase() === "dpa"
                                            ? "bg-green-100 text-green-800"
                                            : "bg-slate-200 text-slate-700"
                                        }`}
                                      >
                                        {String(result.library_source || "main").toLowerCase() === "dpa" ? "DPA" : "Main"}
                                      </span>
                                    ) : null}
                                  </p>
                                  <p className="text-[11px] text-muted-foreground">
                                    {result.relevance === "yes"
                                      ? "Satisfies"
                                      : result.relevance === "partial"
                                        ? "Partial"
                                        : "Not relevant"}{" "}
                                    — {result.reason || "No reason provided."}
                                  </p>
                                </div>
                              ))
                            )}
                          </div>
                        </td>
                      </tr>
                    ) : null,
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      ) : null}

      {showUploadModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <Card className="w-full max-w-2xl p-6">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-lg font-semibold">Upload New Checklist</h3>
              <Button variant="outline" size="sm" onClick={() => setShowUploadModal(false)}>
                Close
              </Button>
            </div>
            {uploadForm}
          </Card>
        </div>
      ) : null}
      {savingItemId ? <StatusMessage type="info" message="Saving checklist item..." /> : null}
      <ApiLimitOverrideDialog
        open={showOverrideDialog}
        estimate={overrideEstimate}
        message={overrideMessage}
        onRunAnyway={async () => {
          setShowOverrideDialog(false);
          setOverrideMessage(null);
          const checklistId = overrideChecklistId ?? activeChecklistId;
          const action = overrideAction;
          const importId = overrideImportId;
          setOverrideAction(null);
          setOverrideChecklistId(null);
          setOverrideImportId(null);
          if (!action) return;
          if (action === "match" && checklistId) {
            await startMatchEvidence(checklistId, true);
          } else if (action === "refresh" && checklistId) {
            await startRefreshEvidence(checklistId, true);
          } else if (action === "upload" && importId) {
            await forceProcessQueuedImport(importId, true);
          }
        }}
        onIncreaseLimit={() => {
          setShowOverrideDialog(false);
          setOverrideMessage(null);
          setOverrideAction(null);
          setOverrideChecklistId(null);
          setOverrideImportId(null);
          window.location.href = "/settings#daily-api-limit-input";
        }}
        onCancel={() => {
          setShowOverrideDialog(false);
          setOverrideMessage(null);
          if (overrideAction === "match") {
            toast.info("Matching paused — daily API limit reached. Run Match Evidence to Requests again when ready.");
          } else if (overrideAction === "refresh") {
            toast.info("Refresh paused — daily API limit reached.");
          } else if (overrideAction === "upload") {
            toast.info("Upload queued — will process automatically when the daily limit resets at midnight UTC.");
          }
          setOverrideAction(null);
          setOverrideChecklistId(null);
          setOverrideImportId(null);
        }}
      />
    </div>
  );
}
