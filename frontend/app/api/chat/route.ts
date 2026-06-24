import { NextRequest } from "next/server";

const API_BASE = process.env.API_BASE_URL || "http://backend:8010";

type ChatRequestBody = {
  messages?: Array<{ role: string; content: string }>;
  conversation_id?: number | null;
};

export async function POST(req: NextRequest) {
  const body = (await req.json()) as ChatRequestBody;
  const messages = body.messages ?? [];
  const lastUserMessage = [...messages].reverse().find((m) => m.role === "user");
  if (!lastUserMessage?.content) {
    return new Response("Missing user message", { status: 400 });
  }

  let conversationId = body.conversation_id ?? null;
  if (conversationId == null) {
    const createResponse = await fetch(`${API_BASE}/chat/conversations`, { method: "POST" });
    if (!createResponse.ok) {
      return new Response("Failed to create conversation", { status: 500 });
    }
    const created = (await createResponse.json()) as { conversation_id: number };
    conversationId = created.conversation_id;
  }

  const streamResponse = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: lastUserMessage.content, conversation_id: conversationId }),
  });
  if (!streamResponse.ok || !streamResponse.body) {
    return new Response("Failed to connect to backend stream", { status: 500 });
  }

  const decoder = new TextDecoder();
  const encoder = new TextEncoder();
  const reader = streamResponse.body.getReader();

  let buffer = "";
  let eventName = "";
  let eventData = "";
  let sawToolCall = false;
  let bufferingLeadText = true;
  const leadingTextBuffer: string[] = [];

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const flushLeadingText = () => {
        if (!leadingTextBuffer.length) return;
        for (const textChunk of leadingTextBuffer) {
          controller.enqueue(encoder.encode(`${JSON.stringify({ type: "text_chunk", text: textChunk })}\n`));
        }
        leadingTextBuffer.length = 0;
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const rawLine of lines) {
          const line = rawLine.trimEnd();
          if (line.startsWith("event:")) {
            eventName = line.replace("event:", "").trim();
          } else if (line.startsWith("data:")) {
            eventData = `${eventData}${line.replace("data:", "").trim()}`;
          } else if (line === "") {
            if (eventName === "text_chunk") {
              try {
                const parsed = JSON.parse(eventData) as { text?: string };
                if (parsed.text) {
                  if (bufferingLeadText && !sawToolCall) {
                    leadingTextBuffer.push(parsed.text);
                    if (leadingTextBuffer.length >= 20) {
                      bufferingLeadText = false;
                      flushLeadingText();
                    }
                  } else {
                    if (bufferingLeadText) {
                      bufferingLeadText = false;
                      flushLeadingText();
                    }
                    controller.enqueue(encoder.encode(`${JSON.stringify({ type: "text_chunk", text: parsed.text })}\n`));
                  }
                }
              } catch {
                // Ignore malformed text chunk events.
              }
            } else if (eventName === "tool_call") {
              try {
                const parsed = JSON.parse(eventData) as {
                  tool_name?: string;
                  status?: string;
                  summary?: string;
                };
                sawToolCall = true;
                controller.enqueue(
                  encoder.encode(
                    `${JSON.stringify({
                      type: "tool_call",
                      tool_name: parsed.tool_name ?? "",
                      status: parsed.status ?? "",
                      summary: parsed.summary ?? "",
                    })}\n`,
                  ),
                );
              } catch {
                // Ignore malformed tool call events.
              }
            } else if (eventName === "error") {
              try {
                const parsed = JSON.parse(eventData) as { message?: string };
                controller.enqueue(
                  encoder.encode(
                    `${JSON.stringify({
                      type: "error",
                      message: parsed.message ?? "Unknown error",
                    })}\n`,
                  ),
                );
              } catch {
                controller.enqueue(
                  encoder.encode(
                    `${JSON.stringify({
                      type: "error",
                      message: "Unknown error",
                    })}\n`,
                  ),
                );
              }
            }
            eventName = "";
            eventData = "";
          }
        }
      }
      if (bufferingLeadText) {
        bufferingLeadText = false;
        flushLeadingText();
      }
      controller.enqueue(encoder.encode(`${JSON.stringify({ type: "done" })}\n`));
      controller.close();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "application/x-ndjson; charset=utf-8",
      "x-conversation-id": String(conversationId),
    },
  });
}

