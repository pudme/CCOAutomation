import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

type ToolCallDisplayProps = {
  toolName: string;
  status: "started" | "completed";
  summary?: string;
};

const labelMap: Record<string, string> = {
  search_documents: "🔍 Searching documents...",
  get_framework_detail: "📊 Pulling framework detail...",
  get_import_history: "📥 Reviewing import history...",
  run_personnel_check: "👥 Running personnel compliance checks...",
  update_control_status: "✏️ Updating control status...",
  add_evidence: "📄 Adding evidence record...",
  get_open_findings: "🚩 Pulling open findings...",
  list_obligations: "📆 Loading obligations...",
  create_obligation: "📝 Creating obligation...",
  update_obligation: "📝 Updating obligation...",
  generate_gap_report: "📈 Generating gap report...",
  generate_scorecard: "📉 Generating scorecard...",
  generate_audit_package: "🗂️ Building audit package index...",
  generate_corrective_action_report: "🛠️ Building corrective action report...",
};

export function ToolCallDisplay({ toolName, status, summary }: ToolCallDisplayProps) {
  const label = labelMap[toolName] ?? `Running ${toolName}...`;
  return (
    <Card className="my-2 p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span>{label}</span>
        <Badge variant={status === "started" ? "secondary" : "default"}>{status}</Badge>
      </div>
      {status === "completed" && summary ? (
        <p className="mt-2 text-xs text-muted-foreground">{summary}</p>
      ) : null}
    </Card>
  );
}

