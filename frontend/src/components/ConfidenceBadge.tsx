import type { Authority } from "../types";

const LABELS: Record<Exclude<Authority, "not_applicable">, string> = {
  authoritative: "Authoritative",
  derived: "Derived",
  unverified: "Unverified",
};

const TITLES: Record<Exclude<Authority, "not_applicable">, string> = {
  authoritative: "Headline numbers came from the canonical backend.",
  derived: "Numbers were computed from sandbox analysis evidence.",
  unverified: "Numeric claims were not backed by an authority tool.",
};

interface ConfidenceBadgeProps {
  authority: Authority;
}

export function ConfidenceBadge({ authority }: ConfidenceBadgeProps) {
  if (authority === "not_applicable") return null;

  return (
    <span className={`confidence-badge confidence-${authority}`} title={TITLES[authority]}>
      {LABELS[authority]}
    </span>
  );
}
