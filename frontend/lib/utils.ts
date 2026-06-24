import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

const STATUS_LABELS: Record<string, string> = {
  not_started: "Not Started",
  in_progress: "In Progress",
  evidenced: "Evidenced",
  risk_accepted: "Risk Accepted",
  not_applicable: "N/A",
  evidence_submitted: "Evidence Submitted",
  satisfied: "Satisfied",
  open: "Open",
  resolved: "Resolved",
  verified: "Verified",
  closed: "Closed",
  current: "Current",
  due_soon: "Due Soon",
  overdue: "Overdue",
  waived: "Waived",
  minor_nc: "Minor NC",
  major_nc: "Major NC",
  observation: "Observation",
  critical: "Critical",
  exact_name_match: "Exact Match",
  last_name_only: "Last Name Match",
  manual_review: "Needs Review",
  high: "High",
  medium: "Medium",
  low: "Low",
  missing: "Missing",
  stale: "Stale",
  pending: "Pending",
  queued: "Queued",
  processing: "Processing",
  complete: "Complete",
  failed: "Failed",
}

export function formatLabel(value: string | null | undefined): string {
  if (!value) return "—"
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace("Nc", "NC")
    .replace("Mfa", "MFA")
    .replace("Nda", "NDA")
    .replace("Ict", "ICT")
    .replace("Pii", "PII")
    .replace("N A", "N/A")
}

export function formatStatus(value: string | null | undefined): string {
  if (!value) return "—"
  return STATUS_LABELS[value] ?? formatLabel(value)
}
