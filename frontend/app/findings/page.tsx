"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
/* eslint-disable react-hooks/set-state-in-effect */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { EmptyState, ErrorState, SkeletonCard } from "@/components/shared/LoadingStates";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { getFindings } from "@/lib/api";
import { formatStatus } from "@/lib/utils";

export default function FindingsPage() {
  const [findings, setFindings] = useState<any[]>([]);
  const [severity, setSeverity] = useState<string>("all");
  const [status, setStatus] = useState<string>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterLoading, setFilterLoading] = useState(false);

  async function loadFindings() {
    setLoading(true);
    setError(null);
    try {
      const payload = await getFindings();
      setFindings(payload);
    } catch {
      setFindings([]);
      setError("Failed to load findings.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadFindings();
  }, []);

  useEffect(() => {
    setFilterLoading(true);
    const timeout = window.setTimeout(() => setFilterLoading(false), 250);
    return () => window.clearTimeout(timeout);
  }, [severity, status]);

  const filtered = useMemo(
    () =>
      findings.filter(
        (item) =>
          (severity === "all" || item.severity === severity) &&
          (status === "all" || item.status === status),
      ),
    [findings, severity, status],
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <select className="rounded border px-2 py-1 text-sm" value={severity} onChange={(e) => setSeverity(e.target.value)}>
          <option value="all">All severities</option>
          <option value="observation">Observation</option>
          <option value="minor_nc">Minor NC</option>
          <option value="major_nc">Major NC</option>
          <option value="critical">Critical</option>
        </select>
        <select className="rounded border px-2 py-1 text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="all">All statuses</option>
          <option value="open">Open</option>
          <option value="in_progress">In Progress</option>
          <option value="risk_accepted">Risk Accepted</option>
          <option value="resolved">Resolved</option>
          <option value="closed">Closed</option>
        </select>
      </div>

      <div className="grid gap-3">
        {loading ? (
          <>
            <SkeletonCard lines={3} />
            <SkeletonCard lines={3} />
          </>
        ) : error ? (
          <ErrorState title="Failed to load findings" description="Could not load findings list." onRetry={() => void loadFindings()} />
        ) : filterLoading ? (
          <>
            <SkeletonCard lines={3} />
            <SkeletonCard lines={3} />
          </>
        ) : filtered.length === 0 ? (
          <EmptyState title="No findings yet" description="No findings match the current filters." />
        ) : (
          filtered.map((finding) => (
            <Link key={finding.finding_id} href={`/findings/${finding.finding_id}`}>
              <Card className="p-4 transition hover:bg-accent">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="font-semibold">{finding.finding_id}</p>
                    <p className="text-sm text-muted-foreground">{finding.title}</p>
                    <p className="mt-2 text-xs text-muted-foreground">{finding.description}</p>
                    <p className="mt-2 text-xs">Controls: {finding.linked_controls.join(", ") || "None"}</p>
                  </div>
                  <div className="flex flex-col items-end gap-2">
                    <Badge variant="outline">{formatStatus(finding.severity)}</Badge>
                    <Badge>{formatStatus(finding.status)}</Badge>
                  </div>
                </div>
              </Card>
            </Link>
          ))
        )}
      </div>
    </div>
  );
}

