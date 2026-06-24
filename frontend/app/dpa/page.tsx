"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { Card } from "@/components/ui/card";
import { getAuditInfo, getFrameworkControls } from "@/lib/api";

export default function DpaOverviewPage() {
  const [controls, setControls] = useState<any[]>([]);
  const [auditInfo, setAuditInfo] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const run = async () => {
      setLoading(true);
      try {
        const [controlRows, audit] = await Promise.all([
          getFrameworkControls("dpa_attachment_c"),
          getAuditInfo(),
        ]);
        setControls(controlRows);
        setAuditInfo(audit);
      } finally {
        setLoading(false);
      }
    };
    void run();
  }, []);

  const evidencedCount = useMemo(
    () => controls.filter((row) => row.status === "evidenced").length,
    [controls],
  );
  const total = controls.length;
  const pct = total > 0 ? Math.round((evidencedCount / total) * 100) : 0;
  const openObligations = Math.max(0, total - evidencedCount);

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <h2 className="text-xl font-semibold">Deferred Prosecution Agreement - Apprio Inc.</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Effective June 12, 2025 | Term: 3 years | Expires: June 12, 2028
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          Review periods: Initial review (complete), Follow-up Review 1 (current), Follow-up Review 2 (upcoming)
        </p>
      </Card>

      <Card className="p-4">
        <h3 className="font-semibold">Attachment C Coverage</h3>
        {loading ? (
          <p className="mt-2 text-sm text-muted-foreground">Loading DPA coverage...</p>
        ) : (
          <div className="mt-3 flex items-center gap-4">
            <div className="relative h-16 w-16 text-blue-600">
              <svg viewBox="0 0 42 42" className="h-16 w-16 -rotate-90">
                <circle cx="21" cy="21" r="15.915" fill="none" stroke="currentColor" strokeOpacity="0.15" strokeWidth="5" />
                <circle
                  cx="21"
                  cy="21"
                  r="15.915"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="5"
                  strokeDasharray={`${pct} ${100 - pct}`}
                />
              </svg>
              <span className="absolute inset-0 flex items-center justify-center text-xs font-semibold">{pct}%</span>
            </div>
            <div className="text-sm">
              <p>{evidencedCount}/{total} controls evidenced</p>
              <p>Open obligations: {openObligations}</p>
              <p>
                Days until next review period:{" "}
                {auditInfo?.dpa?.days_remaining === null || auditInfo?.dpa?.days_remaining === undefined
                  ? "Not configured"
                  : Math.max(0, auditInfo.dpa.days_remaining)}
              </p>
            </div>
          </div>
        )}
        <div className="mt-4 flex gap-2">
          <Link href="/dpa/documents" className="rounded border px-3 py-2 text-sm hover:bg-accent">
            DPA Documents
          </Link>
          <Link href="/dpa/auditor" className="rounded border px-3 py-2 text-sm hover:bg-accent">
            DPA Auditor
          </Link>
        </div>
      </Card>
    </div>
  );
}
