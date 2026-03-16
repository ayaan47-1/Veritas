type Status = "needs_review" | "confirmed" | "rejected";

const STATUS_STYLE: Record<Status, string> = {
  needs_review: "bg-amber-100 text-amber-800 border-amber-300",
  confirmed: "bg-emerald-100 text-emerald-800 border-emerald-300",
  rejected: "bg-slate-200 text-slate-700 border-slate-300",
};

const STATUS_LABEL: Record<Status, string> = {
  needs_review: "Needs Review",
  confirmed: "Confirmed",
  rejected: "Rejected",
};

export default function StatusBadge({ status }: { status: Status }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold tracking-wide ${STATUS_STYLE[status]}`}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}
