import { Suspense } from "react";

import ObligationsClientPage from "@/app/obligations/ObligationsClientPage";

export default function ObligationsPage() {
  return (
    <Suspense fallback={<main className="min-h-screen bg-slate-50 px-6 py-10 text-sm text-slate-600">Loading obligations...</main>}>
      <ObligationsClientPage />
    </Suspense>
  );
}
