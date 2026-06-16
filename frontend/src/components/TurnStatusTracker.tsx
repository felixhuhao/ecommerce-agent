import { Check, Loader2, X } from "lucide-react";
import type { TurnProgressStep } from "../types";

interface TurnStatusTrackerProps {
  steps: TurnProgressStep[];
}

interface DisplayStep {
  key: string;
  label: string;
  status: TurnProgressStep["status"];
  count: number;
}

function statusRank(status: TurnProgressStep["status"]) {
  if (status === "running") return 4;
  if (status === "failed") return 3;
  if (status === "pending") return 2;
  return 1;
}

function displaySteps(steps: TurnProgressStep[]): DisplayStep[] {
  const byLabel = new Map<string, DisplayStep>();
  for (const step of steps) {
    const existing = byLabel.get(step.label);
    if (!existing) {
      byLabel.set(step.label, {
        key: step.stepId,
        label: step.label,
        status: step.status,
        count: 1,
      });
      continue;
    }
    existing.count += 1;
    if (statusRank(step.status) > statusRank(existing.status)) {
      existing.status = step.status;
    }
  }
  return [...byLabel.values()];
}

function StepIcon({ status }: { status: TurnProgressStep["status"] }) {
  if (status === "failed") return <X size={14} aria-hidden="true" />;
  if (status === "done") return <Check size={14} aria-hidden="true" />;
  return <Loader2 size={14} aria-hidden="true" />;
}

export function TurnStatusTracker({ steps }: TurnStatusTrackerProps) {
  if (steps.length === 0) return null;
  const compactSteps = displaySteps(steps);
  const isRunning = compactSteps.some((step) => step.status === "running");

  return (
    <section className="turn-status" aria-label="Turn status">
      <div className="turn-status-head">
        <span className="eyebrow">Working</span>
        <span
          className={`turn-status-live ${isRunning ? "is-running" : "is-idle"}`}
          aria-hidden="true"
        />
      </div>
      <ol className="turn-status-steps">
        {compactSteps.map((step) => (
          <li className={`turn-status-step status-${step.status}`} key={step.key}>
            <span className="turn-status-node">
              <StepIcon status={step.status} />
            </span>
            <span className="turn-status-label">
              {step.label}
              {step.count > 1 ? (
                <span className="turn-status-count">x{step.count}</span>
              ) : null}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
