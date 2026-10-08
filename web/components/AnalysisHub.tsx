"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { FACTOR_NAME, VERDICT_STYLE, type HubRow, type Radar, type VerdictKey } from "@/lib/analysis";

type Group = { key: VerdictKey; label: string; rows: HubRow[] };

/** 오늘 의견 모아보기 (docs/analysis.md). 누르면 종목 상세의 분석 카드로 간다 */
export default function AnalysisHub() {
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [radars, setRadars] = useState<Record<string, Radar>>({});
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
        setRadars(body.radars ?? {});
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
  return (
    <div className="space-y-4">
      {Object.entries(radars).map(([market, r]) => <RadarBlock key={market} market={market} r={r} />)}
      {groups.length === 0 && (
        <p className="py-4 text-sm text-slate-500">오늘 보유 점검·매수 검토·보유 유지·참고 분석 종목이 없습니다. 위에서 종목을 찾아 보세요(유니버스 밖 종목은 종목 화면의 &quot;지금 분석&quot;).</p>
      )}
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

/** 레이더 (docs/analysis.md 20장, 25.1043) — 배치가 시장마다 만든 세 목록을 그린다 */
function RadarBlock({ market, r }: { market: string; r: Radar }) {
  const pct = (x: number) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
  const 칸 = (title: string, note: string, items: Array<{ stock_id: number; name: string; ticker: string; right: string }>) =>
    items.length > 0 && (
      <div className="rounded-lg border border-slate-200 p-2.5 dark:border-slate-800">
        <p className="text-xs font-semibold text-slate-600 dark:text-slate-300">{title}</p>
        <p className="mb-1 text-[11px] text-slate-400">{note}</p>
        <ul className="space-y-0.5 text-xs">
          {items.map((x) => (
            <li key={x.stock_id} className="flex items-baseline justify-between gap-2">
              <Link href={`/stocks/${x.stock_id}#verdict`} className="truncate text-sky-700 hover:underline dark:text-sky-300">
                {x.name} <span className="text-slate-400">{x.ticker}</span>
              </Link>
              <span className="shrink-0 text-slate-600 dark:text-slate-300">{x.right}</span>
            </li>
          ))}
        </ul>
      </div>
    );
  return (
    <section>
      <h2 className="mb-1 text-sm font-semibold">
        레이더 — {market === "KR" ? "국내" : "미국"} <span className="font-normal text-slate-400">기준 {r.as_of}</span>
      </h2>
      <div className="grid gap-2 sm:grid-cols-3">
        {칸("가격 기준까지 가까운 종목", "아직 충족 안 된 신호 판정표 기준을 가격으로 푼 값까지의 거리",
          r.near.map((x) => ({ ...x, right: `${pct(x.dist)} · ${x.label.replace(/ \(.*\)$/, "")}` })))}
        {칸("4주 동안 점수가 가장 많이 오른 종목", "종합 점수 변화와 가장 많이 오른 팩터",
          r.rising.map((x) => ({ ...x, right: `${x.delta >= 0 ? "+" : ""}${x.delta.toFixed(1)}${x.up ? ` · ${FACTOR_NAME[x.up] ?? x.up}` : ""}` })))}
        {칸("네 눈이 모두 오름", "1년 예상의 눈이 셋 이상이고 모두 오름 — 가장 낮은 눈이 높은 순",
          r.eyes.map((x) => ({ ...x, right: `${pct(x.low)} ~ ${pct(x.high)}` })))}
      </div>
    </section>
  );
}
