"use client";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { BatchCostEstimate } from "@/lib/api";

type Props = {
  open: boolean;
  estimate: BatchCostEstimate | null;
  message?: string | null;
  onRunAnyway: () => void;
  onIncreaseLimit: () => void;
  onCancel: () => void;
};

export function ApiLimitOverrideDialog({
  open,
  estimate,
  message,
  onRunAnyway,
  onIncreaseLimit,
  onCancel,
}: Props) {
  if (!open || !estimate) return null;
  const used = estimate.daily_limit - estimate.remaining;
  const projectedUsed = used + estimate.estimated_calls;
  const percent = Math.max(0, Math.min(100, Math.round((projectedUsed / estimate.daily_limit) * 100)));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <Card className="w-full max-w-2xl space-y-4 p-5">
        <h3 className="text-lg font-semibold">API Limit Warning</h3>
        <p className="text-sm text-muted-foreground">
          {message ||
            `This operation will make approximately ${estimate.estimated_calls} API calls. Your daily limit is ${estimate.daily_limit} and you have ${estimate.remaining} remaining today.`}
        </p>
        <p className="text-sm font-medium">Estimated additional cost: ${estimate.estimated_cost_usd.toFixed(2)}</p>
        <div className="space-y-2">
          <div className="h-2 w-full rounded bg-muted">
            <div className="h-2 rounded bg-yellow-500" style={{ width: `${percent}%` }} />
          </div>
          <p className="text-xs text-muted-foreground">{percent}% of daily limit</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="destructive" onClick={onRunAnyway}>
            Run Anyway - Bypass Limit Once
          </Button>
          <Button variant="outline" onClick={onIncreaseLimit}>
            Increase Daily Limit
          </Button>
          <Button variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </Card>
    </div>
  );
}
