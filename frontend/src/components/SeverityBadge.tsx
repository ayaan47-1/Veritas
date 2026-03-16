type Severity = "low" | "medium" | "high" | "critical";

const SEVERITY_STYLE: Record<Severity, string> = {
  low: "bg-sky-100 text-sky-800 border-sky-300",
  medium: "bg-amber-100 text-amber-800 border-amber-300",
  high: "bg-orange-100 text-orange-800 border-orange-300",
  critical: "bg-rose-100 text-rose-800 border-rose-300",
};

export default function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${SEVERITY_STYLE[severity]}`}>
      {severity.toUpperCase()}
    </span>
  );
}
