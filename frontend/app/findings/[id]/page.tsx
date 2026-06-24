"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */

import { useEffect, useState } from "react";

import { ButtonSpinner, EmptyState, ErrorState, SkeletonCard, StatusMessage } from "@/components/shared/LoadingStates";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { addCorrectiveAction, getFinding, patchFinding } from "@/lib/api";
import { useAsyncAction } from "@/lib/hooks";
import { formatStatus } from "@/lib/utils";

type Props = { params: Promise<{ id: string }> };

export default function FindingDetailPage({ params }: Props) {
  const [id, setId] = useState<string>("");
  const [finding, setFinding] = useState<any | null>(null);
  const [notes, setNotes] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusSaving, setStatusSaving] = useState(false);
  const action = useAsyncAction();

  useEffect(() => {
    params.then(({ id: findingId }) => {
      setId(findingId);
      getFinding(findingId)
        .then((data) => {
          setFinding(data);
          setError(null);
        })
        .catch(() => {
          setFinding(null);
          setError("Failed to load finding.");
        })
        .finally(() => setLoading(false));
    });
  }, [params]);

  const refresh = async () => {
    if (!id) return;
    const next = await getFinding(id);
    setFinding(next);
  };

  if (loading) {
    return (
      <div className="space-y-3">
        <SkeletonCard lines={3} />
        <SkeletonCard lines={3} />
      </div>
    );
  }

  if (error) {
    return <ErrorState title="Failed to load finding" description={error} onRetry={() => void refresh()} />;
  }

  if (!finding) {
    return <EmptyState title="Finding not found" description="The selected finding could not be loaded." />;
  }

  const updateFindingStatus = async (nextStatus: "in_progress" | "resolved" | "risk_accepted") => {
    setStatusSaving(true);
    await action.execute(
      async () => {
        await patchFinding(finding.finding_id, { status: nextStatus });
        await refresh();
      },
      { loadingMessage: "Saving finding...", successMessage: "Finding updated.", errorMessage: "Failed to update finding." },
    );
    setStatusSaving(false);
  };

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">
            {finding.finding_id} - {finding.title}
          </h2>
          <div className="flex gap-2">
            <Badge variant="outline">{formatStatus(finding.severity)}</Badge>
            <Badge>{formatStatus(finding.status)}</Badge>
          </div>
        </div>
        <p className="mt-2 text-sm text-muted-foreground">{finding.description}</p>
        <p className="mt-2 text-xs">Linked controls: {finding.linked_controls.join(", ") || "None"}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" disabled={statusSaving} onClick={async () => void updateFindingStatus("in_progress")}>
            <span className="flex items-center gap-2">
              {statusSaving ? <ButtonSpinner /> : null}
              {statusSaving ? "Saving..." : "Set In Progress"}
            </span>
          </Button>
          <Button size="sm" variant="secondary" disabled={statusSaving} onClick={async () => void updateFindingStatus("resolved")}>
            <span className="flex items-center gap-2">
              {statusSaving ? <ButtonSpinner /> : null}
              {statusSaving ? "Saving..." : "Mark Resolved"}
            </span>
          </Button>
          <Button size="sm" variant="outline" disabled={statusSaving} onClick={async () => void updateFindingStatus("risk_accepted")}>
            <span className="flex items-center gap-2">
              {statusSaving ? <ButtonSpinner /> : null}
              {statusSaving ? "Saving..." : "Risk Accept"}
            </span>
          </Button>
        </div>
      </Card>

      <Card className="p-4">
        <h3 className="font-semibold">Add Corrective Action</h3>
        <textarea
          className="mt-2 w-full rounded border p-2 text-sm"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Describe corrective action..."
        />
        <Button
          className="mt-2"
          disabled={action.loading}
          onClick={async () => {
            if (!notes.trim()) return;
            await action.execute(
              async () => {
                await addCorrectiveAction(finding.finding_id, {
                  description: notes,
                  owner: "Chief Compliance Officer",
                  due_date: null,
                });
                setNotes("");
                await refresh();
              },
              { loadingMessage: "Adding corrective action...", successMessage: "Corrective action added.", errorMessage: "Failed to add corrective action." },
            );
          }}
        >
          <span className="flex items-center gap-2">
            {action.loading ? <ButtonSpinner /> : null}
            {action.loading ? "Saving..." : "Add Action"}
          </span>
        </Button>
        {action.error ? <StatusMessage type="error" message={action.error} /> : null}
      </Card>

      <Card className="p-4">
        <h3 className="font-semibold">Corrective Actions</h3>
        <div className="mt-2 space-y-2">
          {finding.corrective_actions.map((action: any) => (
            <div key={action.id} className="rounded border p-2 text-sm">
              <p>{action.description}</p>
              <p className="text-xs text-muted-foreground">
                Owner: {action.owner || "Unassigned"} | Due: {action.due_date || "N/A"} | Status: {formatStatus(action.status)}
              </p>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
