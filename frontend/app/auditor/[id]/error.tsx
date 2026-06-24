"use client";

import { ErrorState } from "@/components/shared/LoadingStates";

export default function Error({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <ErrorState
      title="Failed to load auditor checklist"
      description="Could not load auditor checklist details."
      onRetry={() => reset()}
    />
  );
}
