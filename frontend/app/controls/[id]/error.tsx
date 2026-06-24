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
      title="Failed to load control"
      description="Could not load the control detail."
      onRetry={() => reset()}
    />
  );
}
