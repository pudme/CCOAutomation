import { SkeletonCard } from "@/components/shared/LoadingStates";

export default function Loading() {
  return (
    <div className="space-y-3">
      <SkeletonCard lines={4} />
      <SkeletonCard lines={4} />
    </div>
  );
}
