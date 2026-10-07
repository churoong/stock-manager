"use client";

import { useState } from "react";
import Footer from "@/components/Footer";

export default function LoginPage() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [locked, setLocked] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });

      if (response.ok) {
        window.location.href = "/settings";
        return;
      }

      const body = await response.json().catch(() => ({}));
      setLocked(response.status === 429);
      setError(body.error ?? "로그인에 실패했습니다");
      setPassword("");
    } catch {
      setLocked(false);
      setError("서버에 연결하지 못했습니다");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col">
      <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 py-12">
        <h1 className="text-xl font-semibold">주식 분석·매매관리</h1>
        <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
          개인용입니다. 본인만 들어올 수 있습니다.
        </p>

        <form onSubmit={submit} className="mt-8 space-y-4">
          <div>
            <label
              htmlFor="password"
              className="block text-sm font-medium text-slate-700 dark:text-slate-300"
            >
              비밀번호
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-base outline-none focus:border-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:focus:border-slate-300"
              required
            />
          </div>

          {error && (
            <div
              role="alert"
              className={`rounded-lg px-3 py-2 text-sm ${
                locked
                  ? "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                  : "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300"
              }`}
            >
              <p>{error}</p>
              {locked && (
                <p className="mt-1 text-xs">
                  본인이 시도한 게 아니라면 비밀번호를 바꾸는 것이 좋습니다.
                </p>
              )}
            </div>
          )}

          <button
            type="submit"
            disabled={busy || locked || password.length === 0}
            className="w-full rounded-lg bg-slate-900 px-4 py-2.5 text-base font-medium text-white disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900"
          >
            {busy ? "확인 중" : "들어가기"}
          </button>
        </form>
      </main>
      <Footer />
    </div>
  );
}
