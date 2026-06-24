/* eslint-disable @typescript-eslint/no-explicit-any */
import Link from "next/link";

import { Card } from "@/components/ui/card";
import { getAuditorChecklist, getAuditorChecklistSummary } from "@/lib/api";
import { formatStatus } from "@/lib/utils";

export default async function AuditorChecklistDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const checklistId = Number(id);
  const [checklist, summary] = await Promise.all([
    getAuditorChecklist(checklistId),
    getAuditorChecklistSummary(checklistId),
  ]);
  return (
    <div className="space-y-4">
      <Link href="/auditor" className="text-sm text-blue-600 underline">
        Back to Auditor Workspace
      </Link>
      <Card className="p-4">
        <h2 className="text-lg font-semibold">{checklist.name}</h2>
        <p className="text-sm text-muted-foreground">
          Auditor: {checklist.auditor_name || "N/A"} | Date: {checklist.audit_date || "N/A"} | Framework:{" "}
          {checklist.framework || "mixed"}
        </p>
        <p className="mt-2 text-sm">Satisfied: {summary.percent_satisfied}%</p>
      </Card>
      <Card className="p-4">
        <h3 className="font-semibold">Items</h3>
        <div className="mt-2 space-y-2">
          {checklist.items.map((item: any) => (
            <div key={item.id} className="rounded border p-2 text-sm">
              <p className="font-medium">
                {item.item_number} - {formatStatus(item.status)}
              </p>
              <p>{item.description}</p>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
