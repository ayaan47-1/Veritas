"use client";

import { Show } from "@clerk/nextjs";
import { usePathname } from "next/navigation";

export default function SignedOutBanner() {
  const pathname = usePathname();
  if (pathname === "/") return null;
  return (
    <Show when="signed-out">
      <div className="border-b border-accent-subtle bg-accent-subtle px-6 py-2 text-center text-sm font-medium text-accent">
        Sign in to load assets and review queues.
      </div>
    </Show>
  );
}
