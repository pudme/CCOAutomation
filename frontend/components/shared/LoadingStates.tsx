"use client";

import { AlertCircle, Inbox } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export const ButtonSpinner = ({ className }: { className?: string }) => (
  <svg className={cn("h-4 w-4 animate-spin", className)} viewBox="0 0 24 24" aria-hidden="true">
    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8V0C5.373 0 0 5.373 0 12h4z" />
  </svg>
);

export const SkeletonCard = ({ lines = 3 }: { lines?: number }) => (
  <div className="animate-pulse space-y-3 rounded-lg border p-4">
    <div className="h-4 w-3/4 rounded bg-gray-200" />
    {Array.from({ length: Math.max(lines - 1, 0) }).map((_, i) => (
      <div key={i} className="h-3 w-full rounded bg-gray-200" />
    ))}
  </div>
);

export const LoadingOverlay = ({ message }: { message: string }) => (
  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20">
    <div className="flex items-center gap-3 rounded-lg bg-white p-6 shadow-xl">
      <ButtonSpinner />
      <span className="text-sm font-medium">{message}</span>
    </div>
  </div>
);

export const StatusMessage = ({
  type,
  message,
}: {
  type: "success" | "error" | "warning" | "info";
  message: string;
}) => {
  const colors = {
    success: "border-green-200 bg-green-50 text-green-800",
    error: "border-red-200 bg-red-50 text-red-800",
    warning: "border-yellow-200 bg-yellow-50 text-yellow-800",
    info: "border-blue-200 bg-blue-50 text-blue-800",
  };
  return <div className={cn("rounded-md border px-4 py-3 text-sm", colors[type])}>{message}</div>;
};

export const EmptyState = ({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) => (
  <div className="rounded-lg border border-dashed p-8 text-center">
    <Inbox className="mx-auto h-8 w-8 text-muted-foreground" />
    <p className="mt-3 text-sm font-semibold">{title}</p>
    <p className="mt-1 text-sm text-muted-foreground">{description}</p>
    {action ? <div className="mt-3">{action}</div> : null}
  </div>
);

export const ErrorState = ({
  title = "Failed to load data",
  description = "Please try again.",
  onRetry,
}: {
  title?: string;
  description?: string;
  onRetry?: () => void;
}) => (
  <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-center">
    <AlertCircle className="mx-auto h-6 w-6 text-red-600" />
    <p className="mt-2 text-sm font-semibold text-red-800">{title}</p>
    <p className="mt-1 text-sm text-red-700">{description}</p>
    {onRetry ? (
      <button onClick={onRetry} className="mt-3 text-sm text-blue-600 underline">
        Try again
      </button>
    ) : null}
  </div>
);
