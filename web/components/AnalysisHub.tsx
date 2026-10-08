"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { VERDICT_STYLE, type HubRow, type VerdictKey } from "@/lib/analysis";

type Group = { key: VerdictKey; label: string; rows: HubRow[] };

/** 오늘 의견 모아보기 (docs/analysis.md). 누르면 종목 상세의 분석 카드로 간다 */
export default function AnalysisHub() {
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch("/api/analysis");
        const body = await r.json();
        if (!alive) return;
        if (!r.ok) return setError(body.errors?.join(", ") ?? "조회에 실패했습니다");
        setGroups(body.groups ?? []);
      } catch {
        if (alive) setError("조회에 실패했습니다");
      }
    })();
    return () => {
      alive = false;
    };
  }, []);
  if (error) return <p className="text-sm text-rose-600">{error}</p>;
  if (!groups) return <p className="py-4 text-sm text-slate-500">불러오는 중…</p>;
  if (groups.length === 0) {
    return <p className="py-4 text-sm text-slate-500">오늘 보유 점검·매수 검토·보유 유지·참고 분석 종목이 없습니다. 위에서 종목을 찾아 보세요(유니버스 밖 종목은 종목 화면의 "지금 분석").</p>;
  }
  return (
    <div className="space-y-4">
      {groups.map((g) => (
        <section key={g.key}>
          <h2 className="mb-1 text-sm font-semibold">{g.label} <span className="font-normal text-slate-400">{g.rows.length}</span></h2>
          <ul className="space-y-1.5">
            {g.rows.map((r) => (
              <li key={r.stock_id}>
                <Link href={`/stocks/${r.stock_id}#verdict`} className={`block rounded-lg border px-2.5 py-2 text-sm ${VERDICT_STYLE[r.verdict]}`}>
                  <span className="font-medium">{r.name}</span> <span className="text-xs opacity-70">{r.ticker}</span>
                  <span className="block text-xs">{r.headline.replace(/^[^—]+—\s*/, "")}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
