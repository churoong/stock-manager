"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import EquityChart from "@/components/EquityChart";
import { bodyOf, readJson } from "@/lib/http";
import { JOURNEY_LIMIT, journeyCut, journeyEvents, weightSeries, type HoldingPoint, type JourneyRaw, type LabAttribution } from "@/lib/lab";
import { ATTRIBUTION_MIN_SAMPLE, attrCellText, entryCellText } from "@/lib/paper";

/**
 * 연구실 (docs/desk.md 5장, docs/infra.md 25.1506) — 전략을 고치려고 보는 자료. 값은 배치가 적은 그대로다.
 */
const CARD = "rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900";

const 칸: Array<[string, (a: NonNullable<LabAttribution["attribution"]>) => string]> = [
  ["낸 새 후보", (a) => attrCellText(a.selection)],
  ["이미 든 종목 추가 매수", (a) => attrCellText(a.add_on)],
  ["안 낸 새 후보 (오르면 안 낸 것이 비용)", (a) => attrCellText(a.skipped)],
  ["진입(시장가 체결가→종가)", (a) => entryCellText(a.entry)],
  ["안 찬 회차 (오르면 놓친 기회)", (a) => attrCellText(a.missed)],
  ["판 뒤 (음수면 판 것이 옳았다)", (a) => attrCellText(a.sold)],
  ["플래그 무시하고 든 뒤", (a) => attrCellText(a.held_flag)],
  ["막은 매수 뒤 (오르면 막은 것이 비용)", (a) => attrCellText(a.blocked)],
  ["막은 매도 뒤 (내리면 막은 것이 비용)", (a) => attrCellText(a.blocked_sell)],
  [
    "가상 대조 분위",
    (a) => (a.virtual?.n && a.virtual.avg_pct != null ? `${a.virtual.avg_pct.toFixed(0)} (n=${a.virtual.n}${a.virtual.n < ATTRIBUTION_MIN_SAMPLE ? ", 참고" : ""})` : "표본 없음"),
  ],
];

