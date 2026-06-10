import type { SendResult } from "../api/client";

interface SendApi {
  postMessage: (sessionId: string, message: string) => Promise<SendResult>;
}

export async function performSend(
  sessionId: string,
  message: string,
  api: SendApi,
  markTurnStarted: (turnId: string) => void,
): Promise<{ busy: boolean }> {
  const result = await api.postMessage(sessionId, message);
  if (result.turnInProgress) return { busy: true };
  markTurnStarted(result.turnId);
  return { busy: false };
}
