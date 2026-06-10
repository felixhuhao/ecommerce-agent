import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "./components/AppShell";
import { ApprovalWorkspace } from "./components/ApprovalWorkspace";
import { ArtifactPanel } from "./components/ArtifactPanel";
import { ConversationView } from "./components/ConversationView";
import { HealthPanel } from "./components/HealthPanel";
import { RightRail, type RailTab } from "./components/RightRail";
import { SessionSidebar } from "./components/SessionSidebar";
import { TracePanel } from "./components/TracePanel";
import {
  approveApproval,
  createSession,
  getArtifacts,
  getHealth,
  getMcpHealth,
  getThread,
  getTrace,
  listSessions,
  postMessage,
  rejectApproval,
  shouldRetryTrace,
  traceExportUrl,
} from "./api/client";
import { useSessionStream } from "./api/useSessionStream";
import { performApprove, performReject } from "./state/approvalActions";
import { foldApprovals } from "./state/approvals";
import { performSend } from "./state/sendMessage";
import type { ArtifactSummary, SessionSummary } from "./types";

const EMPTY_SESSIONS: SessionSummary[] = [];
const EMPTY_ARTIFACTS: ArtifactSummary[] = [];

function isNotFound(error: unknown) {
  return error instanceof Error && error.message === "404";
}

