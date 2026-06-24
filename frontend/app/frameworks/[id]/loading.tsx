import { SkeletonCard } from "@/components/shared/LoadingStates";

export default function Loading() {
  return (
    <div className="space-y-4">
      <SkeletonCard lines={4} />
      <SkeletonCard lines={6} />
    </div>
  );
}
