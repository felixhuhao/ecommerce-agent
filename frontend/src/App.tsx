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
  ApiError,
  type Me,
  approveApproval,
  createSession,
  getArtifacts,
  getHealth,
  getMe,
  getMcpHealth,
  getThread,
  getTrace,
  login,
  logout,
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
  return isApiStatus(error, 404);
}

function isUnauthorized(error: unknown) {
  return isApiStatus(error, 401);
}

function isApiStatus(error: unknown, status: number) {
  return (
    (error instanceof ApiError && error.status === status) ||
    (error instanceof Error && error.message === String(status))
  );
}

export function App() {
  const queryClient = useQueryClient();
  const meQuery = useQuery({ queryKey: ["auth", "me"], queryFn: getMe, retry: false });
  const [loginError, setLoginError] = useState<string | null>(null);

  const loginMutation = useMutation({
    mutationFn: ({ username, password }: { username: string; password: string }) =>
      login(username, password),
    onSuccess: (me) => {
      queryClient.clear();
      queryClient.setQueryData(["auth", "me"], me);
      setLoginError(null);
    },
    onError: (error) => {
      setLoginError(error instanceof Error ? error.message : "Login failed");
    },
  });

  const resetAuthenticatedState = useCallback(() => {
    queryClient.setQueryData(["auth", "me"], null);
  }, [queryClient]);

  const handleLogout = useCallback(() => {
    resetAuthenticatedState();
    queryClient.removeQueries({ predicate: ({ queryKey }) => queryKey[0] !== "auth" });
    void logout().catch(() => undefined);
  }, [queryClient, resetAuthenticatedState]);

  const handleUnauthorized = useCallback(() => {
    resetAuthenticatedState();
    queryClient.removeQueries({ predicate: ({ queryKey }) => queryKey[0] !== "auth" });
  }, [queryClient, resetAuthenticatedState]);

  if (meQuery.isLoading) {
    return <div className="auth-loading" role="status">Loading</div>;
  }

  if (!meQuery.data) {
    return (
      <LoginForm
        error={loginError}
        isSubmitting={loginMutation.isPending}
        onSubmit={(username, password) => loginMutation.mutate({ username, password })}
      />
    );
  }

  return (
    <OperatorConsole
      actor={meQuery.data}
      onLogout={handleLogout}
      onUnauthorized={handleUnauthorized}
    />
  );
}

function LoginForm({
  error,
  isSubmitting,
  onSubmit,
}: {
  error: string | null;
  isSubmitting: boolean;
  onSubmit: (username: string, password: string) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  return (
    <main className="login-shell">
      <form
        className="login-panel"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit(username, password);
        }}
      >
        <p className="eyebrow">Operator Console</p>
        <h1>Sign in</h1>
        <label>
          Username
          <input
            autoComplete="username"
            name="username"
            onChange={(event) => setUsername(event.target.value)}
            value={username}
          />
        </label>
        <label>
          Password
          <input
            autoComplete="current-password"
            name="password"
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            value={password}
          />
        </label>
        {error ? <p className="auth-error">{error}</p> : null}
        <button className="login-button" disabled={isSubmitting} type="submit">
          Sign in
        </button>
      </form>
    </main>
  );
}

function OperatorConsole({
  actor,
  onLogout,
  onUnauthorized,
}: {
  actor: Me;
  onLogout: () => void;
  onUnauthorized: () => void;
}) {
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
    retry: false,
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
    retry: false,
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

  const handleAuthExpired = useCallback(() => {
    applyThread([]);
    setActiveId(null);
    setActionError(null);
    onUnauthorized();
  }, [applyThread, onUnauthorized]);

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

  useEffect(() => {
    const errors = [
      sessionsQuery.error,
      artifactsQuery.error,
      traceQuery.error,
      createMutation.error,
    ];
    if (errors.some(isUnauthorized)) handleAuthExpired();
  }, [
    artifactsQuery.error,
    createMutation.error,
    handleAuthExpired,
    sessionsQuery.error,
    traceQuery.error,
  ]);

  const reconcileThread = useCallback(async (sessionId: string | null = activeIdRef.current) => {
    if (!sessionId) return;
    try {
      const messages = await getThread(sessionId);
      if (activeIdRef.current !== sessionId) return;
      applyThread(messages);
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    } catch (error) {
      if (isUnauthorized(error)) {
        handleAuthExpired();
        return;
      }
      if (isNotFound(error)) {
        await handleMissingSession(sessionId);
        return;
      }
      throw error;
    }
  }, [applyThread, handleAuthExpired, handleMissingSession, queryClient]);

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
        if (isUnauthorized(error)) {
          handleAuthExpired();
          return;
        }
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
    [clearBusyNoteTimeout, handleAuthExpired, handleMissingSession, markTurnStarted, queryClient],
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
        if (isUnauthorized(error)) {
          handleAuthExpired();
          return;
        }
        setActionError(error instanceof Error ? error.message : "Approval failed");
      } finally {
        setPendingApprovalId(null);
      }
    },
    [activeId, handleAuthExpired, reconcileThread],
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
        if (isUnauthorized(error)) {
          handleAuthExpired();
          return;
        }
        setActionError(error instanceof Error ? error.message : "Reject failed");
      } finally {
        setPendingApprovalId(null);
      }
    },
    [activeId, handleAuthExpired, reconcileThread],
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
        <div className="sidebar-auth-shell">
          <div className="auth-strip">
            <div className="auth-user">
              <span>{actor.username}</span>
              <small>{actor.role}</small>
            </div>
            <button className="auth-logout" type="button" onClick={onLogout}>
              Log out
            </button>
          </div>
          <SessionSidebar
            sessions={sessions}
            activeId={activeId}
            isCreating={createMutation.isPending}
            onSelect={handleSelectSession}
            onNew={handleNewSession}
          />
        </div>
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
