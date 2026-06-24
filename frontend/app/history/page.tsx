"use client";

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { getHistoryEntries, processImportById, type ChangeLogEntry } from "@/lib/api";

const CATEGORY_OPTIONS = ["all", "document", "evidence", "control", "finding", "obligation", "auditor", "settings", "sync"];

function relativeTime(value: string): string {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return value;
  const diff = Date.now() - timestamp;
  if (diff < 60_000) return "just now";
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function categoryClass(category: string): string {
  if (category === "document") return "bg-blue-100 text-blue-800";
  if (category === "evidence") return "bg-green-100 text-green-800";
  if (category === "finding") return "bg-amber-100 text-amber-900";
  if (category === "sync") return "bg-purple-100 text-purple-800";
  return "bg-slate-100 text-slate-800";
}

export default function HistoryPage() {
  const [entries, setEntries] = useState<ChangeLogEntry[]>([]);
  const [category, setCategory] = useState("all");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [loading, setLoading] = useState(true);
  const [reprocessingImportId, setReprocessingImportId] = useState<number | null>(null);

  const params = useMemo(
    () => ({
      category: category === "all" ? undefined : category,
      start_date: startDate || undefined,
      end_date: endDate || undefined,
      limit: 100,
    }),
    [category, endDate, startDate],
  );

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const result = await getHistoryEntries(params);
        if (!cancelled) setEntries(result.entries || []);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [params]);

  function extractImportId(entry: ChangeLogEntry): number | null {
    const blob = `${entry.subject || ""} ${entry.detail || ""}`;
    const match = blob.match(/import_id\s*=\s*(\d+)/i);
    if (!match) return null;
    const value = Number.parseInt(match[1], 10);
    return Number.isFinite(value) ? value : null;
  }

  async function reprocessFailedImport(importId: number) {
    setReprocessingImportId(importId);
    try {
      await processImportById(importId, { bypass_limit: false });
      toast.success(`Queued reprocess for import #${importId}.`);
      const result = await getHistoryEntries(params);
      setEntries(result.entries || []);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to reprocess import.");
    } finally {
      setReprocessingImportId(null);
    }
  }

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <h2 className="text-lg font-semibold">Revision History</h2>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <select className="rounded border px-2 py-1" value={category} onChange={(e) => setCategory(e.target.value)}>
            {CATEGORY_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option === "all" ? "All categories" : option}
              </option>
            ))}
          </select>
          <input type="date" className="rounded border px-2 py-1" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
          <input type="date" className="rounded border px-2 py-1" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
        </div>
      </Card>

      <Card className="p-4">
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading history...</p>
        ) : entries.length === 0 ? (
          <p className="text-sm text-muted-foreground">No history entries found.</p>
        ) : (
          <div className="space-y-2">
            {entries.map((entry) => (
              <div key={entry.id} className="rounded border p-3 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className={`rounded px-2 py-0.5 text-xs ${categoryClass(entry.category)}`}>{entry.category}</span>
                  <span className="text-xs text-muted-foreground" title={new Date(entry.timestamp).toLocaleString()}>
                    {relativeTime(entry.timestamp)}
                  </span>
                </div>
                <p className="mt-1 font-medium">
                  {entry.action}
                  {entry.subject ? `: ${entry.subject}` : ""}
                </p>
                {entry.detail ? <p className="mt-1 text-xs text-muted-foreground">{entry.detail}</p> : null}
                {entry.action.toLowerCase() === "import failed" && extractImportId(entry) ? (
                  <div className="mt-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={reprocessingImportId === extractImportId(entry)}
                      onClick={() => {
                        const importId = extractImportId(entry);
                        if (!importId) return;
                        void reprocessFailedImport(importId);
                      }}
                    >
                      Reprocess
                    </Button>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
