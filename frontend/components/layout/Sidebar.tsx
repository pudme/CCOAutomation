"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Briefcase, CalendarClock, ClipboardList, FileSearch, FileText, Folder, LayoutDashboard, MessageSquare, Settings, Shield, Users } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useAuditInfo } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/frameworks", label: "Frameworks", icon: Shield },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/history", label: "Revision History", icon: FileText },
  { href: "/findings", label: "Findings", icon: FileText },
  { href: "/evidence", label: "Evidence", icon: FileSearch },
  { href: "/auditor", label: "Auditor", icon: ClipboardList },
  { href: "/obligations", label: "Obligations", icon: CalendarClock },
  { href: "/personnel", label: "Personnel", icon: Users },
  { href: "/workforce", label: "Workforce", icon: Briefcase },
  { href: "/chat", label: "Chat", icon: MessageSquare },
];
const DPA_NAV_ITEMS = [
  { href: "/dpa", label: "DPA Overview", icon: Shield },
  { href: "/dpa/documents", label: "DPA Documents", icon: Folder },
  { href: "/dpa/auditor", label: "DPA Auditor", icon: ClipboardList },
];

export function Sidebar() {
  const pathname = usePathname();
  const { auditInfo } = useAuditInfo();

  return (
    <aside className="sticky top-0 flex h-screen w-64 shrink-0 flex-col overflow-y-auto border-r bg-card p-4">
      <div className="mb-6">
        <h1 className="text-lg font-semibold">CCOA</h1>
        <p className="text-sm text-muted-foreground">Chief Compliance Officer Assistant</p>
      </div>
      <div className="mb-4">
        <Badge variant="secondary">
          ISO: {auditInfo ? Math.max(0, auditInfo.iso.days_remaining) : "--"} · CMMC:{" "}
          {auditInfo ? Math.max(0, auditInfo.cmmc.days_remaining) : "--"} · DPA:{" "}
          {auditInfo && auditInfo.dpa.days_remaining !== null ? Math.max(0, auditInfo.dpa.days_remaining) : "--"}
          {auditInfo && auditInfo.ato.days_remaining !== null ? ` · ATO: ${Math.max(0, auditInfo.ato.days_remaining)}` : ""}
        </Badge>
      </div>
      <nav className="space-y-1">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
                active ? "bg-primary text-primary-foreground" : "hover:bg-muted",
              )}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="mt-4 rounded-md border bg-muted/30 p-2">
        <p className="px-1 pb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">DPA</p>
        <nav className="space-y-1">
          {DPA_NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
                  active ? "bg-primary text-primary-foreground" : "hover:bg-muted",
                )}
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </Link>
            );
          })}
        </nav>
      </div>
      <div className="mt-auto pt-4">
        <Link
          href="/settings"
          className={cn(
            "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
            pathname.startsWith("/settings") ? "bg-primary text-primary-foreground" : "hover:bg-muted",
          )}
        >
          <Settings className="h-4 w-4" />
          Settings
        </Link>
      </div>
    </aside>
  );
}

