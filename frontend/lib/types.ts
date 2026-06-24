export type FrameworkReadiness = {
  framework: string;
  name: string;
  total_controls: number;
  evidenced_controls: number;
  percent_evidenced: number;
  mode: "auditor" | "framework";
  checklist_name: string | null;
  checklist_id: number | null;
  percentage: number;
  satisfied: number;
  total: number;
  progress_label: string;
};

export type DashboardSummary = {
  framework_readiness: FrameworkReadiness[];
  open_findings_by_severity: Record<string, number>;
  obligations_due_30_days: Array<{
    obligation_id: string;
    source: string;
    due_date: string;
    status: string;
  }>;
  recent_agent_actions: Array<{
    timestamp: string;
    tool_name: string;
    result_summary: string;
    operator: string;
  }>;
  personnel_exceptions: {
    training_gaps: number;
    mfa_gaps: number;
    nda_gaps: number;
    terminated_access_gaps: number;
  };
};

