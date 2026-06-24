import { Card } from "@/components/ui/card";

const PROMPTS = [
  "What's my audit readiness across all frameworks right now?",
  "What are the highest-priority items I need to close before the May audit?",
  "I just uploaded a [file] - what did you find in it?",
  "Draft a corrective action summary for Todd",
  "What evidence am I still missing for CMMC?",
  "Run a personnel compliance check and tell me what's flagged",
];

type SuggestedPromptsProps = {
  onSelect: (prompt: string) => void;
};

export function SuggestedPrompts({ onSelect }: SuggestedPromptsProps) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {PROMPTS.map((prompt) => (
        <Card
          key={prompt}
          role="button"
          className="cursor-pointer p-4 text-sm hover:bg-muted"
          onClick={() => onSelect(prompt)}
        >
          {prompt}
        </Card>
      ))}
    </div>
  );
}

