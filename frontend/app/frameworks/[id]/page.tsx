/* eslint-disable @typescript-eslint/no-explicit-any */
import { EmptyState } from "@/components/shared/LoadingStates";
import { Card } from "@/components/ui/card";
import { getFrameworkControls, getFrameworkDetail } from "@/lib/api";
import { formatLabel, formatStatus } from "@/lib/utils";

export default async function FrameworkDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const detail = await getFrameworkDetail(id);
  const controls = await getFrameworkControls(id);

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <h2 className="text-xl font-semibold">{detail.name}</h2>
        <p className="text-sm text-muted-foreground">{formatLabel(detail.short_name)}</p>
        {detail.readiness_mode === "auditor" ? (
          <div className="mt-3 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">
            Active audit in progress. Completion is measured against the{" "}
            <span className="font-medium">{detail.active_checklist_name || "active auditor checklist"}</span>{" "}
            auditor checklist, not full framework coverage.
          </div>
        ) : null}
        <div className="mt-3 grid gap-2 md:grid-cols-3">
          {detail.domain_summary.map((domain: any) => (
            <div key={domain.domain} className="rounded border p-2 text-sm">
              <p className="font-medium">{domain.domain}</p>
              <p className="text-xs text-muted-foreground">
                {domain.evidenced_count}/{domain.control_count} evidenced
              </p>
            </div>
          ))}
        </div>
      </Card>

      <Card className="p-4">
        <h3 className="font-semibold">Controls</h3>
        <div className="mt-2 space-y-2">
          {controls.length === 0 ? (
            <EmptyState title="No controls found" description="This framework has no controls to display yet." />
          ) : (
            controls.map((control: any) => (
              <details key={control.control_id} className="rounded border p-2">
                <summary className="cursor-pointer list-none">
                  <p className="font-medium">
                    {control.control_id} - {control.title}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {control.domain} | status: {formatStatus(control.status)} | evidence: {control.evidence_on_file}/{control.evidence_required}
                  </p>
                </summary>
                <div className="mt-3 space-y-2 border-t pt-3 text-sm">
                  <p><span className="font-medium">Control ID:</span> {control.control_id}</p>
                  <p><span className="font-medium">Control Title:</span> {control.title}</p>
                  <p><span className="font-medium">Description / Objective:</span> {control.description || "No description provided."}</p>
                  <p><span className="font-medium">Implementation Guidance:</span> {control.implementation_guidance || "No implementation guidance provided."}</p>
                  <p><span className="font-medium">Current Status:</span> {formatStatus(control.status)}</p>
                  <p>
                    <span className="font-medium">Linked Evidence:</span> {control.evidence_on_file}{" "}
                    <a className="text-blue-600 underline" href={`/documents?control=${encodeURIComponent(control.control_id)}`}>
                      View documents
                    </a>
                  </p>
                </div>
              </details>
            ))
          )}
        </div>
      </Card>
    </div>
  );
}

