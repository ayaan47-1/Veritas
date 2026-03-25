import { Suspense } from "react";

import UsersClientPage from "@/app/admin/users/UsersClientPage";

export default function AdminUsersPage() {
  return (
    <Suspense
      fallback={
        <main className="min-h-screen bg-slate-50 px-6 py-10 text-sm text-slate-600">
          Loading users...
        </main>
      }
    >
      <UsersClientPage />
    </Suspense>
  );
}
