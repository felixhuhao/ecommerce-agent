import type { StreamEvent, ThreadMessage } from "../types";

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
  error: string | null;
}

export function initialSessionState(): SessionState {
  return {
    bySeq: {},
    messages: [],
    inFlightTurnId: null,
    tokenBuffer: "",
    activeTool: null,
    error: null,
  };
}

const TERMINAL_TYPES = new Set(["agent_answer", "agent_proposal"]);

function sortedMessages(bySeq: Record<number, ThreadMessage>): ThreadMessage[] {
  return Object.values(bySeq).sort((a, b) => a.seq - b.seq);
}

function finalize(state: SessionState): SessionState {
  return { ...state, inFlightTurnId: null, tokenBuffer: "", activeTool: null };
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
    case "done":
      return action.turnId === state.inFlightTurnId ? finalize(state) : state;
    case "error":
      return { ...state, error: action.message };
    default:
      return state;
  }
}
