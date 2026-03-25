import { Suspense } from "react";

import ConfigClientPage from "@/app/admin/config/ConfigClientPage";

export default function AdminConfigPage() {
  return (
    <Suspense
      fallback={
        <main className="min-h-screen bg-slate-50 px-6 py-10 text-sm text-slate-600">
          Loading config...
        </main>
      }
    >
      <ConfigClientPage />
    </Suspense>
  );
}
