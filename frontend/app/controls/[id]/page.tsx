"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */

import { useEffect, useState } from "react";

import { ButtonSpinner, EmptyState, ErrorState, SkeletonCard } from "@/components/shared/LoadingStates";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { getControl, patchControl } from "@/lib/api";
import { useAsyncAction } from "@/lib/hooks";
import { formatStatus } from "@/lib/utils";

export default function ControlDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const [id, setId] = useState("");
  const [control, setControl] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const action = useAsyncAction();

  const refresh = async (controlId: string) => {
    try {
      const data = await getControl(controlId);
      setControl(data);
      setError(null);
    } catch {
      setControl(null);
      setError("Failed to load control detail.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    params.then(({ id: controlId }) => {
      setId(controlId);
      refresh(controlId).catch(() => setControl(null));
    });
  }, [params]);

  if (loading) {
    return (
      <div className="space-y-3">
        <SkeletonCard lines={3} />
      </div>
    );
  }

  if (error) {
    return <ErrorState title="Failed to load control detail" description={error} onRetry={() => void refresh(id)} />;
  }

  if (!control) {
    return <EmptyState title="Control not found" description="This control could not be loaded." />;
  }

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <h2 className="text-lg font-semibold">
          {control.control_id} - {control.title}
        </h2>
        <p className="text-sm text-muted-foreground">{control.description}</p>
        <p className="mt-2 text-xs">Domain: {control.domain}</p>
        <p className="text-xs">Status: {formatStatus(control.status)}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            size="sm"
            disabled={action.loading}
            onClick={async () => {
              await action.execute(
                async () => {
                  await patchControl(id, { status: "in_progress", notes: "Updated from control detail page" });
                  await refresh(id);
                },
                { loadingMessage: "Saving control status...", successMessage: "Control updated.", errorMessage: "Failed to update control." },
              );
            }}
          >
            <span className="flex items-center gap-2">
              {action.loading ? <ButtonSpinner /> : null}
              {action.loading ? "Saving..." : "Mark In Progress"}
            </span>
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={action.loading}
            onClick={async () => {
              await action.execute(
                async () => {
                  await patchControl(id, { status: "evidenced", notes: "Evidence validated" });
                  await refresh(id);
                },
                { loadingMessage: "Saving control status...", successMessage: "Control updated.", errorMessage: "Failed to update control." },
              );
            }}
          >
            <span className="flex items-center gap-2">
              {action.loading ? <ButtonSpinner /> : null}
              {action.loading ? "Saving..." : "Mark Evidenced"}
            </span>
          </Button>
        </div>
      </Card>

      <Card className="p-4">
        <h3 className="font-semibold">Evidence Items</h3>
        <div className="mt-2 space-y-2">
          {control.evidence_items.map((e: any) => (
            <div key={e.id} className="rounded border p-2 text-sm">
              <p>{e.filename}</p>
              <p className="text-xs text-muted-foreground">
                {formatStatus(e.type)} | {formatStatus(e.status)} | {e.date || "N/A"} | {e.entity || "N/A"}
              </p>
            </div>
          ))}
        </div>
      </Card>

      <Card className="p-4">
        <h3 className="font-semibold">Cross-Mapped Controls</h3>
        <p className="mt-2 text-sm">{control.cross_mapped_controls.join(", ") || "None"}</p>
      </Card>
    </div>
  );
}

