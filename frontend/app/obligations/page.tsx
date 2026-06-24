"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useState } from "react";

import { ButtonSpinner, EmptyState, ErrorState, SkeletonCard } from "@/components/shared/LoadingStates";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { createObligation, deleteObligation, getObligations, patchObligation } from "@/lib/api";
import { useAsyncAction } from "@/lib/hooks";
import { formatStatus } from "@/lib/utils";

export default function ObligationsPage() {
  const [obligations, setObligations] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const action = useAsyncAction();

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getObligations();
      setObligations(data);
    } catch {
      setError("Failed to load obligations.");
      setObligations([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh().catch(() => setObligations([]));
  }, []);

  return (
    <div className="space-y-4">
      <Button
        disabled={action.loading}
        onClick={async () => {
          await action.execute(
            async () => {
              await createObligation({
                source: "Manual Entry",
                description: "New obligation from dashboard",
                owner: "Chief Compliance Officer",
                cadence: "Monthly",
                status: "current",
              });
              await refresh();
            },
            { loadingMessage: "Creating obligation...", successMessage: "Obligation created.", errorMessage: "Failed to create obligation." },
          );
        }}
      >
        <span className="flex items-center gap-2">
          {action.loading ? <ButtonSpinner /> : null}
          {action.loading ? "Saving..." : "Add Obligation"}
        </span>
      </Button>

      <div className="grid gap-3">
        {loading ? (
          <>
            <SkeletonCard lines={3} />
            <SkeletonCard lines={3} />
          </>
        ) : error ? (
          <ErrorState title="Failed to load obligations" description="Could not load obligations list." onRetry={() => void refresh()} />
        ) : obligations.length === 0 ? (
          <EmptyState title="No obligations yet" description="Create your first obligation to track upcoming requirements." />
        ) : (
          obligations.map((obligation) => (
            <Card key={obligation.obligation_id} className="p-4">
              <p className="font-semibold">{obligation.obligation_id}</p>
              <p className="text-sm">{obligation.description}</p>
              <p className="text-xs text-muted-foreground">
                {obligation.source} | Due: {obligation.due_date || "N/A"} | Status: {formatStatus(obligation.status)}
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={savingId === obligation.obligation_id}
                  onClick={async () => {
                    const id = obligation.obligation_id;
                    setSavingId(id);
                    await action.execute(
                      async () => {
                        await patchObligation(id, { status: "satisfied" });
                        await refresh();
                      },
                      { loadingMessage: "Saving obligation...", successMessage: "Obligation status updated.", errorMessage: "Failed to update obligation." },
                    );
                    setSavingId(null);
                  }}
                >
                  <span className="flex items-center gap-2">
                    {savingId === obligation.obligation_id ? <ButtonSpinner /> : null}
                    {savingId === obligation.obligation_id ? "Saving..." : "Mark Satisfied"}
                  </span>
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={savingId === obligation.obligation_id}
                  onClick={async () => {
                    const id = obligation.obligation_id;
                    setSavingId(id);
                    await action.execute(
                      async () => {
                        await deleteObligation(id);
                        await refresh();
                      },
                      { loadingMessage: "Deleting obligation...", successMessage: "Obligation removed.", errorMessage: "Failed to delete obligation." },
                    );
                    setSavingId(null);
                  }}
                >
                  <span className="flex items-center gap-2">
                    {savingId === obligation.obligation_id ? <ButtonSpinner /> : null}
                    {savingId === obligation.obligation_id ? "Saving..." : "Soft Delete"}
                  </span>
                </Button>
              </div>
            </Card>
          ))
        )}
      </div>
    </div>
  );
}

