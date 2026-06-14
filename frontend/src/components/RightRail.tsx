import type { ReactNode } from "react";

export type RailTab = "alerts" | "approvals" | "artifacts" | "trace" | "health";

interface RightRailProps {
  activeTab: RailTab;
  onTabChange: (tab: RailTab) => void;
  approvalCount: number;
  alertCount: number;
  alerts: ReactNode;
  approvals: ReactNode;
  artifacts: ReactNode;
  trace: ReactNode;
  health: ReactNode;
}

const TABS: { id: RailTab; label: string }[] = [
  { id: "alerts", label: "Alerts" },
  { id: "approvals", label: "Approvals" },
  { id: "artifacts", label: "Artifacts" },
  { id: "trace", label: "Trace" },
  { id: "health", label: "Health" },
];

export function RightRail({
  activeTab,
  onTabChange,
  approvalCount,
  alertCount,
  alerts,
  approvals,
  artifacts,
  trace,
  health,
}: RightRailProps) {
  const panels: Record<RailTab, ReactNode> = { alerts, approvals, artifacts, trace, health };
  return (
    <div className="rail-tabbed">
      <div className="rail-tabs" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            className={`rail-tab ${activeTab === tab.id ? "is-active" : ""}`}
            onClick={() => onTabChange(tab.id)}
          >
            {tab.label}
            {tab.id === "alerts" && alertCount > 0 ? (
              <span className="rail-tab-badge">{alertCount}</span>
            ) : null}
            {tab.id === "approvals" && approvalCount > 0 ? (
              <span className="rail-tab-badge">{approvalCount}</span>
            ) : null}
          </button>
        ))}
      </div>
      <div className="rail-tab-panel">{panels[activeTab]}</div>
    </div>
  );
}
