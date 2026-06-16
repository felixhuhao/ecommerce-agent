import type { StreamEvent, ThreadMessage, TurnProgressStep } from "../types";

export type SessionAction =
  | StreamEvent
  | { kind: "turn_started"; turnId: string }
  | { kind: "reset" }
  | { kind: "thread_loaded"; messages: ThreadMessage[] };

export interface SessionState {
  bySeq: Record<number, ThreadMessage>;
  messages: ThreadMessage[];
  inFlightTurnId: string | null;
  tokenBuffer: string;
  activeTool: string | null;
  turnProgress: TurnProgressStep[];
  error: string | null;
}

export function initialSessionState(): SessionState {
  return {
    bySeq: {},
    messages: [],
    inFlightTurnId: null,
    tokenBuffer: "",
    activeTool: null,
    turnProgress: [],
    error: null,
  };
}

const TERMINAL_TYPES = new Set(["agent_answer", "agent_proposal"]);

function sortedMessages(bySeq: Record<number, ThreadMessage>): ThreadMessage[] {
  return Object.values(bySeq).sort((a, b) => a.seq - b.seq);
}

function finalize(state: SessionState): SessionState {
  return { ...state, inFlightTurnId: null, tokenBuffer: "", activeTool: null, turnProgress: [] };
}

function completeStartingStep(steps: TurnProgressStep[], incoming: TurnProgressStep): TurnProgressStep[] {
  if (incoming.stepId.startsWith("start:")) return steps;
  return steps.map((step) =>
    step.status === "running" && step.stepId.startsWith("start:")
      ? { ...step, status: "done" as const }
      : step,
  );
}

function upsertProgress(steps: TurnProgressStep[], step: TurnProgressStep): TurnProgressStep[] {
  const next = completeStartingStep(steps, step);
  const index = next.findIndex((existing) => existing.stepId === step.stepId);
  if (index === -1) return [...next, step];
  return next.map((existing, existingIndex) =>
    existingIndex === index ? { ...existing, ...step } : existing,
  );
}

function failRunningProgress(steps: TurnProgressStep[]): TurnProgressStep[] {
  return steps.map((step) => {
    if (step.status === "running") {
      return { ...step, status: "failed" as const };
    }
    return step;
  });
}

export function sessionReducer(state: SessionState, action: SessionAction): SessionState {
  switch (action.kind) {
    case "reset":
      return initialSessionState();
    case "thread_loaded": {
      const bySeq: Record<number, ThreadMessage> = {};
      for (const message of action.messages) bySeq[message.seq] = message;
      return { ...initialSessionState(), bySeq, messages: sortedMessages(bySeq) };
    }
    case "turn_started":
      return {
        ...state,
        inFlightTurnId: action.turnId,
        tokenBuffer: "",
        activeTool: null,
        turnProgress: [
          {
            turnId: action.turnId,
            stepId: `start:${action.turnId}`,
            kind: "start",
            label: "Starting turn",
            status: "running",
            detail: null,
            ts: null,
          },
        ],
        error: null,
      };
    case "thread.append": {
      const message = action.message;
      const bySeq = { ...state.bySeq, [message.seq]: message };
      const next = { ...state, bySeq, messages: sortedMessages(bySeq) };
      if (
        TERMINAL_TYPES.has(message.type) &&
        message.turn_id &&
        message.turn_id === state.inFlightTurnId
      ) {
        return finalize(next);
      }
      return next;
    }
    case "token":
      return state.inFlightTurnId
        ? { ...state, tokenBuffer: state.tokenBuffer + action.text }
        : state;
    case "tool":
      return { ...state, activeTool: action.phase === "start" ? action.name : null };
    case "turn.progress":
      return action.step.turnId === state.inFlightTurnId
        ? { ...state, turnProgress: upsertProgress(state.turnProgress, action.step) }
        : state;
    case "done":
      return action.turnId === state.inFlightTurnId ? finalize(state) : state;
    case "error":
      return {
        ...state,
        inFlightTurnId: null,
        tokenBuffer: "",
        activeTool: null,
        turnProgress: failRunningProgress(state.turnProgress),
        error: action.message,
      };
    default:
      return state;
  }
}