function Journey() {
  const [ticker, setTicker] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  type Stock = { id: number; ticker: string; name: string; country: string; market?: string; status?: string };
  const [result, setResult] = useState<{ stock: Stock | null; raw?: JourneyRaw; choices?: Stock[] } | null>(null);
  // 늦게 온 앞 요청의 응답이 뒤 요청의 결과를 덮지 않게 — 마지막 요청 번호만 받는다(25.1509, 검토 4)
  const 번호 = useRef(0);

  async function look(q: string) {
    const 나 = ++번호.current;
    setBusy(true);
    setError(null);
    const r = await readJson<NonNullable<typeof result>>(`/api/lab/journey?${q}`);
    const body = bodyOf<NonNullable<typeof result>>(r);
    if (나 !== 번호.current) return;
    setBusy(false);
    if (!r.ok || body.errors?.length) {
      setResult(null); // 앞 종목의 여정이 새 오류 아래 남지 않게
      setError(body.errors?.join(" · ") || r.error || "읽지 못했습니다");
    } else setResult(body);
  }

  const 자름 = result?.raw ? journeyCut(result.raw) : null;
  const 사건 = result?.raw ? journeyEvents(result.raw).filter((e) => !자름 || e.day >= 자름) : [];
  return (
    <section className={CARD}>
      <h2 className="text-base font-semibold">종목 여정</h2>
      <p className="mt-1 text-xs text-slate-500">
        한 종목이 추천 → 계좌의 결정(산 것·안 산 것과 그 까닭) → 주문 → 가상 체결 → KIS모의투자 주문을 어떻게 지나갔는지 날짜순으로 봅니다. 원장마다 최근 {JOURNEY_LIMIT}줄 —
        어느 원장이 꽉 차면 모든 원장을 그 원장의 가장 오래된 날에서 맞춰 자릅니다.
      </p>
      <form
        className="mt-2 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void look(`ticker=${encodeURIComponent(ticker.trim())}`);
        }}
      >
        <input
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          placeholder="티커 (예: 005930, AAPL)"
          className="w-48 rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950"
        />
        <button type="submit" disabled={busy || !ticker.trim()} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900">
          {busy ? "찾는 중…" : "보기"}
        </button>
      </form>
      {error ? <p className="mt-2 text-sm text-red-600">{error}</p> : null}
      {result?.choices ? (
        <p className="mt-2 text-sm">
          같은 티커가 여럿입니다:{" "}
          {result.choices.map((c) => (
            <button key={c.id} type="button" disabled={busy} className="mr-2 underline disabled:opacity-50" onClick={() => void look(`stock=${c.id}`)}>
              {c.name} ({[c.country, c.market, c.status].filter(Boolean).join(" · ")})
            </button>
          ))}
        </p>
      ) : null}
      {result && !result.choices && !result.stock ? <p className="mt-2 text-sm text-slate-500">그 티커의 종목이 없습니다.</p> : null}
      {result?.stock ? (
        <div className="mt-3 text-sm">
          <p>
            <b>{result.stock.name}</b> <span className="text-xs text-slate-500">{result.stock.ticker} · {result.stock.country}</span>{" "}
            <Link href={`/stocks/${result.stock.id}`} className="text-xs underline">
              종목 화면
            </Link>
          </p>
          {자름 ? <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">{자름} 이전은 잘림 — 원장마다 최근 {JOURNEY_LIMIT}줄이라 그보다 앞은 일부 원장만 남습니다</p> : null}
          {사건.length ? (
            <ul className="mt-2 divide-y divide-slate-100 dark:divide-slate-800">
              {사건.map((e, i) => (
                <li key={i} className="py-1">
                  <span className="text-xs text-slate-500">
                    {e.day} · {e.step}
                    {e.account ? ` · ${e.account}` : ""}
                  </span>{" "}
                  {e.text}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-slate-500">이 종목은 아직 추천·결정·주문 기록이 없습니다.</p>
          )}
        </div>
      ) : null}
    </section>
  );
}

export default function LabView() {
  const [data, setData] = useState<{ attribution: LabAttribution[]; holding: HoldingPoint[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void (async () => {
      const r = await readJson<{ attribution: LabAttribution[]; holding: HoldingPoint[] }>("/api/lab");
      const body = bodyOf<{ attribution: LabAttribution[]; holding: HoldingPoint[] }>(r);
      if (!r.ok || body.errors?.length) setError(body.errors?.join(" · ") || r.error || "연구실 자료를 읽지 못했습니다");
      else if (!Array.isArray(body.attribution) || !Array.isArray(body.holding)) setError("응답 꼴이 다름 — 새로 고침해 주세요");
      else setData({ attribution: body.attribution, holding: body.holding });
    })();
  }, []);

  /** 시장마다 마지막 실행 — 오류면 그 문장(25.1509, 검토 5) */
  const 마지막 = (m: "KR" | "US") => {
    const xs = (data?.holding ?? []).filter((p) => p.market === m);
    return xs.length ? xs[xs.length - 1] : null;
  };
  return (
    <div className="space-y-5">
      <section className={CARD}>
        <h2 className="text-base font-semibold">성과 분해 — 계좌끼리</h2>
        <p className="mt-1 text-xs text-slate-500">
          결정 뒤 20 거래일의 지수 대비 초과(%p). 낸 후보가 안 낸 새 후보보다 높으면 고르기가 통한 것, 판 뒤가 음수면 판 것이 옳았던 것. 가상 대조 분위는 산 종목이 같은 날 무작위 20종목 안 몇
          분위였나(무작위면 50). 표본 30건 미만은 참고 — 몇 달 차이는 대부분 운입니다.
        </p>
        {error ? <p className="mt-2 text-sm text-red-600">{error}</p> : null}
        {!data && !error ? <p className="mt-2 text-sm text-slate-500">불러오는 중…</p> : null}
        {data ? (
          data.attribution.some((a) => a.attribution) ? (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-[900px] text-xs">
                <thead className="text-slate-500">
                  <tr>
                    <th className="py-1 text-left">계좌</th>
                    {칸.map(([k]) => (
                      <th key={k} className="text-left">
                        {k}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {data.attribution.map((a) => (
                    <tr key={a.id}>
                      <td className="py-1 pr-2 font-semibold">{a.id}</td>
                      {칸.map(([k, f]) => (
                        <td key={k} className="pr-2">
                          {a.attribution ? f(a.attribution) : "-"}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="mt-2 text-sm text-slate-500">아직 성과가 찬 결정이 없습니다 — 결정 뒤 20 거래일이 지나야 채워집니다.</p>
          )
        ) : null}
      </section>

      <section className={CARD}>
        <h2 className="text-base font-semibold">지주사(H) 회사 가중치</h2>
        <p className="mt-1 text-xs text-slate-500">
          H 는 회사들(A·C·D·E·B)의 추천을 그 회사의 최근 성적으로 가중해 합칩니다(docs/company.md 10장). 표본이 적을수록 1 에 가깝게 눌립니다. 날마다 운용 요약에 적힌 값 — 운용 요약
          최근 120줄(배치가 돈 날).
        </p>
        {data && !data.holding.length ? <p className="mt-2 text-sm text-slate-500">아직 지주사 기록이 없습니다 — 일일 배치가 운용 요약에 적은 날부터 보입니다.</p> : null}
        {data
          ? (["KR", "US"] as const).map((m) => {
              const s = weightSeries(data.holding, m);
              const 끝 = 마지막(m);
              if (!끝) return null;
              const 날 = new Date(끝.run_at).toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });
              return (
                <div key={m} className="mt-3">
                  <h3 className="text-sm font-medium">{m === "KR" ? "국내" : "미국"}</h3>
                  {s.some((x) => x.points.length > 1) ? <EquityChart series={s} height={180} /> : null}
                  {끝.memo.error ? (
                    <p className="text-xs text-red-600 dark:text-red-400">
                      {날} 실행: 지주사가 고르지 못함 — {끝.memo.error}
                    </p>
                  ) : (
                    <p className="text-xs text-slate-600 dark:text-slate-400">
                      {날} 실행: {Object.entries(끝.memo.weights ?? {}).map(([c, w]) => `${c} ${Number(w).toFixed(2)}`).join(" · ")}
                      {끝.memo.samples ? ` · 표본 ${Object.entries(끝.memo.samples).map(([c, n]) => `${c} ${n}`).join(" · ")}` : ""} · 추천 {끝.memo.recommended ?? "-"} · 두 곳 이상{" "}
                      {끝.memo.eligible ?? "-"} · 고름 {끝.memo.chosen ?? "-"}
                    </p>
                  )}
                </div>
              );
            })
          : null}

      </section>

      <Journey />

      <section className={CARD}>
        <h2 className="text-base font-semibold">더 보기</h2>
        <ul className="mt-1 list-disc pl-5 text-sm">
          <li>
            <Link href="/backtest" className="underline">백테스트</Link> — 규칙이 과거에 통했는지(점수·신호 재료)
          </li>
          <li>
            <Link href="/analysis" className="underline">종목 분석</Link> — 한 종목의 결론·근거·반대 목소리(재료)
          </li>
          <li>기법 발굴 회차·판정 대기는 docs/factors.md 12장에 적혀 있습니다(아직 화면 없음)</li>
        </ul>
      </section>
    </div>
  );
}
