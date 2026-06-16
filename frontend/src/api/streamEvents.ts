import type { StreamEvent, ThreadMessage, TurnProgressStatus } from "../types";

const PROGRESS_STATUSES = new Set<TurnProgressStatus>(["pending", "running", "done", "failed"]);

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
    case "turn.progress":
      return parseProgress(body);
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

function parseProgress(body: Record<string, unknown>): StreamEvent | null {
  if (
    typeof body.turn_id !== "string" ||
    typeof body.step_id !== "string" ||
    typeof body.kind !== "string" ||
    typeof body.label !== "string" ||
    typeof body.status !== "string" ||
    !PROGRESS_STATUSES.has(body.status as TurnProgressStatus)
  ) {
    return null;
  }

  return {
    kind: "turn.progress",
    step: {
      turnId: body.turn_id,
      stepId: body.step_id,
      kind: body.kind,
      label: body.label,
      status: body.status as TurnProgressStatus,
      detail: typeof body.detail === "string" ? body.detail : null,
      ts: typeof body.ts === "number" ? body.ts : null,
    },
  };
}
