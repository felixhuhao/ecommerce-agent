import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "./components/AppShell";
import { ApprovalWorkspace } from "./components/ApprovalWorkspace";
import { ConversationView } from "./components/ConversationView";
import { HealthPanel } from "./components/HealthPanel";
import { SessionSidebar } from "./components/SessionSidebar";
import {
  approveApproval,
  createSession,
  getHealth,
  getMcpHealth,
  getThread,
  listSessions,
  postMessage,
  rejectApproval,
} from "./api/client";
import { useSessionStream } from "./api/useSessionStream";
import { performApprove, performReject } from "./state/approvalActions";
import { foldApprovals } from "./state/approvals";
import { performSend } from "./state/sendMessage";

export function App() {
  const queryClient = useQueryClient();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [busyNote, setBusyNote] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingApprovalId, setPendingApprovalId] = useState<string | null>(null);

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

  const sessions = sessionsQuery.data ?? [];

  useEffect(() => {
    if (!activeId && sessions.length > 0) setActiveId(sessions[0].session_id);
  }, [activeId, sessions]);

  const { state, markTurnStarted, applyThread } = useSessionStream(activeId);
  const approvals = useMemo(() => foldApprovals(state.messages), [state.messages]);

  const createMutation = useMutation({
    mutationFn: createSession,
    onSuccess: async ({ session_id }) => {
      setActiveId(session_id);
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  const reconcileThread = useCallback(async () => {
    if (!activeId) return;
    const messages = await getThread(activeId);
    applyThread(messages);
    await queryClient.invalidateQueries({ queryKey: ["sessions"] });
  }, [activeId, applyThread, queryClient]);

  const handleSend = useCallback(
    async (message: string) => {
      if (!activeId) return;
      setActionError(null);
      setBusyNote(null);
      try {
        const result = await performSend(activeId, message, { postMessage }, markTurnStarted);
        if (result.busy) {
          setBusyNote("A turn is already running.");
          window.setTimeout(() => setBusyNote(null), 3500);
        }
        await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      } catch (error) {
        setActionError(error instanceof Error ? error.message : "Send failed");
      }
    },
    [activeId, markTurnStarted, queryClient],
  );

  const handleApprove = useCallback(
    async (approvalId: string) => {
      if (!activeId) return;
      setPendingApprovalId(approvalId);
      setActionError(null);
      try {
        await performApprove(activeId, approvalId, { approveApproval }, reconcileThread);
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
      if (!activeId) return;
      setPendingApprovalId(approvalId);
      setActionError(null);
      try {
        await performReject(activeId, approvalId, reason, { rejectApproval }, reconcileThread);
      } catch (error) {
        setActionError(error instanceof Error ? error.message : "Reject failed");
      } finally {
        setPendingApprovalId(null);
      }
    },
    [activeId, reconcileThread],
  );

  return (
    <AppShell
      sidebar={
        <SessionSidebar
          sessions={sessions}
          activeId={activeId}
          isCreating={createMutation.isPending}
          onSelect={setActiveId}
          onNew={() => createMutation.mutate()}
        />
      }
      conversation={
        <ConversationView
          messages={state.messages}
          provisionalAnswer={state.inFlightTurnId ? state.tokenBuffer : null}
          activeTool={state.activeTool}
          composerDisabled={!activeId || state.inFlightTurnId !== null}
          busyNote={busyNote}
          error={state.error}
          onSend={handleSend}
        />
      }
      rail={
        <>
          <ApprovalWorkspace
            approvals={approvals}
            pendingApprovalId={pendingApprovalId}
            actionError={actionError}
            onApprove={handleApprove}
            onReject={handleReject}
          />
          <HealthPanel
            health={healthQuery.data}
            mcp={mcpQuery.data}
            healthUnavailable={healthQuery.isError || healthQuery.isRefetchError}
            mcpUnavailable={mcpQuery.isError || mcpQuery.isRefetchError}
          />
        </>
      }
    />
  );
}