export function App() {
  const queryClient = useQueryClient();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [busyNote, setBusyNote] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingApprovalId, setPendingApprovalId] = useState<string | null>(null);
  const [pendingSendSessionId, setPendingSendSessionId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<RailTab>("approvals");
  const [inspectedTurnId, setInspectedTurnId] = useState<string | null>(null);
  const [focusMessageId, setFocusMessageId] = useState<string | null>(null);
  const [focusApprovalId, setFocusApprovalId] = useState<string | null>(null);
  const activeIdRef = useRef<string | null>(null);
  const pendingSendSessionIdRef = useRef<string | null>(null);
  const busyNoteTimeoutRef = useRef<number | null>(null);
  const wasInFlight = useRef(false);
  activeIdRef.current = activeId;
  pendingSendSessionIdRef.current = pendingSendSessionId;

  const sessionsQuery = useQuery({
    queryKey: ["sessions"],
    queryFn: listSessions,
    refetchInterval: 5000,
  });
  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 10000,
    retry: false,
  });
  const mcpQuery = useQuery({
    queryKey: ["health", "mcp"],
    queryFn: getMcpHealth,
    refetchInterval: 10000,
    retry: false,
  });
  const artifactsQuery = useQuery({
    queryKey: ["artifacts", activeId],
    queryFn: () => getArtifacts(activeId as string),
    enabled: !!activeId,
  });
  const traceQuery = useQuery({
    queryKey: ["trace", activeId, inspectedTurnId],
    queryFn: () => getTrace(activeId as string, inspectedTurnId as string),
    enabled: activeTab === "trace" && !!activeId && !!inspectedTurnId,
    retry: shouldRetryTrace,
    retryDelay: 400,
  });

  const sessions = sessionsQuery.data ?? EMPTY_SESSIONS;

  useEffect(() => {
    if (!activeId && sessions.length > 0) setActiveId(sessions[0].session_id);
  }, [activeId, sessions]);

  const { state, streamStatus, markTurnStarted, applyThread } = useSessionStream(activeId);
  const approvals = useMemo(() => foldApprovals(state.messages), [state.messages]);

  useEffect(() => {
    setInspectedTurnId(null);
    setFocusMessageId(null);
    setFocusApprovalId(null);
  }, [activeId]);

  useEffect(() => {
    const inFlight = state.inFlightTurnId !== null;
    if (wasInFlight.current && !inFlight && activeIdRef.current) {
      queryClient.invalidateQueries({ queryKey: ["artifacts", activeIdRef.current] });
    }
    wasInFlight.current = inFlight;
  }, [state.inFlightTurnId, queryClient]);

  const clearBusyNoteTimeout = useCallback(() => {
    if (busyNoteTimeoutRef.current !== null) {
      window.clearTimeout(busyNoteTimeoutRef.current);
      busyNoteTimeoutRef.current = null;
    }
  }, []);

  useEffect(() => clearBusyNoteTimeout, [clearBusyNoteTimeout]);

  const handleMissingSession = useCallback(
    async (sessionId: string) => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      if (activeIdRef.current !== sessionId) return;
      applyThread([]);
      setActiveId(null);
      setActionError("Session no longer exists.");
    },
    [applyThread, queryClient],
  );

  const createMutation = useMutation({
    mutationFn: createSession,
    onSuccess: async ({ session_id }) => {
      setActiveId(session_id);
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  const reconcileThread = useCallback(async (sessionId: string | null = activeIdRef.current) => {
    if (!sessionId) return;
    try {
      const messages = await getThread(sessionId);
      if (activeIdRef.current !== sessionId) return;
      applyThread(messages);
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    } catch (error) {
      if (isNotFound(error)) {
        await handleMissingSession(sessionId);
        return;
      }
      throw error;
    }
  }, [applyThread, handleMissingSession, queryClient]);

  const handleSend = useCallback(
    async (message: string) => {
      const sessionId = activeIdRef.current;
      if (!sessionId || pendingSendSessionIdRef.current === sessionId) return;
      pendingSendSessionIdRef.current = sessionId;
      setPendingSendSessionId(sessionId);
      setActionError(null);
      clearBusyNoteTimeout();
      setBusyNote(null);
      try {
        const result = await performSend(sessionId, message, { postMessage }, (turnId) => {
          if (activeIdRef.current === sessionId) markTurnStarted(turnId);
        });
        if (activeIdRef.current !== sessionId) return;
        if (result.busy) {
          setBusyNote("A turn is already running.");
          busyNoteTimeoutRef.current = window.setTimeout(() => setBusyNote(null), 3500);
        }
        await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      } catch (error) {
        if (isNotFound(error)) {
          await handleMissingSession(sessionId);
          return;
        }
        setActionError(error instanceof Error ? error.message : "Send failed");
      } finally {
        if (pendingSendSessionIdRef.current === sessionId) {
          pendingSendSessionIdRef.current = null;
          setPendingSendSessionId(null);
        }
      }
    },
    [clearBusyNoteTimeout, handleMissingSession, markTurnStarted, queryClient],
  );

  const handleApprove = useCallback(
    async (approvalId: string) => {
      const sessionId = activeId;
      if (!sessionId) return;
      setPendingApprovalId(approvalId);
      setActionError(null);
      try {
        await performApprove(sessionId, approvalId, { approveApproval }, () =>
          reconcileThread(sessionId),
        );
      } catch (error) {
        setActionError(error instanceof Error ? error.message : "Approval failed");
      } finally {
        setPendingApprovalId(null);
      }
    },
    [activeId, reconcileThread],
  );

  const handleReject = useCallback(
    async (approvalId: string, reason: string | undefined) => {
      const sessionId = activeId;
      if (!sessionId) return;
      setPendingApprovalId(approvalId);
      setActionError(null);
      try {
        await performReject(sessionId, approvalId, reason, { rejectApproval }, () =>
          reconcileThread(sessionId),
        );
      } catch (error) {
        setActionError(error instanceof Error ? error.message : "Reject failed");
      } finally {
        setPendingApprovalId(null);
      }
    },
    [activeId, reconcileThread],
  );

  const handleSelectSession = useCallback((sessionId: string) => {
    setActiveId(sessionId);
    setActionError(null);
    clearBusyNoteTimeout();
    setBusyNote(null);
  }, [clearBusyNoteTimeout]);

  const handleInspect = useCallback((turnId: string) => {
    setInspectedTurnId(turnId);
    setActiveTab("trace");
  }, []);

  const handleViewApproval = useCallback((approvalId: string) => {
    setActiveTab("approvals");
    setFocusApprovalId(approvalId);
  }, []);

  const handleViewArtifacts = useCallback(() => {
    setActiveTab("artifacts");
  }, []);

  const handleJumpToMessage = useCallback((messageId: string) => {
    setFocusMessageId(messageId);
  }, []);

  const handleFocusMessageHandled = useCallback(() => {
    setFocusMessageId(null);
  }, []);

  const handleFocusApprovalHandled = useCallback(() => {
    setFocusApprovalId(null);
  }, []);

  const createNewSession = createMutation.mutate;
  const handleNewSession = useCallback(() => {
    createNewSession();
  }, [createNewSession]);

  return (
    <AppShell
      sidebar={
        <SessionSidebar
          sessions={sessions}
          activeId={activeId}
          isCreating={createMutation.isPending}
          onSelect={handleSelectSession}
          onNew={handleNewSession}
        />
      }
      conversation={
        <ConversationView
          messages={state.messages}
          provisionalAnswer={state.inFlightTurnId ? state.tokenBuffer : null}
          activeTool={state.activeTool}
          streamStatus={streamStatus}
          composerDisabled={
            !activeId ||
            state.inFlightTurnId !== null ||
            pendingSendSessionId === activeId
          }
          busyNote={busyNote}
          error={state.error}
          onSend={handleSend}
          onInspect={handleInspect}
          focusMessageId={focusMessageId}
          onFocusMessageHandled={handleFocusMessageHandled}
        />
      }
      rail={
        <RightRail
          activeTab={activeTab}
          onTabChange={setActiveTab}
          approvalCount={approvals.filter((approval) => approval.status === "pending").length}
          approvals={
            <ApprovalWorkspace
              approvals={approvals}
              pendingApprovalId={pendingApprovalId}
              actionError={actionError}
              onApprove={handleApprove}
              onReject={handleReject}
              focusApprovalId={focusApprovalId}
              onFocusApprovalHandled={handleFocusApprovalHandled}
            />
          }
          artifacts={
            <ArtifactPanel
              artifacts={artifactsQuery.data ?? EMPTY_ARTIFACTS}
              isLoading={artifactsQuery.isLoading}
              isError={artifactsQuery.isError}
              onJumpToMessage={handleJumpToMessage}
            />
          }
          trace={
            <TracePanel
              timeline={traceQuery.data}
              inspectedTurnId={inspectedTurnId}
              isLoading={traceQuery.isLoading}
              isError={traceQuery.isError}
              exportHref={activeId && inspectedTurnId ? traceExportUrl(activeId, inspectedTurnId) : null}
              onViewArtifacts={handleViewArtifacts}
              onViewApproval={handleViewApproval}
            />
          }
          health={
            <HealthPanel
              health={healthQuery.data}
              mcp={mcpQuery.data}
              healthUnavailable={healthQuery.isError || healthQuery.isRefetchError}
              mcpUnavailable={mcpQuery.isError || mcpQuery.isRefetchError}
            />
          }
        />
      }
    />
  );
}
