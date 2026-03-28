type Role = "admin" | "reviewer" | "viewer";

const ROLE_STYLE: Record<Role, { background: string; color: string; borderColor: string }> = {
  admin: {
    background: "var(--accent-subtle)",
    color: "var(--accent)",
    borderColor: "var(--accent)",
  },
  reviewer: {
    background: "var(--bg-subtle)",
    color: "var(--text-primary)",
    borderColor: "var(--border-strong)",
  },
  viewer: {
    background: "var(--bg-subtle)",
    color: "var(--text-tertiary)",
    borderColor: "var(--border)",
  },
};

export default function RoleBadge({ role }: { role: Role }) {
  return (
    <span
      style={ROLE_STYLE[role]}
      className="inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium capitalize tracking-wide"
    >
      {role}
    </span>
  );
}
