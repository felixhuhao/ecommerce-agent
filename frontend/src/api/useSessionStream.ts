import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { initialSessionState, sessionReducer } from "../state/sessionReducer";
import type { ThreadMessage } from "../types";
import { parseStreamEvent } from "./streamEvents";

type EventSourceFactory = (url: string) => EventSource;
export type StreamStatus = "idle" | "connecting" | "open" | "reconnecting";
const defaultFactory: EventSourceFactory = (url) => new EventSource(url);
const MESSAGE_EVENT_NAMES = ["thread.append", "token", "tool", "turn.progress", "done"] as const;

export function useSessionStream(
  sessionId: string | null,
  factory: EventSourceFactory = defaultFactory,
) {
  const [state, dispatch] = useReducer(sessionReducer, undefined, initialSessionState);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("idle");
  const dispatchRef = useRef(dispatch);
  dispatchRef.current = dispatch;

  useEffect(() => {
    dispatchRef.current({ kind: "reset" });
    setStreamStatus(sessionId ? "connecting" : "idle");
    if (!sessionId) return undefined;

    const eventSource = factory(`/api/sessions/${sessionId}/stream`);
    const onOpen = () => setStreamStatus("open");
    const onError = (event: Event) => {
      const data = "data" in event ? (event as MessageEvent).data : undefined;
      if (typeof data === "string") {
        const parsed = parseStreamEvent("error", data);
        if (parsed) dispatchRef.current(parsed);
        return;
      }
      setStreamStatus("reconnecting");
    };
    eventSource.addEventListener("open", onOpen);
    eventSource.addEventListener("error", onError);

    const handlers = MESSAGE_EVENT_NAMES.map((name) => {
      const fn = (event: MessageEvent) => {
        const parsed = parseStreamEvent(name, event.data);
        if (parsed) dispatchRef.current(parsed);
      };
      eventSource.addEventListener(name, fn);
      return [name, fn] as const;
    });

    return () => {
      handlers.forEach(([name, fn]) => eventSource.removeEventListener?.(name, fn));
      eventSource.removeEventListener?.("open", onOpen);
      eventSource.removeEventListener?.("error", onError);
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
    streamStatus,
    markTurnStarted,
    applyThread,
  };
}
