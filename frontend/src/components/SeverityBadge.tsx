type Severity = "low" | "medium" | "high" | "critical";

const SEVERITY_STYLE: Record<Severity, { background: string; color: string; borderColor: string }> = {
  low: {
    background: "var(--bg-subtle)",
    color: "var(--text-secondary)",
    borderColor: "var(--border)",
  },
  medium: {
    background: "var(--accent-subtle)",
    color: "var(--accent)",
    borderColor: "var(--accent)",
  },
  high: {
    background: "var(--warning-subtle)",
    color: "var(--warning)",
    borderColor: "var(--warning)",
  },
  critical: {
    background: "var(--danger-subtle)",
    color: "var(--danger)",
    borderColor: "var(--danger)",
  },
};

export default function SeverityBadge({
  severity,
  llmSeverity,
}: {
  severity: Severity;
  llmSeverity?: Severity | null;
}) {
  const effectiveSeverity = llmSeverity ?? severity;
  const isRevised = llmSeverity != null && llmSeverity !== severity;

  return (
    <span
      style={SEVERITY_STYLE[effectiveSeverity]}
      className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium uppercase tracking-wide"
      title={isRevised ? `Revised by LLM from ${severity}` : undefined}
    >
      {effectiveSeverity}
      {isRevised ? <span className="text-[0.6rem] opacity-60">*</span> : null}
    </span>
  );
}
