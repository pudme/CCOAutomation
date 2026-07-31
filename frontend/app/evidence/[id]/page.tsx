"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */

import Link from "next/link";
import { useEffect, useState } from "react";

import { ButtonSpinner, EmptyState, ErrorState, SkeletonCard } from "@/components/shared/LoadingStates";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { getEvidence, getEvidenceCorrections, patchEvidenceControl } from "@/lib/api";
import { useAsyncAction } from "@/lib/hooks";

type Props = { params: Promise<{ id: string }> };

export default function EvidenceDetailPage({ params }: Props) {
  const [id, setId] = useState<number | null>(null);
  const [item, setItem] = useState<any | null>(null);
  const [corrections, setCorrections] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [renameDrafts, setRenameDrafts] = useState<Record<string, string>>({});
  const action = useAsyncAction();

  const load = async (evidenceId: number) => {
    setLoading(true);
    try {
      const [ev, corr] = await Promise.all([
        getEvidence(evidenceId),
        getEvidenceCorrections(evidenceId),
      ]);
      setItem(ev);
      setCorrections(corr.items || []);
      setError(null);
    } catch {
      setItem(null);
      setCorrections([]);
      setError("Failed to load evidence.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    params.then(({ id: raw }) => {
      const evidenceId = Number(raw);
      setId(evidenceId);
      void load(evidenceId);
    });
  }, [params]);

  if (loading) {
    return <SkeletonCard lines={5} />;
  }
  if (error || !item) {
    return (
      <ErrorState
        title="Evidence not found"
        description={error || "Could not load evidence."}
        onRetry={() => id != null && void load(id)}
      />
    );
  }

  return (
    <div className="space-y-4">
      <Link href="/evidence" className="text-sm text-muted-foreground hover:underline">
        ← Evidence
      </Link>

      <Card className="space-y-2 p-4">
        <h3 className="text-lg font-semibold">{item.display_name || item.filename}</h3>
        <p className="text-sm text-muted-foreground">
          #{item.id} · file {item.filename} · type {item.evidence_type || "—"} · status {item.status || "—"}
        </p>
        {item.analysis_summary ? <p className="text-sm">{item.analysis_summary}</p> : null}
      </Card>

      <Card className="space-y-3 p-4">
        <h4 className="font-medium">Control links</h4>
        {(item.controls || []).length === 0 ? (
          <EmptyState title="No controls linked" description="Unmatched evidence has no control links yet." />
        ) : (
          (item.controls as any[]).map((link) => {
            const key = link.control_id;
            const draft = renameDrafts[key] ?? link.display_name ?? "";
            return (
              <div key={key} className="flex flex-wrap items-center gap-2 rounded border p-2">
                <div className="min-w-[8rem]">
                  <p className="text-sm font-medium">{link.control_id}</p>
                  <p className="text-xs text-muted-foreground">{link.framework} · {link.title}</p>
                </div>
                <Input
                  className="max-w-md flex-1"
                  value={draft}
                  onChange={(e) => setRenameDrafts((prev) => ({ ...prev, [key]: e.target.value }))}
                />
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={action.loading || draft === (link.display_name || "")}
                  onClick={async () => {
                    await action.execute(
                      async () => {
                        await patchEvidenceControl(item.id, link.control_id, { display_name: draft });
                        if (id != null) await load(id);
                      },
                      {
                        loadingMessage: "Renaming...",
                        successMessage: "Display name updated.",
                        errorMessage: "Rename failed.",
                      },
                    );
                  }}
                >
                  {action.loading ? <ButtonSpinner /> : null}
                  Rename
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={action.loading}
                  onClick={async () => {
                    await action.execute(
                      async () => {
                        await patchEvidenceControl(item.id, link.control_id, { remove: true });
                        if (id != null) await load(id);
                      },
                      {
                        loadingMessage: "Unlinking...",
                        successMessage: "Control unlinked.",
                        errorMessage: "Unlink failed.",
                      },
                    );
                  }}
                >
                  Unlink
                </Button>
              </div>
            );
          })
        )}
      </Card>

      <Card className="space-y-3 p-4">
        <h4 className="font-medium">Correction history</h4>
        {corrections.length === 0 ? (
          <p className="text-sm text-muted-foreground">No corrections recorded for this evidence item.</p>
        ) : (
          <div className="space-y-2">
            {corrections.map((c) => (
              <div key={c.id} className="rounded border p-2 text-sm">
                <p className="font-medium">
                  {c.field_name} · {c.source}
                </p>
                <p className="text-xs text-muted-foreground">
                  {c.timestamp} · {c.operator}
                  {c.detail ? ` · ${c.detail}` : ""}
                </p>
                <p className="mt-1 text-xs">
                  <span className="text-muted-foreground">before:</span> {c.before_value ?? "—"}
                </p>
                <p className="text-xs">
                  <span className="text-muted-foreground">after:</span> {c.after_value ?? "—"}
                </p>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
