import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useSessionStream } from "./useSessionStream";

class FakeEventSource {
  listeners: Record<string, (event: Event) => void> = {};

  constructor(public url: string) {}

  addEventListener(name: string, fn: (event: Event) => void) {
    this.listeners[name] = fn;
  }

  removeEventListener(name: string) {
    delete this.listeners[name];
  }

  emit(name: string, data: unknown) {
    this.listeners[name]?.({ data: JSON.stringify(data) } as MessageEvent);
  }

  emitOpen() {
    this.listeners.open?.(new Event("open"));
  }

  emitTransportError() {
    this.listeners.error?.(new Event("error"));
  }

  close() {}
}

describe("useSessionStream", () => {
  it("applies thread.append + token frames into state", () => {
    let eventSource!: FakeEventSource;
    const factory = (url: string) => {
      eventSource = new FakeEventSource(url);
      return eventSource as unknown as EventSource;
    };
    const { result } = renderHook(() => useSessionStream("s1", factory));

    act(() => result.current.markTurnStarted("t1"));
    act(() => eventSource.emit("token", { text: "Hi" }));
    act(() =>
      eventSource.emit("thread.append", {
        message: { seq: 1, type: "agent_answer", content: "Hi", turn_id: "t1" },
      }),
    );

    expect(result.current.state.messages.map((message) => message.seq)).toEqual([1]);
    expect(result.current.state.inFlightTurnId).toBeNull();
  });

  it("tracks reconnecting state for transport errors and recovers on open", () => {
    let eventSource!: FakeEventSource;
    const factory = (url: string) => {
      eventSource = new FakeEventSource(url);
      return eventSource as unknown as EventSource;
    };
    const { result } = renderHook(() => useSessionStream("s1", factory));

    expect(result.current.streamStatus).toBe("connecting");
    act(() => eventSource.emitOpen());
    expect(result.current.streamStatus).toBe("open");
    act(() => eventSource.emitTransportError());
    expect(result.current.streamStatus).toBe("reconnecting");
    act(() => eventSource.emitOpen());
    expect(result.current.streamStatus).toBe("open");
  });

  it("keeps backend error events separate from transport errors", () => {
    let eventSource!: FakeEventSource;
    const factory = (url: string) => {
      eventSource = new FakeEventSource(url);
      return eventSource as unknown as EventSource;
    };
    const { result } = renderHook(() => useSessionStream("s1", factory));

    act(() => eventSource.emitOpen());
    act(() => eventSource.emit("error", { message: "turn failed" }));

    expect(result.current.streamStatus).toBe("open");
    expect(result.current.state.error).toBe("turn failed");
  });

  it("applies turn progress frames", () => {
    let eventSource!: FakeEventSource;
    const factory = (url: string) => {
      eventSource = new FakeEventSource(url);
      return eventSource as unknown as EventSource;
    };
    const { result } = renderHook(() => useSessionStream("s1", factory));

    act(() => result.current.markTurnStarted("t1"));
    act(() =>
      eventSource.emit("turn.progress", {
        turn_id: "t1",
        step_id: "tool:stats",
        kind: "tool",
        label: "Reading sales data",
        status: "running",
      }),
    );

    expect(result.current.state.turnProgress.map((step) => step.label)).toEqual([
      "Starting turn",
      "Reading sales data",
    ]);
  });

  it("resets state when the session id changes", () => {
    const factory = (url: string) => new FakeEventSource(url) as unknown as EventSource;
    const { result, rerender } = renderHook(({ id }) => useSessionStream(id, factory), {
      initialProps: { id: "s1" as string | null },
    });

    act(() => result.current.markTurnStarted("t1"));
    expect(result.current.state.inFlightTurnId).toBe("t1");

    act(() => rerender({ id: "s2" }));
    expect(result.current.state.inFlightTurnId).toBeNull();
    expect(result.current.state.messages).toEqual([]);
  });
});
