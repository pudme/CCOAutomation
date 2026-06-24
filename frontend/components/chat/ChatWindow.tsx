"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

import { ButtonSpinner } from "@/components/shared/LoadingStates";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { SuggestedPrompts } from "@/components/chat/SuggestedPrompts";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

type ChatStreamEvent =
  | { type: "text_chunk"; text: string }
  | { type: "tool_call"; tool_name: string; status: string; summary?: string }
  | { type: "error"; message: string }
  | { type: "done" };

const READ_TOOLS = new Set([
  "search_documents",
  "get_control",
  "get_framework_gaps",
  "get_open_findings",
  "get_personnel_exceptions",
  "run_personnel_check",
  "get_obligations_due",
  "list_obligations",
  "get_framework_detail",
  "get_import_history",
  "get_auditor_checklist",
  "get_unsatisfied_auditor_items",
]);
const WRITE_TOOLS = new Set([
  "update_control_status",
  "add_evidence",
  "create_finding",
  "update_finding",
  "add_corrective_action",
  "update_obligation",
  "create_obligation",
  "update_checklist_item",
  "ingest_notion_page",
  "ingest_text",
]);
const GENERATE_TOOLS = new Set([
  "generate_gap_report",
  "generate_scorecard",
  "generate_audit_package",
  "generate_corrective_action_report",
]);

export function ChatWindow() {
  const searchParams = useSearchParams();
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [selectedFileName, setSelectedFileName] = useState<string | null>(null);
  const [selectedFileSize, setSelectedFileSize] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showThinking, setShowThinking] = useState(false);
  const [thinkingStatus, setThinkingStatus] = useState("Thinking...");

  useEffect(() => {
    const prefill = searchParams.get("prefill");
    if (prefill && !input) {
      setInput(prefill);
    }
  }, [searchParams, input]);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!input.trim() || isSubmitting) {
      return;
    }

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: input,
    };
    const assistantId = crypto.randomUUID();
    setMessages((prev) => [...prev, userMessage, { id: assistantId, role: "assistant", content: "" }]);
    setInput("");
    setIsSubmitting(true);
    setShowThinking(true);
    setThinkingStatus("Thinking...");

    try {
      const payload = {
        messages: [...messages, userMessage].map((message) => ({ role: message.role, content: message.content })),
        conversation_id: conversationId,
      };
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok || !response.body) {
        throw new Error("Failed to stream chat response");
      }

      const nextConversationId = response.headers.get("x-conversation-id");
      if (nextConversationId) {
        setConversationId(Number(nextConversationId));
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let sawFirstTextToken = false;
      let releasedSubmit = false;
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const rawLine of lines) {
          const line = rawLine.trim();
          if (!line) continue;
          let event: ChatStreamEvent | null = null;
          try {
            event = JSON.parse(line) as ChatStreamEvent;
          } catch {
            event = null;
          }
          if (!event) continue;
          if (event.type === "text_chunk") {
            if (!releasedSubmit) {
              releasedSubmit = true;
              setIsSubmitting(false);
            }
            if (!sawFirstTextToken) {
              sawFirstTextToken = true;
              setShowThinking(false);
            }
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantId ? { ...message, content: `${message.content}${event.text}` } : message,
              ),
            );
          } else if (event.type === "tool_call" && event.status === "started") {
            if (!releasedSubmit) {
              releasedSubmit = true;
              setIsSubmitting(false);
            }
            if (GENERATE_TOOLS.has(event.tool_name)) {
              setThinkingStatus("Generating report...");
            } else if (WRITE_TOOLS.has(event.tool_name)) {
              setThinkingStatus("Updating records...");
            } else if (READ_TOOLS.has(event.tool_name)) {
              setThinkingStatus("Searching compliance records...");
            }
          } else if (event.type === "error") {
            setShowThinking(false);
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantId
                  ? { ...message, content: `Error while streaming response: ${event.message}` }
                  : message,
              ),
            );
          } else if (event.type === "done") {
            setShowThinking(false);
          }
        }
      }
      setShowThinking(false);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Unknown chat error";
      setShowThinking(false);
      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantId
            ? { ...message, content: `Error while streaming response: ${errorMessage}` }
            : message,
        ),
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const hasMessages = messages.length > 0;
  const displayMessages = useMemo(
    () =>
      messages.filter((message): message is ChatMessage => message.role === "user" || message.role === "assistant"),
    [messages],
  );

  return (
    <div className="flex h-[calc(100vh-9rem)] flex-col gap-4">
      <ScrollArea className="flex-1 rounded-md border p-4">
        {!hasMessages ? (
          <SuggestedPrompts onSelect={(prompt) => setInput(prompt)} />
        ) : (
          <div className="space-y-4">
            {displayMessages.map((message) => (
              <MessageBubble key={message.id} role={message.role} content={message.content} />
            ))}
            {showThinking ? (
              <div className="flex justify-start">
                <Card className="max-w-3xl px-4 py-3">
                  <p className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">{thinkingStatus}</p>
                  <div className="flex items-center gap-1 p-1">
                    <span className="h-2 w-2 animate-bounce rounded-full bg-gray-400" style={{ animationDelay: "0ms" }} />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-gray-400" style={{ animationDelay: "150ms" }} />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-gray-400" style={{ animationDelay: "300ms" }} />
                  </div>
                </Card>
              </div>
            ) : null}
          </div>
        )}
      </ScrollArea>

      <form
        className="sticky bottom-0 flex items-center gap-2 rounded-md border bg-background p-3"
        onSubmit={handleSubmit}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          const file = event.dataTransfer.files?.[0];
          if (!file) return;
          setSelectedFileName(file.name);
          setSelectedFileSize(file.size);
        }}
      >
        <Input
          type="text"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Ask about compliance status, findings, controls, or obligations..."
        />
        <Input
          type="file"
          className="max-w-[220px]"
          onChange={(event) => {
            const file = event.target.files?.[0];
            setSelectedFileName(file?.name ?? null);
            setSelectedFileSize(file?.size ?? null);
          }}
        />
        <Button type="submit" disabled={isSubmitting}>
          <span className="flex items-center gap-2">
            {isSubmitting ? <ButtonSpinner /> : null}
            {isSubmitting ? "Sending..." : "Send"}
          </span>
        </Button>
      </form>
      {selectedFileName ? (
        <p className="text-xs text-muted-foreground">
          Selected file: {selectedFileName}
          {selectedFileSize !== null ? ` (${Math.max(1, Math.round(selectedFileSize / 1024))} KB)` : ""}
        </p>
      ) : null}
    </div>
  );
}

