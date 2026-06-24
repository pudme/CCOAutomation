"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { getApiUsage, getAuditInfo, getReanalyzeStatus } from "@/lib/api";
import { useAnalysisProgressStore } from "@/lib/stores/analysis-progress";

type AuditInfo = {
  iso: {
    audit_date: string;
    days_remaining: number;
    label: string;
    frameworks: string[];
  };
  cmmc: {
    audit_date: string;
    days_remaining: number;
    label: string;
    frameworks: string[];
  };
  dpa: {
    audit_date: string | null;
    days_remaining: number | null;
    label: string;
    frameworks: string[];
  };
  ato: {
    audit_date: string | null;
    days_remaining: number | null;
    label: string;
    frameworks: string[];
  };
};

let auditInfoCache: AuditInfo | null = null;

export function useAuditInfo() {
  const [auditInfo, setAuditInfo] = useState<AuditInfo | null>(auditInfoCache);
  const [loading, setLoading] = useState<boolean>(auditInfoCache === null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await getAuditInfo();
      auditInfoCache = payload;
      setAuditInfo(payload);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load audit settings");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (auditInfoCache === null) {
      void refresh();
    }
  }, [refresh]);

  return { auditInfo, loading, error, refresh };
}

type ApiUsage = {
  today_count: number;
  daily_limit: number;
  remaining: number;
  enabled: boolean;
  reset_at: string;
  estimated_cost_today: number;
};

export function useApiUsage(pollMs = 30000) {
  const [usage, setUsage] = useState<ApiUsage | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const payload = await getApiUsage();
      setUsage(payload);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, pollMs);
    return () => window.clearInterval(timer);
  }, [pollMs, refresh]);

  return { usage, loading, refresh };
}

export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timeout = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timeout);
  }, [value, delayMs]);

  return debounced;
}

type AsyncActionOptions = {
  loadingMessage?: string;
  successMessage?: string;
  errorMessage?: string;
};

export function useAsyncAction() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const execute = useCallback(async (action: () => Promise<void>, options?: AsyncActionOptions) => {
    setLoading(true);
    setError(null);
    const loadingToastId = options?.loadingMessage ? toast.loading(options.loadingMessage) : null;
    let timedOut = false;

    const timeoutId = window.setTimeout(() => {
      timedOut = true;
      setLoading(false);
      toast.error(options?.errorMessage || "Request timed out - please try again");
      if (loadingToastId) toast.dismiss(loadingToastId);
    }, 30000);

    try {
      await action();
      if (timedOut) return;
      window.clearTimeout(timeoutId);
      if (loadingToastId) toast.dismiss(loadingToastId);
      if (options?.successMessage) toast.success(options.successMessage);
    } catch (e) {
      if (timedOut) return;
      window.clearTimeout(timeoutId);
      if (loadingToastId) toast.dismiss(loadingToastId);
      const msg =
        options?.errorMessage ||
        (e instanceof Error ? e.message : null) ||
        "Something went wrong";
      setError(msg);
      toast.error(msg);
    } finally {
      if (!timedOut) {
        setLoading(false);
      }
    }
  }, []);

  return { loading, error, execute };
}

export type ImportProgressSnapshot = {
  running: boolean;
  total: number;
  complete: number;
  failed: number;
  queued: number;
  processing: number;
  batchIds: string[];
  startedAt: number;
  updatedAt: number;
  apiBase: string;
};

export const IMPORT_PROGRESS_STORAGE_KEY = "coo_import_progress";

function getDefaultApiBase(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";
}

export function readImportProgress(): ImportProgressSnapshot | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(IMPORT_PROGRESS_STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as ImportProgressSnapshot;
  } catch {
    return null;
  }
}

export function writeImportProgress(snapshot: ImportProgressSnapshot | null): void {
  if (typeof window === "undefined") return;
  if (snapshot === null) {
    window.localStorage.removeItem(IMPORT_PROGRESS_STORAGE_KEY);
    return;
  }
  window.localStorage.setItem(IMPORT_PROGRESS_STORAGE_KEY, JSON.stringify(snapshot));
}

type BatchStatus = {
  batch_id: string;
  total_files: number;
  queued: number;
  processing: number;
  complete: number;
  failed: number;
  skipped: number;
};

export function useImportProgressBanner() {
  const [snapshot, setSnapshot] = useState<ImportProgressSnapshot | null>(null);
  const batchIdsKey = (snapshot?.batchIds || []).join(",");

  useEffect(() => {
    const syncFromStorage = () => setSnapshot(readImportProgress());
    syncFromStorage();
    const onStorage = (event: StorageEvent) => {
      if (event.key === IMPORT_PROGRESS_STORAGE_KEY) syncFromStorage();
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  useEffect(() => {
    if (!snapshot?.running || snapshot.batchIds.length === 0) return;
    const poll = async () => {
      try {
        const results = await Promise.all(
          snapshot.batchIds.map(async (batchId) => {
            const response = await fetch(`${snapshot.apiBase || getDefaultApiBase()}/import/batch/${batchId}/status`, {
              cache: "no-store",
            });
            if (!response.ok) throw new Error("Failed to fetch import status");
            return (await response.json()) as BatchStatus;
          }),
        );
        const total = results.reduce((acc, row) => acc + row.total_files, 0) || snapshot.total;
        const complete = results.reduce((acc, row) => acc + row.complete, 0);
        const failed = results.reduce((acc, row) => acc + row.failed, 0);
        const queued = results.reduce((acc, row) => acc + row.queued, 0);
        const processing = results.reduce((acc, row) => acc + row.processing, 0);
        const done = results.reduce((acc, row) => acc + row.complete + row.failed + row.skipped, 0);
        const next: ImportProgressSnapshot = {
          ...snapshot,
          total,
          complete,
          failed,
          queued,
          processing,
          running: done < total,
          updatedAt: snapshot.updatedAt || Date.now(),
        };
        writeImportProgress(next);
        setSnapshot(next);
      } catch {
        // keep current banner state if polling fails
      }
    };
    void poll();
    const timer = window.setInterval(() => {
      void poll();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [batchIdsKey, snapshot?.apiBase, snapshot?.running]);

  return { importProgress: snapshot };
}

export function useAnalysisProgressBanner(pollMs = 3000) {
  const [mounted, setMounted] = useState(false);
  const snapshot = useAnalysisProgressStore((state) => ({
    isAnalyzing: state.isAnalyzing,
    completed: state.completed,
    total: state.total,
  }));
  const setProgress = useAnalysisProgressStore((state) => state.setProgress);
  const clear = useAnalysisProgressStore((state) => state.clear);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted || !snapshot.isAnalyzing) return;
    const poll = async () => {
      try {
        const status = await getReanalyzeStatus();
        if (!status.running) {
          clear();
          return;
        }
        setProgress({
          isAnalyzing: true,
          completed: status.completed || 0,
          total: status.total || 0,
          newLinks: status.new_links || 0,
          message: status.message || "",
          lastDocument: status.last_document || null,
        });
      } catch {
        // keep current banner state on polling errors
      }
    };
    void poll();
    const timer = window.setInterval(() => {
      void poll();
    }, pollMs);
    return () => window.clearInterval(timer);
  }, [clear, mounted, pollMs, setProgress, snapshot.isAnalyzing]);

  return { analysisProgress: mounted ? snapshot : { isAnalyzing: false, completed: 0, total: 0 } };
}
