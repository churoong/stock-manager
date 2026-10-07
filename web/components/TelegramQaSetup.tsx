"use client";

import { useState } from "react";

/** 텔레그램 질의응답 켜기·끄기 (docs/telegram-qa.md, 25.1004). 텔레그램에 웹훅을 걸고 푼다 — 누를 때만 */
export default function TelegramQaSetup() {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  async function run(action: "on" | "off") {
    setBusy(true);
    setMsg(null);
    try {
      const response = await fetch("/api/telegram/webhook-setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const body = await response.json().catch(() => ({}));
      setMsg(response.ok ? { ok: true, text: body.message ?? "됐습니다" } : { ok: false, text: body.error ?? "실패했습니다" });
    } catch {
      setMsg({ ok: false, text: "서버에 연결하지 못했습니다" });
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="rounded-xl border border-slate-200 p-3 text-sm dark:border-slate-800">
      <h2 className="mb-1 font-medium">텔레그램으로 물어보기</h2>
      <p className="mb-2 text-xs leading-relaxed text-slate-500">
        켜면 봇에게 종목 이름이나 티커를 보내 점수·신호·보유·매도 플래그를 받습니다(/보유, /알림 도 됩니다). 본인 대화방에만
        답하고, 읽기만 합니다. Vercel 환경변수 TELEGRAM_WEBHOOK_SECRET 이 있어야 켜집니다.
      </p>
      <div className="flex gap-2">
        <button type="button" disabled={busy} onClick={() => void run("on")}
          className="rounded-lg border border-slate-300 px-3 py-1.5 disabled:opacity-40 dark:border-slate-700">켜기</button>
        <button type="button" disabled={busy} onClick={() => void run("off")}
          className="rounded-lg border border-slate-300 px-3 py-1.5 disabled:opacity-40 dark:border-slate-700">끄기</button>
      </div>
      {msg ? <p className={`mt-2 text-xs ${msg.ok ? "text-emerald-700 dark:text-emerald-300" : "text-rose-600"}`}>{msg.text}</p> : null}
    </section>
  );
}
