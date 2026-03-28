type Status = "needs_review" | "confirmed" | "rejected";

const STATUS_STYLE: Record<Status, { background: string; color: string; borderColor: string }> = {
  needs_review: {
    background: "var(--accent-subtle)",   /* light yellow */
    color: "var(--accent)",
    borderColor: "var(--accent)",
  },
  confirmed: {
    background: "var(--success-subtle)",  /* light green */
    color: "var(--success)",
    borderColor: "var(--success)",
  },
  rejected: {
    background: "var(--danger-subtle)",   /* light red */
    color: "var(--danger)",
    borderColor: "var(--danger)",
  },
};

const STATUS_LABEL: Record<Status, string> = {
  needs_review: "Needs Review",
  confirmed: "Confirmed",
  rejected: "Rejected",
};

export default function StatusBadge({ status }: { status: Status }) {
  return (
    <span
      style={STATUS_STYLE[status]}
      className="inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium tracking-wide"
    >
      {STATUS_LABEL[status]}
    </span>
  );
}
