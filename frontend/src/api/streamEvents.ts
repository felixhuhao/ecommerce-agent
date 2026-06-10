import type { StreamEvent, ThreadMessage } from "../types";

export function parseStreamEvent(eventName: string, data: string): StreamEvent | null {
  let payload: unknown;
  try {
    payload = JSON.parse(data);
  } catch {
    return null;
  }

  if (!payload || typeof payload !== "object") return null;
  const body = payload as Record<string, unknown>;

  switch (eventName) {
    case "thread.append":
      return body.message && typeof body.message === "object"
        ? { kind: "thread.append", message: body.message as ThreadMessage }
        : null;
    case "token":
      return typeof body.text === "string" ? { kind: "token", text: body.text } : null;
    case "tool":
      return typeof body.name === "string" && typeof body.phase === "string"
        ? { kind: "tool", name: body.name, phase: body.phase }
        : null;
    case "done":
      return typeof body.turn_id === "string" ? { kind: "done", turnId: body.turn_id } : null;
    case "error":
      return {
        kind: "error",
        message: typeof body.message === "string" ? body.message : "error",
      };
    default:
      return null;
  }
}
