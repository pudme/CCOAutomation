"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { ApiLimitOverrideDialog } from "@/components/shared/ApiLimitOverrideDialog";
import { ButtonSpinner, SkeletonCard } from "@/components/shared/LoadingStates";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  estimateBatchCost,
  patchApiEnabled,
  patchApiLimit,
  patchAuditDates,
} from "@/lib/api";
import { armOneTimeBatchBypass } from "@/lib/api-limit-override";
import { useApiUsage, useAsyncAction, useAuditInfo } from "@/lib/hooks";

export default function SettingsPage() {
  const { auditInfo, loading, refresh } = useAuditInfo();
  const { usage, refresh: refreshUsage } = useApiUsage(30000);
  const [editing, setEditing] = useState<"iso" | "cmmc" | "dpa" | "ato" | null>(null);
  const [isoDraft, setIsoDraft] = useState("");
  const [cmmcDraft, setCmmcDraft] = useState("");
  const [dpaDraft, setDpaDraft] = useState("");
  const [atoDraft, setAtoDraft] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [apiStatus, setApiStatus] = useState<string | null>(null);
  const [dailyLimitDraft, setDailyLimitDraft] = useState<number>(usage?.daily_limit ?? 200);
  const [showOverrideDialog, setShowOverrideDialog] = useState(false);
  const [overrideEstimate, setOverrideEstimate] = useState<any | null>(null);
  const [killSwitchPending, setKillSwitchPending] = useState(false);
  const [optimisticEnabled, setOptimisticEnabled] = useState<boolean | null>(null);
  const saveAction = useAsyncAction();
  const apiAction = useAsyncAction();

  const rows = auditInfo
    ? [
        {
          key: "iso" as const,
          auditName: "ISO Surveillance Audit",
          frameworks: "ISO 27001, ISO 20000, ISO 9001",
          auditDate: auditInfo.iso.audit_date,
          daysRemaining: auditInfo.iso.days_remaining,
        },
        {
          key: "cmmc" as const,
          auditName: "CMMC Level 2 Assessment",
          frameworks: "CMMC Level 2",
          auditDate: auditInfo.cmmc.audit_date,
          daysRemaining: auditInfo.cmmc.days_remaining,
        },
        {
          key: "dpa" as const,
          auditName: "DPA Follow-up Review",
          frameworks: "Attachment C",
          auditDate: auditInfo.dpa.audit_date || "",
          daysRemaining: auditInfo.dpa.days_remaining,
        },
        {
          key: "ato" as const,
          auditName: "ATO Readiness",
          frameworks: "NIST 800-53 Moderate",
          auditDate: auditInfo.ato.audit_date || "",
          daysRemaining: auditInfo.ato.days_remaining,
        },
      ]
    : [];

  useEffect(() => {
    if (usage?.daily_limit) {
      setDailyLimitDraft(usage.daily_limit);
    }
  }, [usage?.daily_limit]);

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-semibold">Settings</h2>
      <Card className="p-4">
        <h3 className="font-semibold">Audit Configuration</h3>
        {loading || !auditInfo ? (
          <div className="mt-3 space-y-2">
            <SkeletonCard lines={3} />
          </div>
        ) : (
          <div className="mt-3 space-y-3">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] border text-sm">
                <thead className="bg-muted/40 text-left">
                  <tr>
                    <th className="border px-3 py-2">Audit</th>
                    <th className="border px-3 py-2">Frameworks Covered</th>
                    <th className="border px-3 py-2">Date</th>
                    <th className="border px-3 py-2">Days Remaining</th>
                    <th className="border px-3 py-2">Edit</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.key}>
                      <td className="border px-3 py-2 font-medium">{row.auditName}</td>
                      <td className="border px-3 py-2">{row.frameworks}</td>
                      <td className="border px-3 py-2">
                        {editing === row.key ? (
                          <input
                            type="date"
                            className="rounded border px-2 py-1"
                            value={
                              row.key === "iso"
                                ? isoDraft
                                : row.key === "cmmc"
                                  ? cmmcDraft
                                  : row.key === "dpa"
                                    ? dpaDraft
                                    : atoDraft
                            }
                            onChange={(event) => {
                              if (row.key === "iso") {
                                setIsoDraft(event.target.value);
                              } else if (row.key === "cmmc") {
                                setCmmcDraft(event.target.value);
                              } else if (row.key === "dpa") {
                                setDpaDraft(event.target.value);
                              } else {
                                setAtoDraft(event.target.value);
                              }
                            }}
                          />
                        ) : (
                          row.auditDate
                        )}
                      </td>
                      <td className="border px-3 py-2">
                        {row.auditDate && row.daysRemaining !== null ? Math.max(0, row.daysRemaining) : "--"}
                      </td>
                      <td className="border px-3 py-2">
                        {editing === row.key ? (
                          <div className="flex gap-2">
                            <Button
                              size="sm"
                              disabled={saving || !isoDraft || !cmmcDraft}
                              onClick={async () => {
                                setSaving(true);
                                setStatus(null);
                                await saveAction.execute(async () => {
                                  await patchAuditDates({
                                    iso_audit_date: isoDraft,
                                    cmmc_audit_date: cmmcDraft,
                                    dpa_audit_date: dpaDraft || null,
                                    ato_audit_date: atoDraft || null,
                                  });
                                  await refresh();
                                  setStatus("Audit dates updated.");
                                  setEditing(null);
                                }, { loadingMessage: "Saving audit dates...", successMessage: "Audit dates updated.", errorMessage: "Failed to update audit dates." });
                                setSaving(false);
                              }}
                            >
                              <span className="flex items-center gap-2">
                                {saving ? <ButtonSpinner /> : null}
                                {saving ? "Saving..." : "Save"}
                              </span>
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => {
                                setEditing(null);
                                setStatus(null);
                              }}
                            >
                              Cancel
                            </Button>
                          </div>
                        ) : (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setIsoDraft(auditInfo.iso.audit_date);
                              setCmmcDraft(auditInfo.cmmc.audit_date);
                              setDpaDraft(auditInfo.dpa.audit_date || "");
                              setAtoDraft(auditInfo.ato.audit_date || "");
                              setEditing(row.key);
                              setStatus(null);
                            }}
                          >
                            ✏
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {status ? <p className="text-sm text-muted-foreground">{status}</p> : null}
          </div>
        )}
      </Card>

      <Card className="space-y-4 p-4">
        <h3 className="font-semibold">API Usage Controls</h3>
        {!usage ? (
          <SkeletonCard lines={3} />
        ) : (
          <>
            <div className="space-y-2">
              <p className="text-sm font-medium">
                Today&apos;s API Usage: {usage.today_count} / {usage.daily_limit} calls (
                {Math.round((usage.today_count / Math.max(usage.daily_limit, 1)) * 100)}%)
              </p>
              <div className="h-2 w-full rounded bg-muted">
                <div
                  className={`h-2 rounded ${
                    usage.today_count / Math.max(usage.daily_limit, 1) > 0.8
                      ? "bg-red-500"
                      : usage.today_count / Math.max(usage.daily_limit, 1) >= 0.6
                        ? "bg-yellow-500"
                        : "bg-green-500"
                  }`}
                  style={{
                    width: `${Math.max(0, Math.min(100, (usage.today_count / Math.max(usage.daily_limit, 1)) * 100))}%`,
                  }}
                />
              </div>
              <p className="text-xs text-muted-foreground">Estimated cost today: ${usage.estimated_cost_today.toFixed(2)}</p>
              <p className="text-xs text-muted-foreground">Resets at midnight UTC</p>
            </div>

            <div className="space-y-2 rounded border p-3">
              <label className="text-sm font-medium">Daily API Call Limit</label>
              <div className="flex items-center gap-2">
                <input
                  id="daily-api-limit-input"
                  type="number"
                  min={1}
                  className="w-40 rounded border px-2 py-1 text-sm"
                  value={dailyLimitDraft}
                  onChange={(event) => setDailyLimitDraft(Number(event.target.value || "0"))}
                />
                <Button
                  size="sm"
                  disabled={apiAction.loading}
                  onClick={async () => {
                    await apiAction.execute(
                      async () => {
                        await patchApiLimit({ daily_limit: Math.max(1, dailyLimitDraft) });
                        await refreshUsage();
                        setApiStatus("Daily limit updated.");
                      },
                      {
                        loadingMessage: "Saving daily limit...",
                        successMessage: "Daily limit updated.",
                        errorMessage: "Failed to update daily limit.",
                      },
                    );
                  }}
                >
                  <span className="flex items-center gap-2">
                    {apiAction.loading ? <ButtonSpinner /> : null}
                    {apiAction.loading ? "Saving..." : "Save"}
                  </span>
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                Default: 200 calls/day (~$0.60). A full document reanalysis of 446 files requires ~446 calls (~$1.34).
              </p>
            </div>

            <div className="space-y-2 rounded border p-3">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium">Enable AI Features</p>
                {(() => {
                  const isEnabled = optimisticEnabled ?? usage.enabled;
                  return (
                <button
                  type="button"
                  className={`h-6 w-12 rounded-full p-0.5 transition ${isEnabled ? "bg-green-500" : "bg-red-500"} ${killSwitchPending ? "opacity-60" : ""}`}
                  disabled={killSwitchPending}
                  onClick={async () => {
                    setKillSwitchPending(true);
                    const targetEnabled = !isEnabled;
                    setOptimisticEnabled(targetEnabled);
                    setApiStatus(`Saving...`);
                    try {
                      await patchApiEnabled({ enabled: targetEnabled });
                      await refreshUsage();
                      setApiStatus(`AI features ${targetEnabled ? "enabled" : "disabled"}.`);
                      toast.success(`AI features ${targetEnabled ? "enabled" : "disabled"}.`);
                      setOptimisticEnabled(null);
                    } catch {
                      toast.error("Failed to update AI features toggle.");
                      setApiStatus("Failed to update AI features toggle.");
                      setOptimisticEnabled(null);
                    } finally {
                      setKillSwitchPending(false);
                    }
                  }}
                >
                  <span
                    className={`block h-5 w-5 rounded-full bg-white transition ${
                      isEnabled ? "translate-x-6" : "translate-x-0"
                    }`}
                  />
                </button>
                  );
                })()}
              </div>
            </div>

            <div className="space-y-2 rounded border p-3">
              <Button
                variant="destructive"
                disabled={apiAction.loading}
                onClick={async () => {
                  await apiAction.execute(
                    async () => {
                      const estimate = await estimateBatchCost({ num_calls: 50 });
                      setOverrideEstimate(estimate);
                      setShowOverrideDialog(true);
                    },
                    {
                      loadingMessage: "Estimating unrestricted batch cost...",
                      errorMessage: "Failed to estimate unrestricted batch cost.",
                    },
                  );
                }}
              >
                Run Unrestricted Batch Operation
              </Button>
            </div>
          </>
        )}
        {apiStatus ? <p className="text-sm text-muted-foreground">{apiStatus}</p> : null}
      </Card>

      <Card className="p-4">
        <h3 className="font-semibold">About</h3>
        <div className="mt-3 space-y-1 text-sm">
          <p>Platform version: 1.0.0</p>
        </div>
      </Card>

      <ApiLimitOverrideDialog
        open={showOverrideDialog}
        estimate={overrideEstimate}
        onRunAnyway={() => {
          armOneTimeBatchBypass();
          setShowOverrideDialog(false);
          setApiStatus("One-time batch bypass armed for the next batch request.");
        }}
        onIncreaseLimit={() => {
          setShowOverrideDialog(false);
          const target = document.getElementById("daily-api-limit-input");
          if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
        }}
        onCancel={() => setShowOverrideDialog(false)}
      />
    </div>
  );
}
