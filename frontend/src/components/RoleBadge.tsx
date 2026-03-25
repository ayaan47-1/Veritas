type Role = "admin" | "reviewer" | "viewer";

const ROLE_STYLE: Record<Role, string> = {
  admin: "bg-purple-100 text-purple-800 border-purple-300",
  reviewer: "bg-blue-100 text-blue-800 border-blue-300",
  viewer: "bg-slate-100 text-slate-700 border-slate-300",
};

export default function RoleBadge({ role }: { role: Role }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold capitalize tracking-wide ${ROLE_STYLE[role]}`}
    >
      {role}
    </span>
  );
}
