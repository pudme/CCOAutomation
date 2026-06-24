"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { ButtonSpinner, ErrorState, SkeletonCard, StatusMessage } from "@/components/shared/LoadingStates";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { getPersonnelReport } from "@/lib/api";
import { formatStatus } from "@/lib/utils";

type TableRow = {
  name: string;
  email: string;
  entity: string;
  flagType: string;
};

function StatusCard({ label, value }: { label: string; value: number }) {
  const tone = value > 0 ? "border-red-300 bg-red-50 text-red-900" : "border-green-300 bg-green-50 text-green-900";
  return (
    <Card className={`p-4 ${tone}`}>
      <p className="text-xs">{label}</p>
      <p className="mt-1 text-3xl font-semibold">{value}</p>
    </Card>
  );
}

export default function PersonnelPage() {
  const [report, setReport] = useState<Record<string, any> | null>(null);
  const [loading, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({
    training: true,
    access: false,
    nda: false,
    mfa: false,
  });

  async function runCheck() {
    setLoading(true);
    setError(null);
    try {
      const data = await getPersonnelReport();
      setReport(data);
      toast.success("Personnel compliance check complete.");
    } catch {
      setError("Failed to run personnel compliance check.");
      toast.error("Failed to run personnel compliance check.");
    } finally {
      setLoading(false);
      setInitialLoading(false);
    }
  }

  useEffect(() => {
    void runCheck();
  }, []);

  const trainingRows: TableRow[] = ((report?.training_gaps ?? []) as Array<Record<string, string>>).map((item) => ({
    name: item.employee_name || "Unknown User",
    email: item.email || "N/A",
    entity: item.entity || "N/A",
    flagType: item.flag_type || "training_gap",
  }));
  const accessRows: TableRow[] = ((report?.access_revocation_exceptions ?? []) as Array<Record<string, string>>).map((item) => ({
    name: item.employee_name || "Unknown User",
    email: item.email || "N/A",
    entity: "N/A",
    flagType: "termination_access_revocation_gap",
  }));
  const ndaRows: TableRow[] = ((report?.nda_gaps ?? []) as Array<Record<string, string>>).map((item) => ({
    name: item.employee_name || "Unknown User",
    email: item.email || "N/A",
    entity: item.entity || "N/A",
    flagType: "nda_missing",
  }));
  const mfaRows: TableRow[] = ((report?.mfa_gaps ?? []) as Array<Record<string, string>>).map((item) => ({
    name: item.employee_name || "Unknown User",
    email: item.email || "N/A",
    entity: item.entity || "N/A",
    flagType: "mfa_missing",
  }));

  const showIncompleteBanner = useMemo(() => {
    const totalRows = trainingRows.length + accessRows.length + ndaRows.length + mfaRows.length;
    if (!report) return true;
    if (report.total_active_employees === 0) return true;
    if (totalRows === 0) return false;
    const names = [...trainingRows, ...accessRows, ...ndaRows, ...mfaRows].map((row) => row.name.toLowerCase());
    return names.every((name) => name === "unknown user");
  }, [accessRows, mfaRows, ndaRows, report, trainingRows]);

  if (initialLoading) {
    return (
      <div className="space-y-3">
        <SkeletonCard lines={3} />
        <SkeletonCard lines={3} />
      </div>
    );
  }

  if (error && !report) {
    return <ErrorState title="Failed to load personnel report" description={error} onRetry={() => void runCheck()} />;
  }

  if (!report) {
    return <ErrorState title="No personnel report available" description="Run the compliance check to load data." onRetry={() => void runCheck()} />;
  }

  function renderSection(title: string, sectionKey: string, rows: TableRow[]) {
    const isOpen = !!expanded[sectionKey];
    return (
      <Card className="p-0" key={sectionKey}>
        <button
          className="flex w-full items-center justify-between p-4 text-left"
          onClick={() => setExpanded((prev) => ({ ...prev, [sectionKey]: !prev[sectionKey] }))}
        >
          <span className="font-semibold">{title}</span>
          <span className="text-xs text-muted-foreground">{isOpen ? "Collapse" : "Expand"}</span>
        </button>
        {isOpen ? (
          <div className="border-t p-4">
            {rows.length === 0 ? (
              <div className="rounded border border-green-300 bg-green-50 p-3 text-sm text-green-900">No issues found</div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left">
                    <th className="pb-2">Name</th>
                    <th className="pb-2">Email</th>
                    <th className="pb-2">Entity</th>
                    <th className="pb-2">Flag Type</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, idx) => (
                    <tr key={`${row.name}-${idx}`} className="border-b">
                      <td className="py-2">{row.name}</td>
                      <td className="py-2">{row.email}</td>
                      <td className="py-2">{row.entity}</td>
                      <td className="py-2">{formatStatus(row.flagType)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ) : null}
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Personnel Compliance</h2>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground">
            Last run: {new Date(report.run_timestamp).toLocaleString()}
          </span>
          <Button onClick={() => void runCheck()} disabled={loading}>
            <span className="flex items-center gap-2">
              {loading ? <ButtonSpinner /> : null}
              {loading ? "Running compliance check..." : "Run Check"}
            </span>
          </Button>
        </div>
      </div>
      {loading ? (
        <StatusMessage
          type="info"
          message="Running compliance check... This usually takes 5-10 seconds."
        />
      ) : null}
      {error ? <StatusMessage type="error" message={error} /> : null}

      {showIncompleteBanner ? (
        <div className="rounded border border-yellow-300 bg-yellow-50 p-3 text-sm text-yellow-900">
          Personnel data is incomplete. Import your HR employee list, training completion report, and MFA enrollment
          report via the Import Data page.
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        <StatusCard label="Training Gaps" value={report.summary.training_gap_count} />
        <StatusCard label="Access Revocation Issues" value={report.summary.access_revocation_count} />
        <StatusCard label="NDA Gaps" value={report.summary.nda_gap_count} />
        <StatusCard label="MFA Gaps" value={report.summary.mfa_gap_count} />
      </div>

      <div className="space-y-3">
        {renderSection("Training Gaps", "training", trainingRows)}
        {renderSection("Access Revocation Issues", "access", accessRows)}
        {renderSection("NDA Gaps", "nda", ndaRows)}
        {renderSection("MFA Gaps", "mfa", mfaRows)}
      </div>
    </div>
  );
}

