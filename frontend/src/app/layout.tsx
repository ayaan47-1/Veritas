import type { Metadata } from "next";
import Link from "next/link";
import { Instrument_Serif, Jost, JetBrains_Mono } from "next/font/google";
import { ClerkProvider, SignInButton, SignUpButton, UserButton, Show } from "@clerk/nextjs";
import NavLinks from "@/components/NavLinks";
import NotificationBell from "@/components/NotificationBell";
import ThemeToggle from "@/components/ThemeToggle";
import "./globals.css";

const instrumentSerif = Instrument_Serif({
  weight: "400",
  subsets: ["latin"],
  variable: "--font-instrument-serif",
  display: "swap",
});

const jost = Jost({
  weight: ["300", "400", "500", "600"],
  subsets: ["latin"],
  variable: "--font-jost",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  weight: ["400", "500"],
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Veritas",
  description: "AI Operational Intelligence Layer",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${instrumentSerif.variable} ${jost.variable} ${jetbrainsMono.variable}`}
    >
      <body className="antialiased">
        <ClerkProvider>
          <header className="sticky top-0 z-40 border-b border-border bg-surface/80 backdrop-blur-md">
            <div className="mx-auto flex h-11 max-w-7xl items-center justify-between px-6">
              <div className="flex items-center gap-6">
                <Link href="/" className="font-serif text-base uppercase tracking-widest text-text-primary">
                  Veritas
                </Link>
                <NavLinks />
              </div>
              <div className="flex items-center gap-1">
                <ThemeToggle />
                <Show when="signed-out">
                  <SignInButton />
                  <SignUpButton />
                </Show>
                <Show when="signed-in">
                  <NotificationBell />
                  <UserButton />
                </Show>
              </div>
            </div>
          </header>
          <Show when="signed-out">
            <div className="border-b border-accent-subtle bg-accent-subtle px-6 py-2 text-center text-sm font-medium text-accent">
              Sign in to load assets and review queues.
            </div>
          </Show>
          {children}
        </ClerkProvider>
      </body>
    </html>
  );
}
