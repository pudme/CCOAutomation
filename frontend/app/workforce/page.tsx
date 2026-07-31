"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
/* eslint-disable react-hooks/set-state-in-effect */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ButtonSpinner, EmptyState, ErrorState, SkeletonCard } from "@/components/shared/LoadingStates";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  createWorkforceAssignment,
  createWorkforceGap,
  createWorkforcePursuit,
  createWorkforceStaff,
  deleteWorkforceAssignment,
  deleteWorkforceGap,
  deleteWorkforcePursuit,
  deleteWorkforceStaff,
  getWorkforceAssignments,
  getWorkforceGaps,
  getWorkforceOvercommitment,
  getWorkforcePursuits,
  getWorkforceStaff,
  patchWorkforceAssignment,
  patchWorkforceGap,
  patchWorkforceStaff,
  runWorkforceGapAnalysis,
} from "@/lib/api";
import { useAsyncAction } from "@/lib/hooks";

const CLEARANCE_OPTIONS = ["none", "public_trust", "secret", "top_secret", "ts_sci"];
const ASSIGNMENT_STATUSES = ["proposed", "committed", "won", "released"];
const GAP_STATUSES = ["open", "filled", "at_risk"];

export default function WorkforcePage() {
  const [includeCanaide, setIncludeCanaide] = useState(false);
  const [staff, setStaff] = useState<any[]>([]);
  const [pursuits, setPursuits] = useState<any[]>([]);
  const [assignments, setAssignments] = useState<any[]>([]);
  const [gaps, setGaps] = useState<any[]>([]);
  const [overcommitment, setOvercommitment] = useState<any | null>(null);
  const [gapAnalysis, setGapAnalysis] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const action = useAsyncAction();

  const [staffForm, setStaffForm] = useState({
    display_name: "",
    entity: "Apprio",
    labor_category: "",
    clearance_level: "none",
  });
  const [pursuitForm, setPursuitForm] = useState({ title: "", agency: "", required_clearance_level: "none" });
  const [assignmentForm, setAssignmentForm] = useState({
    staff_id: "",
    pursuit_id: "",
    role: "",
    commitment_pct: "50",
    status: "proposed",
  });
  const [gapForm, setGapForm] = useState({
    pursuit_id: "",
    labor_category: "",
    clearance_required: "none",
    status: "open",
  });
  const [analysisPursuitId, setAnalysisPursuitId] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, p, a, g, o] = await Promise.all([
        getWorkforceStaff(),
        getWorkforcePursuits(),
        getWorkforceAssignments(),
        getWorkforceGaps(),
        getWorkforceOvercommitment(includeCanaide),
      ]);
      setStaff(s);
      setPursuits(p);
      setAssignments(a);
      setGaps(g);
      setOvercommitment(o);
    } catch {
      setError("Failed to load workforce data.");
    } finally {
      setLoading(false);
    }
  }, [includeCanaide]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const staffName = (id: number) => staff.find((s) => s.id === id)?.display_name ?? `#${id}`;
  const pursuitTitle = (id: number) => pursuits.find((p) => p.id === id)?.title ?? `#${id}`;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Staffing, pursuits, and commitment alignment. Entity filter defaults to Apprio-only.
        </p>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={includeCanaide}
            onChange={(e) => setIncludeCanaide(e.target.checked)}
          />
          Include CanAide
        </label>
      </div>

      {loading ? (
        <SkeletonCard lines={4} />
      ) : error ? (
        <ErrorState title="Failed to load workforce" description={error} onRetry={() => void refresh()} />
      ) : (
        <Tabs defaultValue="staff">
          <TabsList>
            <TabsTrigger value="staff">Staff</TabsTrigger>
            <TabsTrigger value="pursuits">Pursuits</TabsTrigger>
            <TabsTrigger value="assignments">Assignments</TabsTrigger>
            <TabsTrigger value="gaps">Gaps</TabsTrigger>
          </TabsList>

          <TabsContent value="staff" className="space-y-3 pt-3">
            <Card className="space-y-2 p-4">
              <p className="text-sm font-medium">Add staff</p>
              <div className="grid gap-2 md:grid-cols-4">
                <Input
                  placeholder="Display name"
                  value={staffForm.display_name}
                  onChange={(e) => setStaffForm({ ...staffForm, display_name: e.target.value })}
                />
                <Input
                  placeholder="Labor category"
                  value={staffForm.labor_category}
                  onChange={(e) => setStaffForm({ ...staffForm, labor_category: e.target.value })}
                />
                <Input
                  placeholder="Entity"
                  value={staffForm.entity}
                  onChange={(e) => setStaffForm({ ...staffForm, entity: e.target.value })}
                />
                <select
                  className="h-9 rounded-md border bg-background px-2 text-sm"
                  value={staffForm.clearance_level}
                  onChange={(e) => setStaffForm({ ...staffForm, clearance_level: e.target.value })}
                >
                  {CLEARANCE_OPTIONS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </div>
              <Button
                size="sm"
                disabled={action.loading || !staffForm.display_name.trim()}
                onClick={async () => {
                  await action.execute(
                    async () => {
                      await createWorkforceStaff(staffForm);
                      setStaffForm({ display_name: "", entity: "Apprio", labor_category: "", clearance_level: "none" });
                      await refresh();
                    },
                    {
                      loadingMessage: "Creating staff...",
                      successMessage: "Staff created.",
                      errorMessage: "Failed to create staff.",
                    },
                  );
                }}
              >
                {action.loading ? <ButtonSpinner /> : null}
                Add Staff
              </Button>
            </Card>
            {staff.length === 0 ? (
              <EmptyState title="No staff" description="Add staff to track clearance and labor categories." />
            ) : (
              staff.map((s) => (
                <Card key={s.id} className="flex flex-wrap items-center justify-between gap-2 p-4">
                  <div>
                    <p className="font-medium">{s.display_name}</p>
                    <p className="text-xs text-muted-foreground">
                      {s.entity || "—"} · {s.labor_category || "—"} · {s.clearance_level} · util {s.utilization_pct}%
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={async () => {
                        const next = window.prompt("Utilization %", String(s.utilization_pct ?? 0));
                        if (next === null) return;
                        await action.execute(
                          async () => {
                            await patchWorkforceStaff(s.id, { utilization_pct: Number(next) });
                            await refresh();
                          },
                          {
                            loadingMessage: "Updating...",
                            successMessage: "Staff updated.",
                            errorMessage: "Failed to update staff.",
                          },
                        );
                      }}
                    >
                      Edit util
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={async () => {
                        await action.execute(
                          async () => {
                            await deleteWorkforceStaff(s.id);
                            await refresh();
                          },
                          {
                            loadingMessage: "Deleting...",
                            successMessage: "Staff deleted.",
                            errorMessage: "Failed to delete staff.",
                          },
                        );
                      }}
                    >
                      Delete
                    </Button>
                  </div>
                </Card>
              ))
            )}
          </TabsContent>

          <TabsContent value="pursuits" className="space-y-3 pt-3">
            <Card className="space-y-2 p-4">
              <p className="text-sm font-medium">Add pursuit</p>
              <div className="grid gap-2 md:grid-cols-3">
                <Input
                  placeholder="Title"
                  value={pursuitForm.title}
                  onChange={(e) => setPursuitForm({ ...pursuitForm, title: e.target.value })}
                />
                <Input
                  placeholder="Agency"
                  value={pursuitForm.agency}
                  onChange={(e) => setPursuitForm({ ...pursuitForm, agency: e.target.value })}
                />
                <select
                  className="h-9 rounded-md border bg-background px-2 text-sm"
                  value={pursuitForm.required_clearance_level}
                  onChange={(e) => setPursuitForm({ ...pursuitForm, required_clearance_level: e.target.value })}
                >
                  {CLEARANCE_OPTIONS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </div>
              <Button
                size="sm"
                disabled={action.loading || !pursuitForm.title.trim()}
                onClick={async () => {
                  await action.execute(
                    async () => {
                      await createWorkforcePursuit(pursuitForm);
                      setPursuitForm({ title: "", agency: "", required_clearance_level: "none" });
                      await refresh();
                    },
                    {
                      loadingMessage: "Creating pursuit...",
                      successMessage: "Pursuit created.",
                      errorMessage: "Failed to create pursuit.",
                    },
                  );
                }}
              >
                Add Pursuit
              </Button>
            </Card>
            <Card className="space-y-2 p-4">
              <p className="text-sm font-medium">Run gap analysis</p>
              <div className="flex flex-wrap gap-2">
                <select
                  className="h-9 min-w-[12rem] rounded-md border bg-background px-2 text-sm"
                  value={analysisPursuitId}
                  onChange={(e) => setAnalysisPursuitId(e.target.value)}
                >
                  <option value="">Select pursuit</option>
                  {pursuits.map((p) => (
                    <option key={p.id} value={String(p.id)}>
                      {p.title}
                    </option>
                  ))}
                </select>
                <Button
                  size="sm"
                  disabled={!analysisPursuitId || action.loading}
                  onClick={async () => {
                    await action.execute(
                      async () => {
                        const result = await runWorkforceGapAnalysis(Number(analysisPursuitId), includeCanaide);
                        setGapAnalysis(result);
                      },
                      {
                        loadingMessage: "Analyzing gaps...",
                        successMessage: "Gap analysis complete.",
                        errorMessage: "Gap analysis failed.",
                      },
                    );
                  }}
                >
                  Analyze
                </Button>
              </div>
              {gapAnalysis ? (
                <p className="text-sm text-muted-foreground">
                  {gapAnalysis.pursuit_title}: {gapAnalysis.gap_count} gaps · {gapAnalysis.filled?.length ?? 0} filled
                  · staff considered {gapAnalysis.staff_considered_count}
                  {gapAnalysis.gaps?.length ? (
                    <span className="mt-1 block">
                      Open: {gapAnalysis.gaps.map((g: any) => g.labor_category).join(", ")}
                    </span>
                  ) : null}
                </p>
              ) : null}
            </Card>
            {pursuits.length === 0 ? (
              <EmptyState title="No pursuits" description="Create a pursuit to plan staffing." />
            ) : (
              pursuits.map((p) => (
                <Card key={p.id} className="flex flex-wrap items-center justify-between gap-2 p-4">
                  <div>
                    <Link href={`/workforce/pursuits/${p.id}`} className="font-medium hover:underline">
                      {p.title}
                    </Link>
                    <p className="text-xs text-muted-foreground">
                      {p.agency || "—"} · clearance {p.required_clearance_level || "none"} · cats{" "}
                      {(p.required_labor_categories || []).join(", ") || "—"}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={async () => {
                      await action.execute(
                        async () => {
                          await deleteWorkforcePursuit(p.id);
                          await refresh();
                        },
                        {
                          loadingMessage: "Deleting...",
                          successMessage: "Pursuit deleted.",
                          errorMessage: "Failed to delete pursuit.",
                        },
                      );
                    }}
                  >
                    Delete
                  </Button>
                </Card>
              ))
            )}
          </TabsContent>

          <TabsContent value="assignments" className="space-y-3 pt-3">
            <Card className="space-y-2 p-4">
              <p className="text-sm font-medium">Add assignment</p>
              <div className="grid gap-2 md:grid-cols-5">
                <select
                  className="h-9 rounded-md border bg-background px-2 text-sm"
                  value={assignmentForm.staff_id}
                  onChange={(e) => setAssignmentForm({ ...assignmentForm, staff_id: e.target.value })}
                >
                  <option value="">Staff</option>
                  {staff.map((s) => (
                    <option key={s.id} value={String(s.id)}>
                      {s.display_name}
                    </option>
                  ))}
                </select>
                <select
                  className="h-9 rounded-md border bg-background px-2 text-sm"
                  value={assignmentForm.pursuit_id}
                  onChange={(e) => setAssignmentForm({ ...assignmentForm, pursuit_id: e.target.value })}
                >
                  <option value="">Pursuit</option>
                  {pursuits.map((p) => (
                    <option key={p.id} value={String(p.id)}>
                      {p.title}
                    </option>
                  ))}
                </select>
                <Input
                  placeholder="Role"
                  value={assignmentForm.role}
                  onChange={(e) => setAssignmentForm({ ...assignmentForm, role: e.target.value })}
                />
                <Input
                  placeholder="Commitment %"
                  value={assignmentForm.commitment_pct}
                  onChange={(e) => setAssignmentForm({ ...assignmentForm, commitment_pct: e.target.value })}
                />
                <select
                  className="h-9 rounded-md border bg-background px-2 text-sm"
                  value={assignmentForm.status}
                  onChange={(e) => setAssignmentForm({ ...assignmentForm, status: e.target.value })}
                >
                  {ASSIGNMENT_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
              <Button
                size="sm"
                disabled={!assignmentForm.staff_id || !assignmentForm.pursuit_id || action.loading}
                onClick={async () => {
                  await action.execute(
                    async () => {
                      await createWorkforceAssignment({
                        staff_id: Number(assignmentForm.staff_id),
                        pursuit_id: Number(assignmentForm.pursuit_id),
                        role: assignmentForm.role || null,
                        commitment_pct: Number(assignmentForm.commitment_pct),
                        status: assignmentForm.status,
                      });
                      setAssignmentForm({
                        staff_id: "",
                        pursuit_id: "",
                        role: "",
                        commitment_pct: "50",
                        status: "proposed",
                      });
                      await refresh();
                    },
                    {
                      loadingMessage: "Creating assignment...",
                      successMessage: "Assignment created.",
                      errorMessage: "Failed to create assignment.",
                    },
                  );
                }}
              >
                Add Assignment
              </Button>
            </Card>
            {overcommitment?.overcommitted_count ? (
              <Card className="border-amber-300 bg-amber-50 p-4 text-sm text-amber-950">
                Overcommitted ({overcommitment.overcommitted_count}):{" "}
                {overcommitment.overcommitted
                  .map((o: any) => `${o.display_name} (${o.total_commitment_pct}%)`)
                  .join(", ")}
              </Card>
            ) : (
              <p className="text-xs text-muted-foreground">No overcommitment detected (Apprio-only unless CanAide included).</p>
            )}
            {assignments.length === 0 ? (
              <EmptyState title="No assignments" description="Assign staff to pursuits." />
            ) : (
              assignments.map((a) => (
                <Card key={a.id} className="flex flex-wrap items-center justify-between gap-2 p-4">
                  <div>
                    <p className="font-medium">
                      {staffName(a.staff_id)} → {pursuitTitle(a.pursuit_id)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {a.role || "—"} · {a.commitment_pct}% · {a.status}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={async () => {
                        const next = ASSIGNMENT_STATUSES[(ASSIGNMENT_STATUSES.indexOf(a.status) + 1) % ASSIGNMENT_STATUSES.length];
                        await action.execute(
                          async () => {
                            await patchWorkforceAssignment(a.id, { status: next });
                            await refresh();
                          },
                          {
                            loadingMessage: "Updating...",
                            successMessage: "Assignment updated.",
                            errorMessage: "Failed to update assignment.",
                          },
                        );
                      }}
                    >
                      Cycle status
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={async () => {
                        await action.execute(
                          async () => {
                            await deleteWorkforceAssignment(a.id);
                            await refresh();
                          },
                          {
                            loadingMessage: "Deleting...",
                            successMessage: "Assignment deleted.",
                            errorMessage: "Failed to delete assignment.",
                          },
                        );
                      }}
                    >
                      Delete
                    </Button>
                  </div>
                </Card>
              ))
            )}
          </TabsContent>

          <TabsContent value="gaps" className="space-y-3 pt-3">
            <Card className="space-y-2 p-4">
              <p className="text-sm font-medium">Flag gap</p>
              <div className="grid gap-2 md:grid-cols-4">
                <select
                  className="h-9 rounded-md border bg-background px-2 text-sm"
                  value={gapForm.pursuit_id}
                  onChange={(e) => setGapForm({ ...gapForm, pursuit_id: e.target.value })}
                >
                  <option value="">Pursuit</option>
                  {pursuits.map((p) => (
                    <option key={p.id} value={String(p.id)}>
                      {p.title}
                    </option>
                  ))}
                </select>
                <Input
                  placeholder="Labor category"
                  value={gapForm.labor_category}
                  onChange={(e) => setGapForm({ ...gapForm, labor_category: e.target.value })}
                />
                <select
                  className="h-9 rounded-md border bg-background px-2 text-sm"
                  value={gapForm.clearance_required}
                  onChange={(e) => setGapForm({ ...gapForm, clearance_required: e.target.value })}
                >
                  {CLEARANCE_OPTIONS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
                <select
                  className="h-9 rounded-md border bg-background px-2 text-sm"
                  value={gapForm.status}
                  onChange={(e) => setGapForm({ ...gapForm, status: e.target.value })}
                >
                  {GAP_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
              <Button
                size="sm"
                disabled={!gapForm.pursuit_id || !gapForm.labor_category.trim() || action.loading}
                onClick={async () => {
                  await action.execute(
                    async () => {
                      await createWorkforceGap({
                        pursuit_id: Number(gapForm.pursuit_id),
                        labor_category: gapForm.labor_category,
                        clearance_required: gapForm.clearance_required,
                        status: gapForm.status,
                      });
                      setGapForm({
                        pursuit_id: "",
                        labor_category: "",
                        clearance_required: "none",
                        status: "open",
                      });
                      await refresh();
                    },
                    {
                      loadingMessage: "Creating gap...",
                      successMessage: "Gap flagged.",
                      errorMessage: "Failed to flag gap.",
                    },
                  );
                }}
              >
                Flag Gap
              </Button>
            </Card>
            {gaps.length === 0 ? (
              <EmptyState title="No gaps" description="Gaps appear here when flagged or created." />
            ) : (
              gaps.map((g) => (
                <Card key={g.id} className="flex flex-wrap items-center justify-between gap-2 p-4">
                  <div>
                    <p className="font-medium">
                      {g.labor_category} · {pursuitTitle(g.pursuit_id)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      clearance {g.clearance_required || "none"} · {g.status}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={async () => {
                        const next = g.status === "open" ? "filled" : "open";
                        await action.execute(
                          async () => {
                            await patchWorkforceGap(g.id, { status: next });
                            await refresh();
                          },
                          {
                            loadingMessage: "Updating...",
                            successMessage: "Gap updated.",
                            errorMessage: "Failed to update gap.",
                          },
                        );
                      }}
                    >
                      Toggle filled
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={async () => {
                        await action.execute(
                          async () => {
                            await deleteWorkforceGap(g.id);
                            await refresh();
                          },
                          {
                            loadingMessage: "Deleting...",
                            successMessage: "Gap deleted.",
                            errorMessage: "Failed to delete gap.",
                          },
                        );
                      }}
                    >
                      Delete
                    </Button>
                  </div>
                </Card>
              ))
            )}
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
