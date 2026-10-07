"use client";

import { useEffect, useState } from "react";
import type { EnvVerdict } from "@/lib/envCheck";
import { readJson } from "@/lib/http";

/**
 * 환경변수 점검 칸 (docs/infra.md 25.842). Vercel 은 값을 가려 무엇을 넣었는지 볼 수 없어, 서버가 **들어 있는지·모양이 맞는지만** 알려 준다.
 * 값은 서버에서도 내보내지 않는다. DB 를 읽지 않으므로 DB 가 막힌 날에도 보인다.
 */
export default function EnvCheck() {
  const [rows, setRows] = useState<EnvVerdict[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const r = await readJson<{ env_check: EnvVerdict[] }>("/api/status/env");
      if (r.ok && r.data) setRows(r.data.env_check);
      else setError(r.error ?? "읽지 못했습니다");
    })();
  }, []);

  const 문제 = rows?.filter((r) => r.problem) ?? [];
  return (
    <section className="mb-4">
      <h2 className="mb-1 text-sm font-semibold">환경변수 점검 (Vercel)</h2>
      <p className="mb-1 text-xs text-slate-500">
        값은 보여 주지 않습니다 — 들어 있는지, 길이·모양이 맞는지만 봅니다. 고친 뒤에는 Vercel 에서 다시 배포해야 반영됩니다.
      </p>
      {error ? <p className="text-xs text-rose-700 dark:text-rose-300">점검을 읽지 못했습니다: {error}</p> : null}
      {rows ? (
        <>
          <p className={`mb-1 text-xs ${문제.length ? "text-amber-800 dark:text-amber-200" : "text-emerald-700 dark:text-emerald-300"}`}>
            {문제.length ? `고칠 것 ${문제.length}개` : "모두 들어 있고 모양이 맞습니다"}
          </p>
          <ul className="flex flex-col gap-1 text-xs">
            {rows.map((r) => (
              <li key={r.name} className="flex flex-wrap items-baseline gap-x-2 rounded-xl border border-slate-200 px-3 py-1.5 dark:border-slate-800">
                <span className="font-mono">{r.name}</span>
                <span className="text-slate-500">{r.purpose}{r.required ? "" : " · 선택"}</span>
                <span className="ml-auto">
                  {r.problem ? (
                    <span className="rounded bg-rose-50 px-1.5 py-0.5 text-[11px] text-rose-700 dark:bg-rose-950 dark:text-rose-300">{r.problem}</span>
                  ) : r.present ? (
                    <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">들어 있음</span>
                  ) : (
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">비어 있음(선택)</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}
