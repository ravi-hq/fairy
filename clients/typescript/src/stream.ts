import { raiseForStatus } from "./errors.js";
import { streamEventFromPayload, type StreamEvent } from "./types.js";

export interface StreamHandle extends AsyncIterable<StreamEvent> {
  close(): Promise<void>;
}

export function createStreamHandle(
  response: Response,
  url: string,
  controller: AbortController,
): StreamHandle {
  const iterator = iterateSSE(response, url);
  return {
    [Symbol.asyncIterator]() {
      return iterator;
    },
    async close() {
      controller.abort();
      try {
        await iterator.return?.(undefined);
      } catch {
        // iterator already finished or errored — nothing to do
      }
    },
  };
}

async function* iterateSSE(
  response: Response,
  url: string,
): AsyncGenerator<StreamEvent, void, unknown> {
  if (response.status >= 400) {
    const text = await response.text();
    let parsed: unknown = text || null;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = text;
      }
    }
    raiseForStatus(response.status, parsed, "GET", url);
    return;
  }

  if (!response.body) return;

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let newlineIndex: number;
      while ((newlineIndex = buffer.indexOf("\n")) !== -1) {
        const rawLine = buffer.slice(0, newlineIndex);
        buffer = buffer.slice(newlineIndex + 1);
        const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
        const event = parseDataLine(line);
        if (event) yield event;
      }
    }

    buffer += decoder.decode();
    if (buffer.length > 0) {
      const event = parseDataLine(buffer);
      if (event) yield event;
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // reader already released
    }
  }
}

function parseDataLine(line: string): StreamEvent | null {
  if (!line.startsWith("data:")) return null;
  const raw = line.slice(5).replace(/^\s+/, "");
  if (!raw) return null;
  let payload: unknown;
  try {
    payload = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return null;
  }
  return streamEventFromPayload(payload as Record<string, unknown>);
}
