"use client";

import { useEffect, useState } from "react";
import EquityChart, { type Series } from "@/components/EquityChart";
import { bodyOf, readJson } from "@/lib/http";
import { amountText, perfLines, priceText, statusText, type KisMarket, type KisMarketView, type KisWhole } from "@/lib/kisPaper";
import { brokerLines } from "@/lib/paper";

/**
 * KIS모의투자 칸 — 국내·미국을 골라 요약·보유내역·거래내역·성과를 본다 (docs/paper.md 15.7, docs/infra.md 25.1496).
 * 값은 배치가 적은 그대로다(보유의 현재가·평가는 KIS 가 준 값, 수익률·낙폭은 배치가 낸 값).
 */

type Sub = "summary" | "holdings" | "trades" | "perf";
const SUBS: Array<[Sub, string]> = [
  ["summary", "요약"],
  ["holdings", "보유내역"],
  ["trades", "거래내역"],
  ["perf", "성과"],
];
const MARKETS: Array<[KisMarket, string]> = [
  ["KR", "국내"],
  ["US", "미국 (예약주문)"],
];

function tone(v: number | null | undefined): string {
  if (v == null || v === 0) return "";
  return v > 0 ? "text-red-600 dark:text-red-400" : "text-blue-600 dark:text-blue-400";
}

/** 계좌 전체 원화 총액 추이 — 시작 총액 1.0 기준 배수(가상모의투자 그래프와 같은 꼴) */
export function kisSeries(w: Pick<KisWhole, "totals">): Series[] {
  const 값 = w.totals.filter((t) => t.total != null && t.total > 0) as Array<{ as_of: string; total: number }>;
  const 첫 = 값[0]?.total;
  return 첫 ? [{ label: "KIS모의투자 총액", color: "#dc2626", points: 값.map((t) => ({ date: t.as_of, value: t.total / 첫 })) }] : [];
}

