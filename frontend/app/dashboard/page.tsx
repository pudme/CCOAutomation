"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { EmptyState, ErrorState, SkeletonCard } from "@/components/shared/LoadingStates";
import { Card } from "@/components/ui/card";
import { getDashboardSummary } from "@/lib/api";
import { useAuditInfo } from "@/lib/hooks";

type DashboardData = Awaited<ReturnType<typeof getDashboardSummary>>;

const SEVERITIES = [
  { key: "observation", label: "Observation", bg: "bg-slate-100 text-slate-800 border-slate-300" },
  { key: "minor_nc", label: "Minor NC", bg: "bg-yellow-100 text-yellow-900 border-yellow-300" },
  { key: "major_nc", label: "Major NC", bg: "bg-orange-100 text-orange-900 border-orange-300" },
  { key: "critical", label: "Critical", bg: "bg-red-100 text-red-900 border-red-300" },
];

function getUrgencyColor(daysRemaining: number): string {
  if (daysRemaining < 0) return "text-red-600";
  if (daysRemaining < 30) return "text-red-600";
  if (daysRemaining <= 60) return "text-yellow-600";
  return "text-green-600";
}

function getRingColor(percent: number): string {
  if (percent >= 90) return "text-green-600";
  if (percent >= 70) return "text-yellow-600";
  return "text-red-600";
}

function getDaysForFramework(
  framework: string,
  auditInfo: {
    iso: { days_remaining: number; frameworks: string[] };
    cmmc: { days_remaining: number; frameworks: string[] };
    ato: { days_remaining: number | null; frameworks: string[] };
  },
): number | null {
  if (auditInfo.iso.frameworks.includes(framework)) return auditInfo.iso.days_remaining;
  if (auditInfo.cmmc.frameworks.includes(framework)) return auditInfo.cmmc.days_remaining;
  if (auditInfo.ato.frameworks.includes(framework)) return auditInfo.ato.days_remaining;
  return null;
}

