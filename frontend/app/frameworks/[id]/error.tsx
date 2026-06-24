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
      title="Failed to load framework detail"
      description="Could not load this framework page."
      onRetry={() => reset()}
    />
  );
}
