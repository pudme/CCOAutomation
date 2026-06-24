"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { toast } from "sonner";

import { ApiLimitOverrideDialog } from "@/components/shared/ApiLimitOverrideDialog";
import { ButtonSpinner, EmptyState, ErrorState, SkeletonCard, StatusMessage } from "@/components/shared/LoadingStates";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  bulkDeleteDocuments,
  cancelReanalyzeBatch,
  confirmDuplicateFlag,
  deleteDocumentWithOptions,
  dismissDuplicateFlag,
  getDocumentPreview,
  getDocuments,
  getAllSettings,
  getApiUsage,
  getReanalyzeBatchPreview,
  getReanalyzeStatus,
  importSingleFile,
  importTextPayload,
  previewFolderSync,
  processImportById,
  estimateBatchCost,
  streamFolderSync,
  type ApiUsage,
  streamReanalyzeBatch,
  refreshEvidenceLinks,
  searchDocuments,
} from "@/lib/api";
import { consumeOneTimeBatchBypass } from "@/lib/api-limit-override";
import { useAsyncAction, useDebouncedValue } from "@/lib/hooks";
import { useAnalysisProgressStore } from "@/lib/stores/analysis-progress";
import { formatStatus } from "@/lib/utils";

type ConfirmationState = {
  mode: "bulk" | "linked";
  message: string;
  documentId?: string;
};

function docTypeBucket(docType: string): "policy" | "report" | "record" | "other" {
  const normalized = (docType || "").trim().toLowerCase();
  if (normalized === "policy") return "policy";
  if (normalized === "report") return "report";
  if (normalized === "record") return "record";
  return "other";
}

