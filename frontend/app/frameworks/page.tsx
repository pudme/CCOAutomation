/* eslint-disable @typescript-eslint/no-explicit-any */
import Link from "next/link";

import { EmptyState } from "@/components/shared/LoadingStates";
import { Card } from "@/components/ui/card";
import { getFrameworks } from "@/lib/api";
import { formatLabel } from "@/lib/utils";

export default async function FrameworksPage() {
  const frameworks = await getFrameworks();
  return (
    <div className="grid gap-3">
      {frameworks.length === 0 ? (
        <EmptyState title="No frameworks yet" description="No frameworks were returned by the API." />
      ) : (
        frameworks.map((framework: any) => (
          <Link key={framework.short_name} href={`/frameworks/${framework.short_name}`}>
            <Card className="p-4 hover:bg-accent">
              <p className="font-semibold">{framework.name}</p>
              <p className="text-xs text-muted-foreground">
                {formatLabel(framework.short_name)} v{framework.version}
              </p>
            </Card>
          </Link>
        ))
      )}
    </div>
  );
}

