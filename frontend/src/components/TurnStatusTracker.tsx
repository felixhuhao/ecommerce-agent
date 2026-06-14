import { Check, Loader2, X } from "lucide-react";
import type { TurnProgressStep } from "../types";

interface TurnStatusTrackerProps {
  steps: TurnProgressStep[];
}

function StepIcon({ status }: { status: TurnProgressStep["status"] }) {
  if (status === "failed") return <X size={14} aria-hidden="true" />;
  if (status === "done") return <Check size={14} aria-hidden="true" />;
  return <Loader2 size={14} aria-hidden="true" />;
}

export function TurnStatusTracker({ steps }: TurnStatusTrackerProps) {
  if (steps.length === 0) return null;

  return (
    <section className="turn-status" aria-label="Turn status">
      <div className="turn-status-head">
        <span className="eyebrow">Working</span>
      </div>
      <ol className="turn-status-steps">
        {steps.map((step) => (
          <li className={`turn-status-step status-${step.status}`} key={step.stepId}>
            <StepIcon status={step.status} />
            <span>{step.label}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
