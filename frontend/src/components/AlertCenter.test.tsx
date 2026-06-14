import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AlertCenter } from "./AlertCenter";
import type { Alert } from "../types";

const alert: Alert = {
  alert_id: "a1",
  check_name: "low_stock",
  dedupe_key: "low_stock:SKU-9",
  title: "Low stock: Power Bank",
  severity: "warning",
  status: "open",
  metric: "inventory",
  value: 12,
  threshold: 50,
  entities: { sku: "SKU-9" },
  cause: "Recent orders consumed available stock.",
  grounding: {
    authority: "authoritative",
    diagnostic: null,
    sources: [
      {
        source_id: "detection:inventory_low_stock:SKU-9",
        tool_name: "inventory_low_stock",
        args_summary: "threshold=50",
        result_summary: "12 units",
        evidence: '{"sku":"SKU-9","quantity":12}',
      },
    ],
  },
  created_at: "2026-06-14T00:00:00+00:00",
  updated_at: "2026-06-14T00:00:00+00:00",
  acknowledged_at: null,
  acknowledged_by: null,
};

describe("AlertCenter", () => {
  it("renders alert badge and inline sources", () => {
    render(
      <AlertCenter
        alerts={[alert]}
        isLoading={false}
        isError={false}
        isRunning={false}
        isAcknowledgingId={null}
        actionError={null}
        runNote={null}
        onRun={vi.fn()}
        onAcknowledge={vi.fn()}
      />,
    );

    expect(screen.getByText("Low stock: Power Bank")).toBeInTheDocument();
    expect(screen.getByText("Authoritative")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Sources (1)"));
    expect(screen.getByText("inventory_low_stock")).toBeInTheDocument();
    expect(screen.getByText('{"sku":"SKU-9","quantity":12}')).toBeInTheDocument();
  });

  it("runs and acknowledges alerts", () => {
    const onRun = vi.fn();
    const onAcknowledge = vi.fn();
    render(
      <AlertCenter
        alerts={[alert]}
        isLoading={false}
        isError={false}
        isRunning={false}
        isAcknowledgingId={null}
        actionError={null}
        runNote={null}
        onRun={onRun}
        onAcknowledge={onAcknowledge}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));

    expect(onRun).toHaveBeenCalledOnce();
    expect(onAcknowledge).toHaveBeenCalledWith("a1");
  });
});

