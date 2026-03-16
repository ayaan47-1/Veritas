import type { Metadata } from "next";
import Link from "next/link";
import { ClerkProvider, SignInButton, SignUpButton, UserButton, Show } from "@clerk/nextjs";
import "./globals.css";

export const metadata: Metadata = {
  title: "VeritasLayer",
  description: "AI Operational Intelligence Layer",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">
        <ClerkProvider>
          <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/90 px-6 py-3 backdrop-blur">
            <div className="mx-auto flex max-w-7xl items-center justify-between">
              <div className="flex items-center gap-3">
                <Link href="/" className="text-sm font-semibold uppercase tracking-[0.2em] text-cyan-800">
                  VeritasLayer
                </Link>
                <nav className="hidden items-center gap-2 text-sm text-slate-600 md:flex">
                  <Link href="/" className="rounded-full px-3 py-1 hover:bg-slate-100">
                    Assets
                  </Link>
                  <Link href="/obligations" className="rounded-full px-3 py-1 hover:bg-slate-100">
                    Obligations
                  </Link>
                  <Link href="/risks" className="rounded-full px-3 py-1 hover:bg-slate-100">
                    Risks
                  </Link>
                </nav>
              </div>

              <div className="flex items-center gap-3">
                <Show when="signed-out">
                  <SignInButton />
                  <SignUpButton />
                </Show>
                <Show when="signed-in">
                  <UserButton />
                </Show>
              </div>
            </div>
          </header>
          <div>
            <Show when="signed-out">
              <div className="border-b border-amber-300 bg-amber-100 px-6 py-2 text-center text-sm font-medium text-amber-900">
                Sign in to load assets and review queues.
              </div>
            </Show>
          </div>
          {children}
        </ClerkProvider>
      </body>
    </html>
  );
}
