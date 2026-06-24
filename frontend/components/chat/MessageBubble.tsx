import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type MessageBubbleProps = {
  role: "user" | "assistant";
  content: string;
};

function renderSimpleMarkdown(input: string): string {
  return input
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.*?)`/g, "<code>$1</code>")
    .replace(/\n/g, "<br />");
}

export function MessageBubble({ role, content }: MessageBubbleProps) {
  return (
    <div className={cn("flex", role === "user" ? "justify-end" : "justify-start")}>
      <Card
        className={cn(
          "max-w-3xl px-4 py-3",
          role === "user" ? "bg-primary text-primary-foreground" : "bg-card",
        )}
      >
        {role === "assistant" ? (
          <p className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">CCOA AI</p>
        ) : null}
        <div dangerouslySetInnerHTML={{ __html: renderSimpleMarkdown(content) }} />
      </Card>
    </div>
  );
}

