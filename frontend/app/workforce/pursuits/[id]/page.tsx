"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */

import Link from "next/link";
import { useEffect, useState } from "react";

import { ButtonSpinner, EmptyState, ErrorState, SkeletonCard } from "@/components/shared/LoadingStates";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  getWorkforcePursuit,
  patchWorkforcePursuit,
  runWorkforceGapAnalysis,
} from "@/lib/api";
import { useAsyncAction } from "@/lib/hooks";

const CLEARANCE_OPTIONS = ["none", "public_trust", "secret", "top_secret", "ts_sci"];

type Props = { params: Promise<{ id: string }> };

export default function PursuitDetailPage({ params }: Props) {
  const [id, setId] = useState<number | null>(null);
  const [pursuit, setPursuit] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [categoriesText, setCategoriesText] = useState("");
  const [clearance, setClearance] = useState("none");
  const [includeCanaide, setIncludeCanaide] = useState(false);
  const [gapResult, setGapResult] = useState<any | null>(null);
  const action = useAsyncAction();

  const load = async (pursuitId: number) => {
    setLoading(true);
    try {
      const data = await getWorkforcePursuit(pursuitId);
      setPursuit(data);
      setCategoriesText((data.required_labor_categories || []).join("\n"));
      setClearance(data.required_clearance_level || "none");
      setError(null);
    } catch {
      setPursuit(null);
      setError("Failed to load pursuit.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    params.then(({ id: raw }) => {
      const pursuitId = Number(raw);
      setId(pursuitId);
      void load(pursuitId);
    });
  }, [params]);

  if (loading) {
    return <SkeletonCard lines={4} />;
  }
  if (error || !pursuit) {
    return (
      <ErrorState
        title="Pursuit not found"
        description={error || "Could not load pursuit."}
        onRetry={() => id != null && void load(id)}
      />
    );
  }

  return (
    <div className="space-y-4">
      <Link href="/workforce" className="text-sm text-muted-foreground hover:underline">
        ← Workforce
      </Link>
      <Card className="space-y-2 p-4">
        <h3 className="text-lg font-semibold">{pursuit.title}</h3>
        <p className="text-sm text-muted-foreground">
          {pursuit.agency || "—"} · NAICS {pursuit.naics || "—"} · due {pursuit.response_due || "—"}
        </p>
      </Card>

      <Card className="space-y-3 p-4">
        <h4 className="font-medium">Requirements</h4>
        <label className="block space-y-1 text-sm">
          <span className="text-muted-foreground">Required labor categories (one per line)</span>
          <Textarea
            rows={5}
            value={categoriesText}
            onChange={(e) => setCategoriesText(e.target.value)}
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-muted-foreground">Required clearance</span>
          <select
            className="h-9 w-full max-w-xs rounded-md border bg-background px-2 text-sm"
            value={clearance}
            onChange={(e) => setClearance(e.target.value)}
          >
            {CLEARANCE_OPTIONS.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <Button
          size="sm"
          disabled={action.loading}
          onClick={async () => {
            if (id == null) return;
            const cats = categoriesText
              .split("\n")
              .map((line) => line.trim())
              .filter(Boolean);
            await action.execute(
              async () => {
                await patchWorkforcePursuit(id, {
                  required_labor_categories: cats,
                  required_clearance_level: clearance,
                });
                await load(id);
              },
              {
                loadingMessage: "Saving requirements...",
                successMessage: "Requirements saved.",
                errorMessage: "Failed to save requirements.",
              },
            );
          }}
        >
          {action.loading ? <ButtonSpinner /> : null}
          Save requirements
        </Button>
      </Card>

      <Card className="space-y-3 p-4">
        <h4 className="font-medium">Gap analysis</h4>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={includeCanaide}
            onChange={(e) => setIncludeCanaide(e.target.checked)}
          />
          Include CanAide
        </label>
        <Button
          size="sm"
          variant="secondary"
          disabled={action.loading || id == null}
          onClick={async () => {
            if (id == null) return;
            await action.execute(
              async () => {
                const result = await runWorkforceGapAnalysis(id, includeCanaide);
                setGapResult(result);
              },
              {
                loadingMessage: "Running gap analysis...",
                successMessage: "Gap analysis complete.",
                errorMessage: "Gap analysis failed.",
              },
            );
          }}
        >
          Run gap analysis
        </Button>
        {gapResult ? (
          <div className="space-y-1 text-sm">
            <p>
              {gapResult.gap_count} gaps · {(gapResult.filled || []).length} filled · staff considered{" "}
              {gapResult.staff_considered_count}
            </p>
            {(gapResult.gaps || []).length === 0 ? (
              <EmptyState title="No open gaps" description="All required categories have candidates." />
            ) : (
              (gapResult.gaps as any[]).map((g) => (
                <p key={g.labor_category} className="text-muted-foreground">
                  Open: {g.labor_category} (clearance {g.clearance_required || "none"}, candidates{" "}
                  {g.candidate_count})
                </p>
              ))
            )}
          </div>
        ) : null}
      </Card>
    </div>
  );
}
