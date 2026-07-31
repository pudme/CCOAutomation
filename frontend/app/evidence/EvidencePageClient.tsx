"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
/* eslint-disable react-hooks/set-state-in-effect */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { ButtonSpinner, EmptyState, ErrorState, SkeletonCard } from "@/components/shared/LoadingStates";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  getEvidenceList,
  getFrameworks,
  patchEvidenceControl,
} from "@/lib/api";
import { useAsyncAction } from "@/lib/hooks";

export default function EvidencePageClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const framework = searchParams.get("framework") || "";
  const [items, setItems] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [frameworks, setFrameworks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [renameDrafts, setRenameDrafts] = useState<Record<string, string>>({});
  const action = useAsyncAction();

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, fw] = await Promise.all([
        getEvidenceList({ framework: framework || undefined, page_size: 50 }),
        getFrameworks(),
      ]);
      setItems(list.items || []);
      setTotal(list.total || 0);
      setFrameworks(fw || []);
    } catch {
      setError("Failed to load evidence.");
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [framework]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const setFrameworkFilter = (value: string) => {
    const params = new URLSearchParams();
    if (value) params.set("framework", value);
    router.push(params.toString() ? `/evidence?${params}` : "/evidence");
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm text-muted-foreground">Framework</label>
        <select
          className="h-9 rounded-md border bg-background px-2 text-sm"
          value={framework}
          onChange={(e) => setFrameworkFilter(e.target.value)}
        >
          <option value="">All</option>
          {frameworks.map((f) => (
            <option key={f.id ?? f.short_name} value={f.short_name}>
              {f.short_name} — {f.name}
            </option>
          ))}
        </select>
        <span className="text-xs text-muted-foreground">{total} items</span>
      </div>

      {loading ? (
        <>
          <SkeletonCard lines={3} />
          <SkeletonCard lines={3} />
        </>
      ) : error ? (
        <ErrorState title="Failed to load evidence" description={error} onRetry={() => void refresh()} />
      ) : items.length === 0 ? (
        <EmptyState title="No evidence" description="Import or drop files to populate evidence." />
      ) : (
        items.map((item) => (
          <Card key={item.id} className="space-y-3 p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <Link href={`/evidence/${item.id}`} className="font-medium hover:underline">
                  {item.display_name || item.filename}
                </Link>
                <p className="text-xs text-muted-foreground">
                  #{item.id} · {item.filename} · {item.evidence_type || "untyped"} · {item.status || "—"}
                </p>
              </div>
              <Badge variant="secondary">{(item.controls || []).length} controls</Badge>
            </div>
            <div className="space-y-2">
              {(item.controls || []).length === 0 ? (
                <p className="text-xs text-muted-foreground">No control links</p>
              ) : (
                (item.controls as any[]).map((link) => {
                  const key = `${item.id}:${link.control_id}`;
                  const draft = renameDrafts[key] ?? link.display_name ?? "";
                  return (
                    <div key={key} className="flex flex-wrap items-center gap-2 rounded border p-2">
                      <div className="min-w-[8rem]">
                        <p className="text-sm font-medium">{link.control_id}</p>
                        <p className="text-xs text-muted-foreground">{link.framework}</p>
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
                              await refresh();
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
                              await refresh();
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
            </div>
          </Card>
        ))
      )}
    </div>
  );
}
