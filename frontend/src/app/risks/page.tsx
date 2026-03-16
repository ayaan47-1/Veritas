import { Suspense } from "react";

import RisksClientPage from "@/app/risks/RisksClientPage";

export default function RisksPage() {
  return (
    <Suspense fallback={<main className="min-h-screen bg-slate-50 px-6 py-10 text-sm text-slate-600">Loading risks...</main>}>
      <RisksClientPage />
    </Suspense>
  );
}
