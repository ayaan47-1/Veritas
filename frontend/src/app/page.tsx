"use client";
import { useAuth } from "@clerk/nextjs";

export default function Home() {
  const { getToken } = useAuth();

  async function testBackend() {
    const token = await getToken();
    console.log("JWT:", token);

    const res = await fetch("http://localhost:8000/health");
    console.log("Health:", await res.json());
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-50 dark:bg-black">
      <div className="text-center">
        <h1 className="text-3xl font-semibold mb-6 dark:text-white">VeritasLayer</h1>
        <button
          onClick={testBackend}
          className="px-6 py-3 bg-black text-white rounded-full hover:bg-zinc-800 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
        >
          Test backend
        </button>
      </div>
    </div>
  );
}
