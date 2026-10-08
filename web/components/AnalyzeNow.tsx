"use client";

import { useState } from "react";
import { requestInFlight, type AnalysisRequest } from "@/lib/analysis";
import { userTimeOf } from "@/lib/market";

/**
 * "지금 분석" 단추 (docs/analysis.md 8장, 25.1018). 관심 종목에 넣고 참고 분석 작업을 깨운다.
 * 끝나면 텔레그램·알림 센터로 알린다 — 이 화면은 기다리지 않는다.
 */
export default function AnalyzeNow({ stockId, request }: { stockId: number; request: AnalysisRequest | null }) {
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [message, setMessage] = useState<string | null>(null);
  const 도는중 = state === "sent" || requestInFlight(request, new Date());

  async function send() {
    setState("sending");
    setMessage(null);
    try {
      const res = await fetch("/api/analysis/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stock_id: stockId }),
      });
      const body = (await res.json().catch(() => ({}))) as { errors?: string[]; state?: string };
      if (!res.ok) {
        setState("error");
        setMessage(body.errors?.join(" ") ?? `요청 실패(${res.status})`);
        return;
      }
      setState("sent");
      setMessage(body.state === "in_flight" ? "이미 분석 중입니다. 끝나면 알림이 갑니다" : "관심 종목에 넣고 분석을 시작했습니다. 몇 분 뒤 알림이 갑니다");
    } catch {
      setState("error");
      setMessage("요청을 보내지 못했습니다");
    }
  }

  return (
    <span className="flex flex-wrap items-center gap-2">
      {request?.status === "failed" && state === "idle" && (
        <span className="text-red-600">지난 분석 실패{request.note ? `: ${request.note}` : ""}</span>
      )}
      {request?.status === "done" && state === "idle" && request.finished_at && (
        <span>마지막 분석 {userTimeOf(request.finished_at)}</span>
      )}
      {message && <span className={state === "error" ? "text-red-600" : "text-emerald-700 dark:text-emerald-300"}>{message}</span>}
      <button
        type="button"
        onClick={send}
        disabled={state === "sending" || 도는중}
        className="rounded-md border border-violet-300 px-2 py-1 font-medium text-violet-700 disabled:opacity-50 dark:border-violet-800 dark:text-violet-300"
      >
        {state === "sending" ? "요청 중…" : 도는중 ? "분석 중" : "지금 분석"}
      </button>
    </span>
  );
}