function relativeTimeLabel(value: string | null): string {
  if (!value) return "Never synced";
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "Never synced";
  const deltaMs = Date.now() - timestamp;
  if (deltaMs < 60_000) return "Last synced: just now";
  const minutes = Math.floor(deltaMs / 60_000);
  if (minutes < 60) return `Last synced: ${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `Last synced: ${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  return `Last synced: ${days} day${days === 1 ? "" : "s"} ago`;
}

export default function DocumentsPage() {
  const pathname = usePathname();
  const activeLibrary = pathname.startsWith("/dpa/documents") ? "dpa" : "main";
  const isDpaLibrary = activeLibrary === "dpa";
  const [documents, setDocuments] = useState<any[]>([]);
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, 500);
  const [results, setResults] = useState<any[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [preview, setPreview] = useState<any | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [confirmation, setConfirmation] = useState<ConfirmationState | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [documentsLoading, setDocumentsLoading] = useState(true);
  const [documentsError, setDocumentsError] = useState<string | null>(null);
  const [analyzeLoading, setAnalyzeLoading] = useState(false);
  const [deleteLoadingId, setDeleteLoadingId] = useState<string | null>(null);
  const [bulkDeleteProgress, setBulkDeleteProgress] = useState<{ current: number; total: number } | null>(null);
  const [batchSummary, setBatchSummary] = useState<string>("");
  const [batchDocuments, setBatchDocuments] = useState<string[]>([]);
  const [pendingBatchDocuments, setPendingBatchDocuments] = useState<string[]>([]);
  const [batchDocumentState, setBatchDocumentState] = useState<
    Record<string, "pending" | "complete" | "error" | "skipped">
  >({});
  const [lastKnownUsage, setLastKnownUsage] = useState<ApiUsage | null>(null);
  const [reanalyzeStatus, setReanalyzeStatus] = useState<{
    running: boolean;
    queued?: boolean;
    job_id?: number | null;
    completed: number;
    total: number;
    new_links: number;
    message?: string;
    last_document?: string | null;
    remaining_unanalyzed?: number;
  }>({
    running: false,
    queued: false,
    completed: 0,
    total: 0,
    new_links: 0,
    message: "",
    last_document: null,
    remaining_unanalyzed: 0,
  });
  const [showOverrideDialog, setShowOverrideDialog] = useState(false);
  const [overrideEstimate, setOverrideEstimate] = useState<any | null>(null);
  const [overrideMessage, setOverrideMessage] = useState<string | null>(null);
  const [analysisStartedAtMs, setAnalysisStartedAtMs] = useState<number | null>(null);
  const [cancelRequested, setCancelRequested] = useState(false);
  const [recentProcessed, setRecentProcessed] = useState<Array<{ filename: string; status: string }>>([]);
  const [showRefreshPrompt, setShowRefreshPrompt] = useState(false);
  const [refreshState, setRefreshState] = useState<"idle" | "running" | "done">("idle");
  const refreshAction = useAsyncAction();
  const setAnalysisProgress = useAnalysisProgressStore((state) => state.setProgress);
  const clearAnalysisProgress = useAnalysisProgressStore((state) => state.clear);
  const pushAnalysisProcessed = useAnalysisProgressStore((state) => state.pushProcessed);
  const analysisIsAnalyzing = useAnalysisProgressStore((state) => state.isAnalyzing);
  const analysisCompleted = useAnalysisProgressStore((state) => state.completed);
  const analysisTotal = useAnalysisProgressStore((state) => state.total);
  const analysisNewLinks = useAnalysisProgressStore((state) => state.newLinks);
  const analysisStartedAt = useAnalysisProgressStore((state) => state.startedAt);
  const analysisMessage = useAnalysisProgressStore((state) => state.message);
  const analysisRecentProcessed = useAnalysisProgressStore((state) => state.recentProcessed);
  const streamAbortRef = useRef<AbortController | null>(null);
  const syncFileInputRef = useRef<HTMLInputElement | null>(null);
  const syncAbortRef = useRef<AbortController | null>(null);
  const [syncCandidateFiles, setSyncCandidateFiles] = useState<File[]>([]);
  const [syncPreview, setSyncPreview] = useState<{
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
  } | null>(null);
  const [showSyncPreviewModal, setShowSyncPreviewModal] = useState(false);
  const [showSyncCompleteModal, setShowSyncCompleteModal] = useState(false);
  const [syncRunning, setSyncRunning] = useState(false);
  const [syncTotal, setSyncTotal] = useState(0);
  const [syncCompleted, setSyncCompleted] = useState(0);
  const [syncMessage, setSyncMessage] = useState("");
  const [syncProcessedFiles, setSyncProcessedFiles] = useState<Array<{ filename: string; mode: string }>>([]);
  const [syncSummary, setSyncSummary] = useState<any | null>(null);
  const [syncEstimate, setSyncEstimate] = useState<any | null>(null);
  const [showSyncOverrideDialog, setShowSyncOverrideDialog] = useState(false);
  const [lastSyncedAt, setLastSyncedAt] = useState<string | null>(null);
  const [activeDuplicatePanelImportId, setActiveDuplicatePanelImportId] = useState<number | null>(null);
  const importFileInputRef = useRef<HTMLInputElement | null>(null);
  const [showImportFileModal, setShowImportFileModal] = useState(false);
  const [showPasteTextModal, setShowPasteTextModal] = useState(false);
  const [importDataDate, setImportDataDate] = useState(new Date().toISOString().slice(0, 10));
  const [importNotes, setImportNotes] = useState("");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importSubmitting, setImportSubmitting] = useState(false);
  const [pasteFilename, setPasteFilename] = useState("");
  const [pasteText, setPasteText] = useState("");

  async function refreshDocuments() {
    setDocumentsLoading(true);
    setDocumentsError(null);
    try {
      const payload = await getDocuments(activeLibrary);
      setDocuments(payload);
      setDocumentsError(null);
    } catch (error) {
      setDocuments([]);
      setDocumentsError(error instanceof Error ? error.message : "Failed to load documents.");
    } finally {
      setDocumentsLoading(false);
    }
  }

  async function refreshSyncMetadata() {
    try {
      const settings = await getAllSettings();
      setLastSyncedAt((isDpaLibrary ? settings.last_dpa_folder_sync : settings.last_folder_sync) || null);
    } catch {
      setLastSyncedAt(null);
    }
  }

  useEffect(() => {
    void refreshDocuments();
    void refreshSyncMetadata();
  }, [activeLibrary]);

  useEffect(() => {
    return () => {
      streamAbortRef.current?.abort();
      streamAbortRef.current = null;
      syncAbortRef.current?.abort();
      syncAbortRef.current = null;
    };
  }, []);

  useEffect(() => {
    const valid = new Set(documents.map((doc) => doc.id));
    setSelectedIds((prev) => prev.filter((id) => valid.has(id)));
  }, [documents]);

  useEffect(() => {
    if (!analysisIsAnalyzing) return;
    setReanalyzeStatus((prev) => ({
      ...prev,
      running: true,
      completed: analysisCompleted,
      total: analysisTotal,
      new_links: analysisNewLinks,
      message: analysisMessage,
    }));
    setRecentProcessed(analysisRecentProcessed);
    setAnalysisStartedAtMs(analysisStartedAt);
  }, [
    analysisCompleted,
    analysisIsAnalyzing,
    analysisMessage,
    analysisNewLinks,
    analysisRecentProcessed,
    analysisStartedAt,
    analysisTotal,
  ]);

  useEffect(() => {
    const reconnectIfRunning = async () => {
      try {
        const status = await getReanalyzeStatus();
        if (!status.running) {
          clearAnalysisProgress();
          return;
        }
        const startedAt = status.started_at ? new Date(status.started_at).getTime() : Date.now();
        setReanalyzeStatus({
          running: true,
          queued: false,
          completed: status.completed || 0,
          total: status.total || 0,
          new_links: status.new_links || 0,
          message: status.message || "Reconnecting to active analysis...",
          last_document: status.last_document || null,
          remaining_unanalyzed: status.remaining_unanalyzed,
        });
        setAnalysisStartedAtMs(startedAt);
        setAnalysisProgress({
          isAnalyzing: true,
          completed: status.completed || 0,
          total: status.total || 0,
          newLinks: status.new_links || 0,
          startedAt,
          message: status.message || "Reconnecting to active analysis...",
          lastDocument: status.last_document || null,
        });
        await attachReanalyzeStream({ bypass: false, resumeOnly: true });
      } catch (error) {
        const typed = error as Error & { status?: number };
        if (typed.name === "AbortError") return;
        if (typed.status === 409) {
          clearAnalysisProgress();
        }
      }
    };
    void reconnectIfRunning();
  }, []);

  const allSelected = documents.length > 0 && selectedIds.length === documents.length;
  const selectedCount = selectedIds.length;

  const totals = useMemo(() => {
    const breakdown = { policy: 0, report: 0, record: 0, other: 0 };
    for (const doc of documents) {
      breakdown[docTypeBucket(doc.doc_type)] += 1;
    }
    return {
      total: documents.length,
      ...breakdown,
    };
  }, [documents]);

  const elapsedSeconds = analysisStartedAtMs ? (Date.now() - analysisStartedAtMs) / 1000 : 0;
  const estimatedRemainingText = useMemo(() => {
    if (!reanalyzeStatus.running) return null;
    if (!analysisStartedAtMs || reanalyzeStatus.completed < 1) return null;
    const perDoc = elapsedSeconds / Math.max(1, reanalyzeStatus.completed);
    const remainingDocs = Math.max(0, reanalyzeStatus.total - reanalyzeStatus.completed);
    const remainingSeconds = perDoc * remainingDocs;
    const remainingMinutes = Math.max(1, Math.round(remainingSeconds / 60));
    return `~${remainingMinutes} min remaining`;
  }, [analysisStartedAtMs, elapsedSeconds, reanalyzeStatus.completed, reanalyzeStatus.running, reanalyzeStatus.total]);

  useEffect(() => {
    let cancelled = false;
    if (!debouncedQuery.trim()) {
      setResults([]);
      setSearchLoading(false);
      setSearchError(null);
      return;
    }
    const runSearch = async () => {
      setSearchLoading(true);
      setSearchError(null);
      try {
        const next = await searchDocuments(debouncedQuery.trim());
        if (cancelled) return;
        setResults(next);
      } catch {
        if (cancelled) return;
        setResults([]);
        setSearchError("Search failed - please try again.");
      } finally {
        if (!cancelled) setSearchLoading(false);
      }
    };
    void runSearch();
    return () => {
      cancelled = true;
    };
  }, [debouncedQuery]);

  useEffect(() => {
    if (!showRefreshPrompt) return;
    const timer = window.setTimeout(() => {
      setShowRefreshPrompt(false);
      setBatchSummary("");
    }, 10000);
    return () => window.clearTimeout(timer);
  }, [showRefreshPrompt]);

  async function attachReanalyzeStream(options: {
    bypass: boolean;
    resumeOnly: boolean;
    previewDocs?: string[];
    previewTotal?: number;
  }) {
    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;
    await streamReanalyzeBatch(
      { bypass_limit: options.bypass, resume_only: options.resumeOnly },
      async (event) => {
        if (event.event === "start") {
          const docs = event.data.documents || options.previewDocs || [];
          setBatchDocuments(docs);
          setPendingBatchDocuments(docs);
          setBatchDocumentState(
            Object.fromEntries(docs.map((name) => [name, "pending"])) as Record<
              string,
              "pending" | "complete" | "error" | "skipped"
            >,
          );
          setReanalyzeStatus((prev) => ({
            ...prev,
            running: true,
            queued: false,
            completed: prev.completed || 0,
            total: event.data.total || prev.total || options.previewTotal || docs.length,
            new_links: prev.new_links || 0,
            message: event.data.total ? `Analyzing ${event.data.total} documents...` : (event.data.message || prev.message),
          }));
          setAnalysisProgress({
            isAnalyzing: true,
            total: event.data.total || options.previewTotal || docs.length,
            startedAt: Date.now(),
            message: event.data.message || "Analyzing documents...",
          });
          if (event.data.api_usage) setLastKnownUsage(event.data.api_usage);
          return;
        }
        if (event.event === "progress") {
          setReanalyzeStatus((prev) => ({
            ...prev,
            running: true,
            completed: event.data.completed ?? prev.completed,
            total: event.data.total ?? prev.total,
            new_links: event.data.new_links ?? prev.new_links,
            message: event.data.message || prev.message,
            last_document: event.data.last_document || prev.last_document,
          }));
          setAnalysisProgress({
            isAnalyzing: true,
            completed: event.data.completed ?? 0,
            total: event.data.total ?? 0,
            newLinks: event.data.new_links ?? 0,
            message: event.data.message || "",
            lastDocument: event.data.last_document || null,
          });
          return;
        }
        if (event.event === "document") {
          setBatchDocumentState((prev) => ({
            ...prev,
            [event.data.filename]: event.data.status,
          }));
          setRecentProcessed((prev) => {
            const next = [{ filename: event.data.filename, status: event.data.status }, ...prev];
            return next.slice(0, 10);
          });
          setReanalyzeStatus((prev) => ({
            ...prev,
            running: true,
            completed: event.data.completed ?? prev.completed,
            total: event.data.total ?? prev.total,
            new_links: event.data.new_links_total ?? event.data.new_links ?? prev.new_links,
            last_document: event.data.filename || prev.last_document,
            message: `Processed ${event.data.filename}`,
          }));
          pushAnalysisProcessed({
            filename: event.data.filename,
            status: event.data.status,
          });
          setAnalysisProgress({
            isAnalyzing: true,
            completed: event.data.completed ?? 0,
            total: event.data.total ?? 0,
            newLinks: event.data.new_links_total ?? event.data.new_links ?? 0,
            message: `Processed ${event.data.filename}`,
            lastDocument: event.data.filename,
          });
          return;
        }
        if (event.event === "cancelled") {
          setReanalyzeStatus((prev) => ({
            ...prev,
            running: false,
            queued: false,
            completed: event.data.completed ?? prev.completed,
            total: event.data.total ?? prev.total,
            new_links: event.data.new_links ?? prev.new_links,
            message: "Reanalysis cancelled.",
          }));
          if (event.data.api_usage) setLastKnownUsage(event.data.api_usage);
          setBatchSummary(
            `Analysis cancelled — ${event.data.completed}/${event.data.total} documents analyzed, ${event.data.new_links} new control links created.`,
          );
          clearAnalysisProgress();
          setShowRefreshPrompt(true);
          await refreshDocuments();
          toast.info("Document analysis cancelled.");
          return;
        }
        if (event.event === "complete") {
          setReanalyzeStatus((prev) => ({
            ...prev,
            running: false,
            queued: false,
            completed: event.data.completed ?? prev.completed,
            total: event.data.total ?? prev.total,
            new_links: event.data.new_links ?? prev.new_links,
            message: "Reanalysis complete.",
          }));
          if (event.data.api_usage) setLastKnownUsage(event.data.api_usage);
          setBatchSummary(
            `Analysis complete — ${event.data.completed} documents analyzed, ${event.data.new_links} new control links created. Click Sync Control Status to update control coverage.`,
          );
          clearAnalysisProgress();
          setShowRefreshPrompt(true);
          await refreshDocuments();
          toast.success("Document batch analysis complete.");
          return;
        }
        if (event.event === "error") {
          const message = event.data.message || "Reanalysis failed.";
          setErrorMessage(message);
          toast.error(message);
        }
      },
      { signal: controller.signal },
    );
  }

  async function runAnalyzeBatch(bypass: boolean) {
    try {
      setAnalyzeLoading(true);
      setErrorMessage(null);
      setBatchSummary("");
      setBatchDocumentState({});
      setShowRefreshPrompt(false);
      setCancelRequested(false);
      setRecentProcessed([]);

      const usage = await getApiUsage();
      const preview = await getReanalyzeBatchPreview(10);
      const previewDocs = preview.documents || [];
      const totalUnanalyzed = preview.total_unanalyzed ?? preview.remaining_unanalyzed ?? 0;

      if (!bypass && totalUnanalyzed > 0 && usage.remaining < totalUnanalyzed) {
        const estimate = await estimateBatchCost({ num_calls: totalUnanalyzed });
        const message = `This will analyze ${totalUnanalyzed} documents using approximately ${totalUnanalyzed} API calls at Haiku pricing (~$${estimate.estimated_cost_usd.toFixed(2)}). You have ${usage.remaining} calls remaining today. Proceed?`;
        setOverrideEstimate(estimate);
        setOverrideMessage(message);
        setShowOverrideDialog(true);
        return;
      }

      setPendingBatchDocuments(previewDocs);
      setBatchDocuments(previewDocs);
      setBatchDocumentState(
        Object.fromEntries(previewDocs.map((name) => [name, "pending"])) as Record<
          string,
          "pending" | "complete" | "error" | "skipped"
        >,
      );
      setLastKnownUsage(usage);
      const startedAt = Date.now();
      setAnalysisStartedAtMs(startedAt);
      setReanalyzeStatus({
        running: true,
        queued: false,
        completed: 0,
        total: totalUnanalyzed,
        new_links: 0,
        message: totalUnanalyzed > 0 ? `Analyzing ${totalUnanalyzed} documents...` : "No unanalyzed documents found.",
        last_document: null,
        remaining_unanalyzed: preview.remaining_unanalyzed,
      });
      setAnalysisProgress({
        isAnalyzing: true,
        completed: 0,
        total: totalUnanalyzed,
        newLinks: 0,
        startedAt,
        message: totalUnanalyzed > 0 ? `Analyzing ${totalUnanalyzed} documents...` : "No unanalyzed documents found.",
        lastDocument: null,
      });
      await attachReanalyzeStream({
        bypass,
        resumeOnly: false,
        previewDocs,
        previewTotal: totalUnanalyzed,
      });
    } catch (error) {
      const typed = error as Error & { status?: number; detail?: any };
      if (typed.name === "AbortError") return;
      if (typed.status === 402 && typed.detail) {
        setOverrideEstimate(typed.detail);
        setOverrideMessage(null);
        setShowOverrideDialog(true);
      } else {
        const message = typed.message || "Failed to analyze documents.";
        setErrorMessage(message);
        toast.error(message);
      }
    } finally {
      setAnalyzeLoading(false);
      if (!useAnalysisProgressStore.getState().isAnalyzing) {
        setAnalysisStartedAtMs(null);
      }
      setCancelRequested(false);
    }
  }

  async function requestCancelReanalysis() {
    try {
      setCancelRequested(true);
      await cancelReanalyzeBatch();
      setReanalyzeStatus((prev) => ({
        ...prev,
        message: "Cancellation requested. Waiting for current document to finish...",
      }));
      setAnalysisProgress({
        message: "Cancellation requested. Waiting for current document to finish...",
      });
    } catch (error) {
      setCancelRequested(false);
      const message = error instanceof Error ? error.message : "Failed to request cancellation.";
      toast.error(message);
    }
  }

  async function handleSingleDelete(documentId: string, force: boolean) {
    setDeleteLoadingId(documentId);
    const timeoutId = window.setTimeout(() => {
      setDeleteLoadingId(null);
      toast.error("Delete request timed out - please try again.");
    }, 30000);
    try {
      await deleteDocumentWithOptions(documentId, { force });
      if (preview?.id === documentId) setPreview(null);
      await refreshDocuments();
      toast.success("Document deleted successfully.");
    } catch (error) {
      const typed = error as Error & { status?: number; detail?: any };
      if (typed.status === 409) {
        setConfirmation({
          mode: "linked",
          message: typed.message,
          documentId,
        });
        return;
      }
      const message = typed.message || "Failed to delete document.";
      setErrorMessage(message);
      toast.error(message);
    } finally {
      window.clearTimeout(timeoutId);
      setDeleteLoadingId(null);
    }
  }

  async function handleBulkDelete() {
    if (selectedIds.length === 0) return;
    setConfirmation({
      mode: "bulk",
      message: `Delete ${selectedIds.length} documents? This cannot be undone.`,
    });
  }

  function filterSyncFiles(allFiles: FileList | null): File[] {
    if (!allFiles) return [];
    const blockedNames = new Set(["desktop.ini", "thumbs.db", ".ds_store"]);
    return Array.from(allFiles).filter((file) => {
      const name = file.name || "";
      const lower = name.toLowerCase();
      if (!name || name.startsWith(".")) return false;
      if (name.startsWith("~$")) return false;
      if (blockedNames.has(lower)) return false;
      if (lower.endsWith(".tmp")) return false;
      if (name.endsWith("~")) return false;
      return true;
    });
  }

  async function startSyncFlowFromFiles(files: File[]) {
    if (files.length === 0) {
      toast.info("No eligible files found in that folder.");
      return;
    }
    const preview = await previewFolderSync(files, activeLibrary);
    if (preview.up_to_date || (preview.new === 0 && preview.modified === 0)) {
      toast.success("Everything is already up to date.");
      setSyncCandidateFiles([]);
      setSyncPreview(null);
      setShowSyncPreviewModal(false);
      return;
    }
    setSyncCandidateFiles(files);
    setSyncPreview(preview);
    setShowSyncPreviewModal(true);
  }

  async function runFolderSync(bypassLimit: boolean) {
    if (!syncPreview || syncCandidateFiles.length === 0) return;
    const estimatedCalls = syncPreview.new + syncPreview.modified;
    if (!bypassLimit && estimatedCalls > 0) {
      const estimate = await estimateBatchCost({ num_calls: estimatedCalls });
      if (estimate.will_exceed_limit) {
        setSyncEstimate(estimate);
        setShowSyncOverrideDialog(true);
        return;
      }
    }
    setShowSyncPreviewModal(false);
    setSyncRunning(true);
    setSyncCompleted(0);
    setSyncTotal(syncPreview.new + syncPreview.modified);
    setSyncMessage(`Syncing folder - importing ${syncPreview.new + syncPreview.modified} files...`);
    setSyncProcessedFiles([]);
    setSyncSummary(null);
    const controller = new AbortController();
    syncAbortRef.current = controller;
    try {
      await streamFolderSync(
        syncCandidateFiles,
        { bypass_limit: bypassLimit, library: activeLibrary },
        async (event) => {
          if (event.event === "start") {
            setSyncTotal(event.data.total_to_import || 0);
            setSyncMessage(event.data.message || "Sync started.");
            return;
          }
          if (event.event === "file") {
            setSyncCompleted(event.data.completed || 0);
            setSyncProcessedFiles((prev) => [{ filename: event.data.filename, mode: event.data.mode }, ...prev].slice(0, 10));
            setSyncMessage(`Processed ${event.data.filename}`);
            return;
          }
          if (event.event === "error") {
            toast.error(event.data.message || "Folder sync failed.");
            setSyncRunning(false);
            return;
          }
          if (event.event === "complete") {
            setSyncSummary(event.data);
            setSyncRunning(false);
            setShowSyncCompleteModal(true);
            await refreshDocuments();
            await refreshSyncMetadata();
            return;
          }
        },
        { signal: controller.signal },
      );
    } catch (error) {
      const typed = error as Error;
      if (typed.name !== "AbortError") {
        toast.error(typed.message || "Folder sync failed.");
      }
      setSyncRunning(false);
    } finally {
      syncAbortRef.current = null;
    }
  }

  async function dismissDuplicate(importId: number) {
    await dismissDuplicateFlag(importId);
    setDocuments((prev) =>
      prev.map((doc) =>
        doc.import_id === importId
          ? {
              ...doc,
              duplicate_status: "false_positive",
              duplicate_flag_dismissed: true,
            }
          : doc,
      ),
    );
    setActiveDuplicatePanelImportId(null);
  }

  async function confirmDuplicate(importId: number) {
    await confirmDuplicateFlag(importId);
    await refreshDocuments();
    setActiveDuplicatePanelImportId(null);
  }

  async function submitSingleFileImport() {
    if (!importFile) {
      toast.error("Choose a file to import.");
      return;
    }
    setImportSubmitting(true);
    try {
      await importSingleFile({
        file: importFile,
        data_date: importDataDate,
        notes: importNotes,
        library: activeLibrary,
      });
      toast.success("File import queued.");
      setShowImportFileModal(false);
      setImportFile(null);
      setImportNotes("");
      await refreshDocuments();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "File import failed.");
    } finally {
      setImportSubmitting(false);
    }
  }

  async function submitPasteTextImport() {
    if (!pasteFilename.trim()) {
      toast.error("Filename is required.");
      return;
    }
    if (!pasteText.trim()) {
      toast.error("Paste text is required.");
      return;
    }
    setImportSubmitting(true);
    try {
      await importTextPayload({
        filename: pasteFilename.trim(),
        content: pasteText,
        data_date: importDataDate,
        notes: importNotes,
        library: activeLibrary,
      });
      toast.success("Text import queued.");
      setShowPasteTextModal(false);
      setPasteFilename("");
      setPasteText("");
      setImportNotes("");
      await refreshDocuments();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Text import failed.");
    } finally {
      setImportSubmitting(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <h3 className="font-semibold">Semantic Search</h3>
        <div className="mt-2 flex gap-2">
          <input
            className="w-full rounded border px-3 py-2 text-sm"
            value={query}
            placeholder="Search document context..."
            onChange={(event) => setQuery(event.target.value)}
          />
          <button
            className="flex min-w-28 items-center justify-center gap-2 rounded bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-70"
            disabled={searchLoading}
            onClick={async () => {
              const current = query.trim();
              if (!current) {
                setResults([]);
                return;
              }
              setSearchLoading(true);
              setSearchError(null);
              try {
                const next = await searchDocuments(current);
                setResults(next);
              } catch {
                setResults([]);
                setSearchError("Search failed - please try again.");
              } finally {
                setSearchLoading(false);
              }
            }}
          >
            {searchLoading ? <ButtonSpinner /> : null}
            {searchLoading ? "Searching..." : "Search"}
          </button>
        </div>
        {searchError ? (
          <div className="mt-3">
            <ErrorState
              title="Search failed"
              description="Search failed - please try again."
              onRetry={() => {
                const activeQuery = query.trim();
                if (!activeQuery) return;
                setQuery(activeQuery);
              }}
            />
          </div>
        ) : null}
        <div className="mt-3 space-y-2">
          {searchLoading ? (
            <>
              <SkeletonCard lines={3} />
              <SkeletonCard lines={3} />
            </>
          ) : results.length > 0 ? (
            <div className="animate-in fade-in duration-300 space-y-2">
              {results.map((item) => (
                <div key={item.id} className="rounded border p-2 text-xs">
                  <p>{item.snippet}</p>
                  <p className="text-muted-foreground">
                    Source: {item.metadata?.source_system || item.metadata?.filename || "Unknown"}
                  </p>
                </div>
              ))}
            </div>
          ) : query.trim() ? (
            <EmptyState
              title="No documents found"
              description={`No documents found matching "${query.trim()}". Try different search terms.`}
            />
          ) : (
            <p className="text-xs text-muted-foreground">Start typing to search document context.</p>
          )}
        </div>
      </Card>

      {preview ? (
        <Card className="p-4">
          <h3 className="font-semibold">Preview - {preview.filename}</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Type: {formatStatus(preview.doc_type)} | Framework: {preview.framework || "Unspecified"} | Controls:{" "}
            {preview.control_ids.length > 0 ? preview.control_ids.join(", ") : "Unmapped"}
          </p>
          <div className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded border bg-muted/30 p-3 text-sm">
            {preview.preview_text || "No preview text available."}
          </div>
          <Button className="mt-3" variant="outline" onClick={() => setPreview(null)}>
            Close Preview
          </Button>
        </Card>
      ) : null}

      <Card className="p-4">
        <h3 className="font-semibold">Document Library</h3>
        {isDpaLibrary ? (
          <div className="mt-2 rounded border border-blue-200 bg-blue-50 p-2 text-xs text-blue-900">
            DPA Document Library - documents in this library are only used for DPA compliance tracking.
            Main library documents are available as supplementary evidence.
          </div>
        ) : null}
        <div className="mt-3 rounded border bg-muted/30 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="space-y-1">
              <p className="text-sm font-semibold">{totals.total} documents</p>
              <p className="text-xs text-muted-foreground">
                {totals.policy} policies · {totals.report} reports · {totals.record} records · {totals.other} other
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setShowImportFileModal(true);
                  setShowPasteTextModal(false);
                }}
              >
                Import File
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setShowPasteTextModal(true);
                  setShowImportFileModal(false);
                }}
              >
                Paste Text
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={syncRunning}
                onClick={() => syncFileInputRef.current?.click()}
              >
                <span className="flex items-center gap-2">
                  {syncRunning ? <ButtonSpinner /> : null}
                  {syncRunning ? "Syncing..." : "Sync Folder"}
                </span>
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={analyzeLoading}
                onClick={async () => {
                  const armedBypass = consumeOneTimeBatchBypass();
                  if (armedBypass) {
                    await runAnalyzeBatch(true);
                    return;
                  }
                  await runAnalyzeBatch(false);
                }}
              >
                <span className="flex items-center gap-2">
                  {analyzeLoading ? <ButtonSpinner /> : null}
                  {analyzeLoading ? "Analyzing..." : "Analyze All Documents"}
                </span>
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={refreshState === "running" || refreshAction.loading}
                onClick={async () => {
                  setRefreshState("running");
                  let success = false;
                  await refreshAction.execute(
                    async () => {
                      await refreshEvidenceLinks();
                      await refreshDocuments();
                      success = true;
                      setShowRefreshPrompt(false);
                      setBatchSummary("");
                      setRefreshState("done");
                      setTimeout(() => setRefreshState("idle"), 3000);
                    },
                    {
                      loadingMessage: "Syncing control status...",
                      successMessage: "Control status synced.",
                      errorMessage: "Failed to sync control status.",
                    },
                  );
                  if (!success) setRefreshState("idle");
                }}
              >
                <span className="flex items-center gap-2">
                  {refreshState === "running" ? <ButtonSpinner /> : null}
                  {refreshState === "running" ? "Syncing..." : refreshState === "done" ? "Done" : "Sync Control Status"}
                </span>
              </Button>
              <span className="text-xs text-muted-foreground">Fast — no AI required</span>
              {selectedCount > 0 ? (
                <Button size="sm" variant="destructive" onClick={() => void handleBulkDelete()}>
                  Delete Selected ({selectedCount})
                </Button>
              ) : null}
              <input
                ref={syncFileInputRef}
                type="file"
                className="hidden"
                multiple
                // eslint-disable-next-line @typescript-eslint/ban-ts-comment
                // @ts-ignore - webkitdirectory is required for folder selection.
                webkitdirectory="true"
                onChange={async (event) => {
                  const files = filterSyncFiles(event.target.files);
                  event.currentTarget.value = "";
                  try {
                    await startSyncFlowFromFiles(files);
                  } catch (error) {
                    toast.error(error instanceof Error ? error.message : "Folder preview failed.");
                  }
                }}
              />
            </div>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">{relativeTimeLabel(lastSyncedAt)}</p>
          {batchSummary && !showRefreshPrompt ? <p className="mt-2 text-xs text-muted-foreground">{batchSummary}</p> : null}
          {reanalyzeStatus.running || reanalyzeStatus.queued || analyzeLoading ? (
            <div className="mt-2 rounded border bg-background p-2 text-xs">
              <p className="mb-1 font-medium">
                {reanalyzeStatus.total > 0
                  ? `Analyzing ${reanalyzeStatus.total} documents...`
                  : "Analyzing documents..."}
              </p>
              <div className="mb-2 flex items-center gap-2 text-muted-foreground">
                <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-r-transparent" />
                <span>{reanalyzeStatus.message || "Calling Claude API..."}</span>
              </div>
              <div className="mb-2 h-2 w-full rounded bg-muted">
                <div
                  className="h-2 rounded bg-primary transition-all duration-300"
                  style={{
                    width: `${Math.min(
                      100,
                      Math.round((reanalyzeStatus.completed / Math.max(1, reanalyzeStatus.total)) * 100),
                    )}%`,
                  }}
                />
              </div>
              <p className="mb-1 text-muted-foreground">
                Progress: {reanalyzeStatus.completed} / {Math.max(0, reanalyzeStatus.total)} complete
              </p>
              {estimatedRemainingText ? <p className="mb-1 text-muted-foreground">{estimatedRemainingText}</p> : null}
              <p className="mb-2 text-muted-foreground">New links: {reanalyzeStatus.new_links}</p>
              {reanalyzeStatus.running ? (
                <Button
                  size="sm"
                  variant="destructive"
                  className="mb-2"
                  disabled={cancelRequested}
                  onClick={() => void requestCancelReanalysis()}
                >
                  {cancelRequested ? (
                    <span className="flex items-center gap-2">
                      <ButtonSpinner />
                      Cancelling...
                    </span>
                  ) : (
                    "Cancel"
                  )}
                </Button>
              ) : null}
              <p className="mb-1 font-medium">Last 10 processed documents:</p>
              <ul className="space-y-1">
                {(recentProcessed.length > 0
                  ? recentProcessed
                  : (batchDocuments.length > 0 ? batchDocuments : pendingBatchDocuments).map((name) => ({
                      filename: name,
                      status: batchDocumentState[name] || "pending",
                    }))
                )
                  .slice(0, 10)
                  .map((entry) => (
                  <li key={entry.filename}>
                    {entry.status === "complete"
                      ? "[done]"
                      : entry.status === "error"
                        ? "[error]"
                        : entry.status === "skipped"
                          ? "[skip]"
                          : "[pending]"}{" "}
                    {entry.filename}
                  </li>
                ))}
              </ul>
              {lastKnownUsage ? (
                <p className="mt-2 text-muted-foreground">
                  API usage today: {lastKnownUsage.today_count} / {lastKnownUsage.daily_limit} calls
                </p>
              ) : null}
            </div>
          ) : null}
          {syncRunning ? (
            <div className="mt-2 rounded border bg-background p-2 text-xs">
              <p className="mb-1 font-medium">{syncMessage || "Syncing folder..."}</p>
              <div className="mb-2 h-2 w-full rounded bg-muted">
                <div
                  className="h-2 rounded bg-primary transition-all duration-300"
                  style={{
                    width: `${Math.min(100, Math.round((syncCompleted / Math.max(syncTotal, 1)) * 100))}%`,
                  }}
                />
              </div>
              <p className="mb-2 text-muted-foreground">
                Progress: {syncCompleted} / {Math.max(syncTotal, 0)} complete
              </p>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => {
                  syncAbortRef.current?.abort();
                  syncAbortRef.current = null;
                  setSyncRunning(false);
                  setSyncMessage("Folder sync cancelled.");
                }}
              >
                Cancel
              </Button>
              <ul className="mt-2 space-y-1">
                {syncProcessedFiles.map((entry) => (
                  <li key={`${entry.filename}-${entry.mode}`}>
                    [{entry.mode}] {entry.filename}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {showRefreshPrompt && batchSummary ? (
            <div className="mt-2 rounded border border-primary/30 bg-primary/5 p-3">
              <p className="text-sm">{batchSummary}</p>
              <Button
                className="mt-2"
                disabled={refreshState === "running" || refreshAction.loading}
                onClick={async () => {
                  setRefreshState("running");
                  let success = false;
                  await refreshAction.execute(
                    async () => {
                      await refreshEvidenceLinks();
                      await refreshDocuments();
                      success = true;
                      setRefreshState("done");
                      setTimeout(() => setRefreshState("idle"), 3000);
                    },
                    {
                      loadingMessage: "Syncing control status...",
                      successMessage: "Control status synced.",
                      errorMessage: "Failed to sync control status.",
                    },
                  );
                  if (!success) setRefreshState("idle");
                }}
              >
                {refreshState === "running" ? (
                  <span className="flex items-center gap-2">
                    <ButtonSpinner />
                    Syncing...
                  </span>
                ) : (
                  "Sync Control Status"
                )}
              </Button>
              <p className="mt-1 text-xs text-muted-foreground">Fast — no AI required</p>
            </div>
          ) : null}
          <div className="mt-2 flex items-center gap-2 text-xs">
            <input
              id="select-all-documents"
              type="checkbox"
              checked={allSelected}
              onChange={(event) => {
                if (event.target.checked) {
                  setSelectedIds(documents.map((doc) => doc.id));
                } else {
                  setSelectedIds([]);
                }
              }}
            />
            <label htmlFor="select-all-documents">Select All</label>
          </div>
        </div>

        {errorMessage ? <StatusMessage type="error" message={errorMessage} /> : null}
        {bulkDeleteProgress ? (
          <StatusMessage
            type="info"
            message={`Deleting ${bulkDeleteProgress.current} of ${bulkDeleteProgress.total}...`}
          />
        ) : null}

        <div className="mt-3 space-y-2">
          {documentsLoading ? (
            <>
              <SkeletonCard lines={3} />
              <SkeletonCard lines={3} />
              <SkeletonCard lines={3} />
            </>
          ) : documentsError ? (
            <ErrorState
              title="Failed to load documents"
              description="The document library could not be loaded."
              onRetry={() => void refreshDocuments()}
            />
          ) : documents.length === 0 ? (
            <EmptyState title="No documents yet" description="Import your first document to start building evidence." />
          ) : (
            documents.map((doc) => (
              <div key={doc.id} className="rounded border p-2 text-sm">
                <div className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={selectedIds.includes(doc.id)}
                    onChange={(event) => {
                      if (event.target.checked) {
                        setSelectedIds((prev) => [...prev, doc.id]);
                      } else {
                        setSelectedIds((prev) => prev.filter((id) => id !== doc.id));
                      }
                    }}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-medium">{doc.filename}</p>
                      {(doc.duplicate_status === "suspected" || doc.duplicate_status === "confirmed_duplicate") &&
                      !doc.duplicate_flag_dismissed &&
                      doc.import_id ? (
                        <button
                          className={`rounded px-2 py-0.5 text-[11px] ${
                            doc.duplicate_status === "confirmed_duplicate"
                              ? "bg-red-100 text-red-800"
                              : "bg-yellow-100 text-yellow-800"
                          }`}
                          onClick={() =>
                            setActiveDuplicatePanelImportId((prev) => (prev === doc.import_id ? null : doc.import_id))
                          }
                        >
                          {doc.duplicate_status === "confirmed_duplicate" ? "🔴 Exact duplicate" : "⚠ Possible duplicate"}
                        </button>
                      ) : null}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Type: {formatStatus(doc.doc_type)} | Entity: {doc.entity || "N/A"} | Framework: {doc.framework || "N/A"}
                    </p>
                    {activeDuplicatePanelImportId === doc.import_id ? (
                      <div className="mt-2 rounded border border-yellow-300 bg-yellow-50 p-2 text-xs text-yellow-900">
                        <p className="font-semibold">⚠ Possible Duplicate Detected</p>
                        <p className="mt-1">
                          This document may be a duplicate of: <span className="font-medium">{doc.duplicate_of_filename || "Unknown"}</span>
                        </p>
                        <p className="mt-1">Reason: {doc.duplicate_reason || "No reason provided."}</p>
                        <p className="mt-1">Confidence: {doc.duplicate_confidence || "N/A"}</p>
                        <div className="mt-2 flex gap-2">
                          <Button
                            size="sm"
                            variant="destructive"
                            className="h-7 px-2 text-xs"
                            onClick={async () => {
                              if (!doc.import_id) return;
                              await confirmDuplicate(doc.import_id);
                            }}
                          >
                            Confirm Duplicate - Remove This
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 px-2 text-xs"
                            onClick={async () => {
                              if (!doc.import_id) return;
                              await dismissDuplicate(doc.import_id);
                            }}
                          >
                            Not a Duplicate - Dismiss Flag
                          </Button>
                        </div>
                      </div>
                    ) : null}
                    <a
                      href={`http://localhost:8010/documents/${encodeURIComponent(doc.id)}/download`}
                      className="text-xs text-blue-600 underline"
                      target="_blank"
                    >
                      Download
                    </a>
                    <div className="mt-2 flex gap-2">
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={async () => {
                          const payload = await getDocumentPreview(doc.id);
                          setPreview(payload);
                        }}
                      >
                        Preview
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={deleteLoadingId === doc.id}
                        onClick={async () => {
                          await handleSingleDelete(doc.id, false);
                        }}
                      >
                        <span className="flex items-center gap-2">
                          {deleteLoadingId === doc.id ? <ButtonSpinner /> : null}
                          {deleteLoadingId === doc.id ? "Deleting..." : "Delete"}
                        </span>
                      </Button>
                    </div>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </Card>

      {showImportFileModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <Card className="w-full max-w-xl space-y-3 p-4">
            <h3 className="text-lg font-semibold">Import File</h3>
            <input
              ref={importFileInputRef}
              type="file"
              className="w-full rounded border p-2 text-sm"
              onChange={(event) => setImportFile(event.target.files?.[0] ?? null)}
            />
            <input
              type="date"
              className="w-full rounded border p-2 text-sm"
              value={importDataDate}
              onChange={(event) => setImportDataDate(event.target.value)}
            />
            <textarea
              className="min-h-20 w-full rounded border p-2 text-sm"
              placeholder="Notes (optional)"
              value={importNotes}
              onChange={(event) => setImportNotes(event.target.value)}
            />
            <div className="flex gap-2">
              <Button size="sm" disabled={importSubmitting} onClick={() => void submitSingleFileImport()}>
                <span className="flex items-center gap-2">
                  {importSubmitting ? <ButtonSpinner /> : null}
                  {importSubmitting ? "Importing..." : "Import"}
                </span>
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  setShowImportFileModal(false);
                  setImportFile(null);
                }}
              >
                Cancel
              </Button>
            </div>
          </Card>
        </div>
      ) : null}

      {showPasteTextModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <Card className="w-full max-w-2xl space-y-3 p-4">
            <h3 className="text-lg font-semibold">Paste Text</h3>
            <input
              type="text"
              className="w-full rounded border p-2 text-sm"
              placeholder="Filename (required)"
              value={pasteFilename}
              onChange={(event) => setPasteFilename(event.target.value)}
            />
            <textarea
              className="min-h-40 w-full rounded border p-2 text-sm"
              placeholder="Paste raw text here"
              value={pasteText}
              onChange={(event) => setPasteText(event.target.value)}
            />
            <input
              type="date"
              className="w-full rounded border p-2 text-sm"
              value={importDataDate}
              onChange={(event) => setImportDataDate(event.target.value)}
            />
            <textarea
              className="min-h-20 w-full rounded border p-2 text-sm"
              placeholder="Notes (optional)"
              value={importNotes}
              onChange={(event) => setImportNotes(event.target.value)}
            />
            <div className="flex gap-2">
              <Button size="sm" disabled={importSubmitting} onClick={() => void submitPasteTextImport()}>
                <span className="flex items-center gap-2">
                  {importSubmitting ? <ButtonSpinner /> : null}
                  {importSubmitting ? "Importing..." : "Import"}
                </span>
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  setShowPasteTextModal(false);
                }}
              >
                Cancel
              </Button>
            </div>
          </Card>
        </div>
      ) : null}

      {showSyncPreviewModal && syncPreview ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <Card className="w-full max-w-2xl space-y-3 p-4">
            <h3 className="text-lg font-semibold">{isDpaLibrary ? "DPA Folder Sync Preview" : "Folder Sync Preview"}</h3>
            <p className="text-sm">{syncPreview.total_scanned} files scanned</p>
            <div className="border-t" />
            <div className="space-y-1 text-sm">
              <p>✓ {syncPreview.unchanged} files already up to date</p>
              <p>+ {syncPreview.new} new files to import</p>
              <p>↑ {syncPreview.modified} modified files to update</p>
            </div>
            <div className="border-t" />
            {isDpaLibrary && (syncPreview.main_library_collisions || []).length > 0 ? (
              <div className="rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">
                <p className="font-semibold">Warning</p>
                <p className="mt-1">Some files already exist in the main library and will be imported separately into DPA:</p>
                <ul className="mt-1 list-disc pl-4">
                  {(syncPreview.main_library_collisions || []).map((name) => (
                    <li key={`collision-${name}`}>{name}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            <div className="space-y-3 rounded border p-2 text-xs">
              <div>
                <p className="font-semibold">New files:</p>
                <div className="mt-1 max-h-[200px] overflow-y-auto rounded border bg-muted/20 p-2">
                  {syncPreview.new_files.length ? (
                    <ul className="space-y-1">
                      {syncPreview.new_files.map((filename) => (
                        <li key={`new-${filename}`}>- {filename}</li>
                      ))}
                    </ul>
                  ) : (
                    <p>- None</p>
                  )}
                </div>
              </div>
              <div>
                <p className="font-semibold">Modified files:</p>
                <div className="mt-1 max-h-[200px] overflow-y-auto rounded border bg-muted/20 p-2">
                  {syncPreview.modified_files.length ? (
                    <ul className="space-y-1">
                      {syncPreview.modified_files.map((filename) => (
                        <li key={`modified-${filename}`}>- {filename}</li>
                      ))}
                    </ul>
                  ) : (
                    <p>- None</p>
                  )}
                </div>
              </div>
            </div>
            <div className="flex gap-2">
              <Button size="sm" onClick={async () => void runFolderSync(false)}>
                Sync Now
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  setShowSyncPreviewModal(false);
                  setSyncPreview(null);
                  setSyncCandidateFiles([]);
                }}
              >
                Cancel
              </Button>
            </div>
          </Card>
        </div>
      ) : null}

      {showSyncCompleteModal && syncSummary ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <Card className="w-full max-w-2xl space-y-3 p-4">
            <h3 className="text-lg font-semibold">Sync Complete</h3>
            <p className="text-sm">Files processed: {syncSummary.new + syncSummary.modified}</p>
            <p className="text-sm">✓ {syncSummary.new} new documents imported</p>
            <p className="text-sm">↑ {syncSummary.modified} documents updated</p>
            <p className="text-sm">= {syncSummary.unchanged} documents already current</p>
            <div className="rounded border p-2 text-xs">
              <p className="font-semibold">New documents</p>
              <ul className="mt-1 list-disc pl-4">
                {((syncSummary.new_details || []) as Array<{ filename: string; controls_linked: number }>).map((entry) => (
                  <li key={entry.filename}>
                    {entry.filename} - linked to {entry.controls_linked} controls
                  </li>
                ))}
              </ul>
              <p className="mt-2 font-semibold">Updated documents</p>
              <ul className="mt-1 list-disc pl-4">
                {((syncSummary.modified_details || []) as Array<{ filename: string; controls_linked: number }>).map((entry) => (
                  <li key={entry.filename}>
                    {entry.filename} - re-analyzed, {entry.controls_linked} control links updated
                  </li>
                ))}
              </ul>
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={() => {
                  setShowSyncCompleteModal(false);
                  setSyncSummary(null);
                }}
              >
                View Documents
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  setShowSyncCompleteModal(false);
                  setSyncSummary(null);
                }}
              >
                Close
              </Button>
            </div>
          </Card>
        </div>
      ) : null}

      {confirmation ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <Card className="w-full max-w-xl space-y-3 p-4">
            <p className="text-sm font-semibold">{confirmation.message}</p>
            <div className="flex gap-2">
              {confirmation.mode === "bulk" ? (
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={bulkDeleteProgress !== null}
                  onClick={async () => {
                    const targetIds = [...selectedIds];
                    setConfirmation(null);
                    setBulkDeleteProgress({ current: 0, total: targetIds.length });
                    let deleted = 0;
                    let failed = 0;
                    for (let i = 0; i < targetIds.length; i += 1) {
                      setBulkDeleteProgress({ current: i + 1, total: targetIds.length });
                      try {
                        const result = await bulkDeleteDocuments([targetIds[i]]);
                        deleted += result.deleted;
                        failed += result.failed;
                      } catch {
                        failed += 1;
                      }
                    }
                    if (preview && targetIds.includes(preview.id)) setPreview(null);
                    setSelectedIds([]);
                    setBulkDeleteProgress(null);
                    if (failed > 0) {
                      setErrorMessage(`Deleted ${deleted} documents, failed ${failed}.`);
                      toast.error(`Deleted ${deleted} documents, failed ${failed}.`);
                    } else {
                      setErrorMessage(null);
                      toast.success(`Deleted ${deleted} documents.`);
                    }
                    await refreshDocuments();
                  }}
                >
                  {bulkDeleteProgress ? (
                    <span className="flex items-center gap-2">
                      <ButtonSpinner />
                      Deleting {bulkDeleteProgress.current} of {bulkDeleteProgress.total}...
                    </span>
                  ) : (
                    `Delete ${selectedCount} Documents`
                  )}
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={confirmation.documentId !== undefined && deleteLoadingId === confirmation.documentId}
                  onClick={async () => {
                    const target = confirmation.documentId;
                    setConfirmation(null);
                    if (target) await handleSingleDelete(target, true);
                  }}
                >
                  {confirmation.documentId && deleteLoadingId === confirmation.documentId ? (
                    <span className="flex items-center gap-2">
                      <ButtonSpinner />
                      Deleting...
                    </span>
                  ) : (
                    "Delete Anyway"
                  )}
                </Button>
              )}
              <Button size="sm" variant="secondary" onClick={() => setConfirmation(null)}>
                Cancel
              </Button>
            </div>
          </Card>
        </div>
      ) : null}
      <ApiLimitOverrideDialog
        open={showOverrideDialog}
        estimate={overrideEstimate}
        message={overrideMessage}
        onRunAnyway={async () => {
          setShowOverrideDialog(false);
          setOverrideMessage(null);
          await runAnalyzeBatch(true);
        }}
        onIncreaseLimit={() => {
          setShowOverrideDialog(false);
          setOverrideMessage(null);
          window.location.href = "/settings#daily-api-limit-input";
        }}
        onCancel={() => {
          setShowOverrideDialog(false);
          setOverrideMessage(null);
        }}
      />
      <ApiLimitOverrideDialog
        open={showSyncOverrideDialog}
        estimate={syncEstimate}
        message={null}
        onRunAnyway={async () => {
          setShowSyncOverrideDialog(false);
          await runFolderSync(true);
        }}
        onIncreaseLimit={() => {
          setShowSyncOverrideDialog(false);
          window.location.href = "/settings#daily-api-limit-input";
        }}
        onCancel={() => {
          setShowSyncOverrideDialog(false);
        }}
      />
    </div>
  );
}
