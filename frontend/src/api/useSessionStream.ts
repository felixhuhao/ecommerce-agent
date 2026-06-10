import { useCallback, useEffect, useReducer, useRef } from "react";
import { initialSessionState, sessionReducer } from "../state/sessionReducer";
import type { ThreadMessage } from "../types";
import { parseStreamEvent } from "./streamEvents";

type EventSourceFactory = (url: string) => EventSource;
const defaultFactory: EventSourceFactory = (url) => new EventSource(url);
const EVENT_NAMES = ["thread.append", "token", "tool", "done", "error"] as const;

export function useSessionStream(
  sessionId: string | null,
  factory: EventSourceFactory = defaultFactory,
) {
  const [state, dispatch] = useReducer(sessionReducer, undefined, initialSessionState);
  const dispatchRef = useRef(dispatch);
  dispatchRef.current = dispatch;

  useEffect(() => {
    dispatchRef.current({ kind: "reset" });
    if (!sessionId) return undefined;

    const eventSource = factory(`/api/sessions/${sessionId}/stream`);
    const handlers = EVENT_NAMES.map((name) => {
      const fn = (event: MessageEvent) => {
        const parsed = parseStreamEvent(name, event.data);
        if (parsed) dispatchRef.current(parsed);
      };
      eventSource.addEventListener(name, fn);
      return [name, fn] as const;
    });

    return () => {
      handlers.forEach(([name, fn]) => eventSource.removeEventListener?.(name, fn));
      eventSource.close();
    };
  }, [sessionId, factory]);

  const markTurnStarted = useCallback((turnId: string) => {
    dispatch({ kind: "turn_started", turnId });
  }, []);

  const applyThread = useCallback((messages: ThreadMessage[]) => {
    dispatch({ kind: "thread_loaded", messages });
  }, []);

  return {
    state,
    markTurnStarted,
    applyThread,
  };
}
