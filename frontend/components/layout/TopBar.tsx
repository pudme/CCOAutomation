"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useApiUsage, useAuditInfo, useImportProgressBanner } from "@/lib/hooks";
import { useAnalysisProgressStore } from "@/lib/stores/analysis-progress";

function getTitle(pathname: string): string {
  if (pathname.startsWith("/chat")) return "Chat";
  if (pathname.startsWith("/frameworks")) return "Frameworks";
  if (pathname.startsWith("/documents")) return "Documents";
  if (pathname.startsWith("/findings")) return "Findings";
  if (pathname.startsWith("/evidence")) return "Evidence";
  if (pathname.startsWith("/obligations")) return "Obligations";
  if (pathname.startsWith("/personnel")) return "Personnel";
  if (pathname.startsWith("/workforce")) return "Workforce";
  if (pathname.startsWith("/dpa/documents")) return "DPA Documents";
  if (pathname.startsWith("/dpa/auditor")) return "DPA Auditor";
  if (pathname.startsWith("/dpa")) return "DPA Overview";
  return "Dashboard";
}

export function TopBar() {
  const pathname = usePathname();
  const { auditInfo } = useAuditInfo();
  const { usage } = useApiUsage();
  const { importProgress } = useImportProgressBanner();
  const analysisIsAnalyzing = useAnalysisProgressStore((state) => state.isAnalyzing);
  const analysisCompleted = useAnalysisProgressStore((state) => state.completed);
  const analysisTotal = useAnalysisProgressStore((state) => state.total);
  return (
    <header className="min-w-0 border-b bg-background px-6 py-4">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-xl font-semibold">{getTitle(pathname)}</h2>
        <div className="shrink-0 whitespace-nowrap rounded border bg-muted/40 px-3 py-1 text-sm text-muted-foreground">
          {auditInfo
            ? `ISO Audit: ${Math.max(auditInfo.iso.days_remaining, 0)} days · CMMC: ${Math.max(auditInfo.cmmc.days_remaining, 0)} days · DPA Review: ${auditInfo.dpa.days_remaining === null ? "--" : Math.max(auditInfo.dpa.days_remaining, 0)} days${auditInfo.ato.days_remaining === null ? "" : ` · ATO: ${Math.max(auditInfo.ato.days_remaining, 0)} days`}`
            : "ISO Audit: -- · CMMC: -- · DPA Review: --"}
        </div>
      </div>
      {importProgress?.running && importProgress.total > 0 ? (
        <Link
          href="/import"
          className="mt-3 block rounded border border-blue-200 bg-blue-50 px-3 py-2 text-sm font-medium text-blue-900 hover:bg-blue-100"
        >
          Import in progress - {importProgress.complete} / {importProgress.total} files complete
        </Link>
      ) : null}
      {analysisIsAnalyzing && analysisTotal > 0 ? (
        <Link
          href="/documents"
          className="mt-3 block rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm font-medium text-amber-900 hover:bg-amber-100"
        >
          Analysis in progress - {analysisCompleted} / {analysisTotal} documents complete
        </Link>
      ) : null}
      {usage && !usage.enabled ? (
        <div className="mt-3 rounded border border-red-300 bg-red-50 px-3 py-2 text-sm font-medium text-red-900">
          ⚠ AI features are disabled. Chat, import classification, and evidence analysis are paused. Enable in
          Settings.
        </div>
      ) : null}
    </header>
  );
}