export default function DashboardPage() {
  const { auditInfo, loading: auditLoading, error: auditError, refresh: refreshAudit } = useAuditInfo();
  const [summary, setSummary] = useState<DashboardData | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  useEffect(() => {
    setSummaryLoading(true);
    getDashboardSummary()
      .then((payload) => {
        setSummary(payload);
        setSummaryError(null);
      })
      .catch(() => {
        setSummary(null);
        setSummaryError("Failed to load dashboard summary.");
      })
      .finally(() => setSummaryLoading(false));
  }, []);

  const readiness = useMemo(
    () =>
      (summary?.framework_readiness ?? []).filter(
        (item) => !item.name.toLowerCase().includes("obligation") && item.framework !== "obligations",
      ),
    [summary],
  );

  return (
    <div className="space-y-4">
      {auditLoading || !auditInfo ? (
        <SkeletonCard lines={4} />
      ) : auditError ? (
        <ErrorState title="Failed to load audit countdown" description={auditError} onRetry={() => void refreshAudit()} />
      ) : (
        <Card className="relative overflow-hidden p-6 text-center">
          <div className="absolute -right-8 -top-8 h-40 w-40 rounded-full bg-primary/10" />
          <h2 className="text-2xl font-semibold">{auditInfo.iso.label}</h2>
          {auditInfo.iso.days_remaining < 0 ? (
            <p className="mt-3 text-4xl font-bold text-red-600">Audit window reached</p>
          ) : (
            <p className={`mt-3 text-6xl font-bold ${getUrgencyColor(auditInfo.iso.days_remaining)}`}>
              {auditInfo.iso.days_remaining}
            </p>
          )}
          <p className="mt-1 text-sm text-muted-foreground">Days remaining until audit date {auditInfo.iso.audit_date}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {auditInfo.cmmc.label}: {Math.max(0, auditInfo.cmmc.days_remaining)} days ({auditInfo.cmmc.audit_date})
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link href="/import" className="rounded bg-primary px-3 py-2 text-sm text-primary-foreground">
              Import Data
            </Link>
            <Link href="/findings" className="rounded border px-3 py-2 text-sm">
              Review Findings
            </Link>
            <Link href="/chat" className="rounded border px-3 py-2 text-sm">
              Open AI Assistant
            </Link>
          </div>
        </Card>
      )}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {summaryLoading ? (
          <>
            <SkeletonCard lines={3} />
            <SkeletonCard lines={3} />
            <SkeletonCard lines={3} />
            <SkeletonCard lines={3} />
          </>
        ) : summaryError ? (
          <ErrorState title="Failed to load readiness widgets" description={summaryError} />
        ) : readiness.length === 0 ? (
          <EmptyState title="No frameworks yet" description="Import framework data to see readiness widgets." />
        ) : (
          readiness.map((item) => (
            <Link key={item.framework} href={item.mode === "auditor" ? "/auditor" : `/frameworks/${item.framework}`}>
              <Card className="p-4 hover:bg-accent/40">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm text-muted-foreground">{item.name}</p>
                  {item.mode === "auditor" ? (
                    <span className="rounded border border-amber-300 bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-800">
                      Audit Mode
                    </span>
                  ) : null}
                </div>
                <div className="mt-2 flex items-center gap-3">
                  <div className={`relative h-14 w-14 ${getRingColor(item.percentage)}`}>
                    <svg viewBox="0 0 42 42" className="h-14 w-14 -rotate-90">
                      <circle cx="21" cy="21" r="15.915" fill="none" stroke="currentColor" strokeOpacity="0.15" strokeWidth="5" />
                      <circle
                        cx="21"
                        cy="21"
                        r="15.915"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="5"
                        strokeDasharray={`${item.percentage} ${100 - item.percentage}`}
                        className="transition-all duration-700"
                      />
                    </svg>
                    <span className="absolute inset-0 flex items-center justify-center text-xs font-semibold">
                      {Math.round(item.percentage)}%
                    </span>
                  </div>
                  <p className="text-2xl font-semibold">{Math.round(item.percentage)}%</p>
                </div>
                <p className="text-xs text-muted-foreground">{item.progress_label}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {auditInfo
                    ? (() => {
                        const days = getDaysForFramework(item.framework, auditInfo);
                        return days === null ? "No assessment date" : `${Math.max(0, days)} days`;
                      })()
                    : "Loading..."}
                </p>
              </Card>
            </Link>
          ))
        )}
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <Card className="p-4">
          <h3 className="mb-2 font-semibold">Open Findings by Severity</h3>
          {summaryLoading ? (
            <SkeletonCard lines={3} />
          ) : summaryError || !summary ? (
            <ErrorState title="Failed to load findings summary" description="Try refreshing the page." />
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              {SEVERITIES.map((severity) => (
                <div key={severity.key} className={`rounded border p-3 ${severity.bg}`}>
                  <p className="text-xs">{severity.label}</p>
                  <p className="text-2xl font-semibold">{summary.open_findings_by_severity[severity.key] ?? 0}</p>
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card className="p-4">
          <h3 className="mb-2 font-semibold">Personnel Exceptions</h3>
          {summaryLoading ? (
            <SkeletonCard lines={3} />
          ) : summaryError || !summary ? (
            <ErrorState title="Failed to load personnel exceptions" description="Try refreshing the page." />
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {[
                { label: "Training Gaps", value: summary.personnel_exceptions.training_gaps },
                { label: "MFA Gaps", value: summary.personnel_exceptions.mfa_gaps },
                { label: "NDA Gaps", value: summary.personnel_exceptions.nda_gaps },
                { label: "Terminated Access Gaps", value: summary.personnel_exceptions.terminated_access_gaps },
              ].map((item) => (
                <div
                  key={item.label}
                  className={`rounded border p-3 ${item.value > 0 ? "border-red-300 bg-red-50 text-red-900" : "border-green-300 bg-green-50 text-green-900"}`}
                >
                  <p className="text-xs">{item.label}</p>
                  <p className="text-2xl font-semibold">{item.value}</p>
                </div>
              ))}
            </div>
          )}
        </Card>
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <Card className="p-4">
          <h3 className="mb-2 font-semibold">Obligations Due (30 Days)</h3>
          {summaryLoading ? (
            <SkeletonCard lines={3} />
          ) : summaryError || !summary ? (
            <ErrorState title="Failed to load obligations" description="Try refreshing the page." />
          ) : summary.obligations_due_30_days.length === 0 ? (
            <EmptyState title="No obligations due soon" description="You're clear for the next 30 days." />
          ) : (
            <div className="space-y-2 text-sm">
              {summary.obligations_due_30_days.map((item) => (
                <div key={item.obligation_id} className="rounded border p-2">
                  <p className="font-medium">{item.obligation_id}</p>
                  <p>{item.source}</p>
                  <p className="text-xs text-muted-foreground">Due: {item.due_date}</p>
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card className="p-4">
          <h3 className="mb-2 font-semibold">Recent Agent Activity Feed</h3>
          {summaryLoading ? (
            <SkeletonCard lines={3} />
          ) : summaryError || !summary ? (
            <ErrorState title="Failed to load recent activity" description="Try refreshing the page." />
          ) : summary.recent_agent_actions.length === 0 ? (
            <div className="rounded border border-dashed p-4 text-sm text-muted-foreground">
              No agent activity yet. Start a chat to begin.
            </div>
          ) : (
            <div className="space-y-2 text-sm">
              {summary.recent_agent_actions.map((item, idx) => (
                <div key={`${item.timestamp}-${idx}`} className="rounded border p-2">
                  <p className="font-medium">{item.tool_name}</p>
                  <p className="text-xs text-muted-foreground">{item.result_summary}</p>
                  <p className="text-[10px] text-muted-foreground">{new Date(item.timestamp).toLocaleString()}</p>
                </div>
              ))}
            </div>
          )}
        </Card>
      </section>
    </div>
  );
}