export default function KisPanel() {
  const [data, setData] = useState<Record<KisMarket, KisMarketView> | null>(null);
  const [whole, setWhole] = useState<KisWhole | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [market, setMarket] = useState<KisMarket>("KR");
  const [sub, setSub] = useState<Sub>("summary");

  useEffect(() => {
    void (async () => {
      const r = await readJson<{ whole: KisWhole; markets: Record<KisMarket, KisMarketView> }>("/api/paper/kis");
      const body = bodyOf<{ whole: KisWhole; markets: Record<KisMarket, KisMarketView> }>(r);
      if (body.errors?.length) setError(body.errors.join(" · "));
      else {
        setData(body.markets);
        setWhole(body.whole);
      }
    })();
  }, []);

  const v = data?.[market];
  const cur = market === "US" ? "USD" : "KRW";
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <h2 className="text-base font-semibold">KIS모의투자 — H(지주사) 주문 베끼기</h2>
      {whole ? (
        <div className="mt-1 text-sm">
          {whole.account_krw?.total_krw != null ? (
            <p>
              계좌 전체 <b>{amountText(whole.account_krw.total_krw, "KRW")}</b>
              <span className="text-xs text-slate-500">
                {" "}
                (국내 {amountText(whole.account_krw.kr_total, "KRW")} + 미국 {amountText((whole.account_krw.value_usd ?? 0) + (whole.account_krw.cash_usd ?? 0), "USD")} × {whole.account_krw.usd_krw ?? "-"}원 · {whole.as_of} 국내 배치 기준)
              </span>
            </p>
          ) : null}
          <ul className="space-y-0.5">
            {perfLines(whole.perf).map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="mt-2 flex gap-2" role="tablist" aria-label="KIS모의투자 시장">
        {MARKETS.map(([m, label]) => (
          <button
            key={m}
            type="button"
            role="tab"
            aria-selected={market === m}
            onClick={() => setMarket(m)}
            className={`rounded-full border px-3 py-1 text-sm ${market === m ? "border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900" : "border-slate-300 dark:border-slate-700"}`}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="mt-3 flex gap-1 overflow-x-auto border-b border-slate-200 text-sm dark:border-slate-800" role="tablist" aria-label="KIS모의투자 화면">
        {SUBS.map(([s, label]) => (
          <button
            key={s}
            type="button"
            role="tab"
            aria-selected={sub === s}
            onClick={() => setSub(s)}
            className={`whitespace-nowrap px-3 py-1.5 ${sub === s ? "border-b-2 border-slate-900 font-semibold dark:border-slate-100" : "text-slate-500"}`}
          >
            {label}
          </button>
        ))}
      </div>

      {error ? <p className="mt-2 text-sm text-red-600">{error}</p> : null}
      {!data && !error ? <p className="mt-2 text-sm text-slate-500">불러오는 중…</p> : null}
      {v ? (
        <div className="mt-3 text-sm">
          {sub === "summary" ? (
            <>
              {v.account ? (
                market === "US" ? (
                  <p>
                    보유 평가 <b>{amountText(v.account.holdings_value, cur)}</b> · 달러 예수금 {amountText(v.account.cash, cur)}
                    <span className="text-xs text-slate-500"> ({v.as_of} 배치 기준 — 미국 매수 돈은 원화 예수금에서 자동 환전으로 나갑니다)</span>
                  </p>
                ) : (
                  <p>
                    총평가 <b>{amountText(v.account.total, cur)}</b> · 예수금(D+2) {amountText(v.account.cash, cur)} · 보유 평가 {amountText(v.account.holdings_value, cur)}
                    <span className="text-xs text-slate-500"> ({v.as_of} 배치 기준)</span>
                  </p>
                )
              ) : null}
              <ul className="mt-2 space-y-0.5 text-slate-600 dark:text-slate-300">
                {brokerLines(v.as_of ? { as_of: v.as_of, diff: v.diff, summary: v.summary } : null).map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            </>
          ) : null}

          {sub === "holdings" ? (
            v.holdings.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-xs">
                  <thead>
                    <tr className="text-left text-slate-500">
                      <th className="py-1">종목</th>
                      <th className="text-right">수량</th>
                      <th className="text-right">평균가</th>
                      <th className="text-right">현재가</th>
                      <th className="text-right">평가금액</th>
                      <th className="text-right">평가손익</th>
                    </tr>
                  </thead>
                  <tbody>
                    {v.holdings.map((h) => (
                      <tr key={h.code} className="border-t border-slate-100 dark:border-slate-800">
                        <td className="py-1">{h.name ?? h.code} <span className="text-slate-400">{h.code}</span></td>
                        <td className="text-right">{h.qty.toLocaleString("ko-KR")}</td>
                        <td className="text-right">{amountText(h.avg, cur)}</td>
                        <td className="text-right">{amountText(h.price, cur)}</td>
                        <td className="text-right">{amountText(h.value, cur)}</td>
                        <td className={`text-right ${tone(h.pnl)}`}>{amountText(h.pnl, cur)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="mt-1 text-xs text-slate-500">{v.as_of} 배치 때 KIS 잔고 조회 그대로(현재가는 그 시각 기준 — 국내는 전날 종가, 미국은 개장 전).</p>
              </div>
            ) : (
              <p className="text-slate-500">{v.as_of ? `${v.as_of} 배치 기준 보유 종목이 없습니다.` : "아직 잔고 기록이 없습니다."}</p>
            )
          ) : null}

          {sub === "trades" ? (
            v.orders.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-xs">
                  <thead>
                    <tr className="text-left text-slate-500">
                      <th className="py-1">날짜</th>
                      <th>종목</th>
                      <th>구분</th>
                      <th className="text-right">수량</th>
                      <th className="text-right">주문가</th>
                      <th>상태</th>
                      <th className="text-right">체결</th>
                      <th className="text-right">체결가</th>
                      <th>비고</th>
                    </tr>
                  </thead>
                  <tbody>
                    {v.orders.map((o) => (
                      <tr key={o.id} className="border-t border-slate-100 dark:border-slate-800">
                        <td className="py-1 whitespace-nowrap">{o.trade_date}</td>
                        <td>{o.name ?? o.code}</td>
                        <td className={o.side === "buy" ? "text-red-600 dark:text-red-400" : "text-blue-600 dark:text-blue-400"}>
                          {o.side === "buy" ? "매수" : "매도"}{o.reason === "정리" ? "(정리)" : ""}
                        </td>
                        <td className="text-right">{o.quantity.toLocaleString("ko-KR")}</td>
                        <td className="text-right">{priceText(o, cur)}</td>
                        <td>{statusText(o.status)}</td>
                        <td className="text-right">{o.filled_qty ?? "-"}</td>
                        <td className="text-right">{amountText(o.avg_price, cur)}</td>
                        <td className="max-w-[16rem] truncate text-slate-500" title={o.message ?? ""}>{o.message ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-slate-500">아직 낸 주문이 없습니다.</p>
            )
          ) : null}

          {sub === "perf" ? (
            <>
              <ul className="space-y-0.5">
                {perfLines(whole?.perf ?? null).map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
              {whole && (kisSeries(whole)[0]?.points.length ?? 0) > 1 ? <EquityChart series={kisSeries(whole)} height={220} /> : null}
              <p className="mt-1 text-xs text-slate-500">
                성과는 국내·미국을 합친 계좌 전체 원화 총액으로 국내 배치가 날마다 냅니다(미국 매수 돈이 원화 예수금에서 자동 환전되어 시장별로 나누면 수익률이 되지 않습니다). 모의 계좌는 입출금이 없어 총액 비가 곧 수익률입니다.
              </p>
            </>
          ) : null}
        </div>
      ) : null}
      <p className="mt-2 text-xs text-slate-500">
        한국투자증권 모의투자 계좌(가상 돈)에 낸 같은 주문의 실제 체결과 가상모의투자의 일봉 판정을 견줍니다. 모의 체결도 실제 시장과 같지 않아 참고로만 봅니다.
      </p>
    </section>
  );
}
