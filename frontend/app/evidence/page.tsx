"use client";

import { Suspense } from "react";

import { SkeletonCard } from "@/components/shared/LoadingStates";

import EvidencePageClient from "./EvidencePageClient";

export default function EvidencePage() {
  return (
    <Suspense fallback={<SkeletonCard lines={4} />}>
      <EvidencePageClient />
    </Suspense>
  );
}
