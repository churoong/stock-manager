"use client";

import { useEffect, useState } from "react";
import EquityChart, { type Series } from "@/components/EquityChart";
import { KisRealized } from "@/components/PaperRealized";
import { bodyOf, readJson } from "@/lib/http";
import Pager from "@/components/Pager";
import { amountText, perfLines, priceText, sentTimeText, statusText, usCashText, wholeBreakdownText, type KisMarket, type KisMarketView, type KisOrder, type KisWhole } from "@/lib/kisPaper";
import { orderTimeText, type LiveView } from "@/lib/kisVts";
import { userTimeOf } from "@/lib/market";
import { pageOf } from "@/lib/paging";
import { brokerLines } from "@/lib/paper";

/**
 * KIS모의투자 칸 — 국내·미국을 골라 요약·보유내역·거래내역·성과를 본다 (docs/paper.md 15.7, docs/infra.md 25.1496).
 * 값은 배치가 적은 그대로다(보유의 현재가·평가는 KIS 가 준 값, 수익률·낙폭은 배치가 낸 값).
 */

type Sub = "summary" | "holdings" | "trades" | "realized" | "perf";
const SUBS: Array<[Sub, string]> = [
  ["summary", "요약"],
  ["holdings", "보유내역"],
  ["trades", "거래내역"],
  // 기간 실현손익·일자별 (2026-10-22 사용자 지시, 25.1694)
  ["realized", "실현손익"],
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

/** 열 때 조회(B) 결과의 머리 — KIS 가 준 값 그대로 (docs/paper.md 15.7, 25.1498) */
function LiveHead({ live }: { live: LiveView }) {
  return <p className="text-xs text-emerald-700 dark:text-emerald-400">KIS에서 방금 조회 · {userTimeOf(live.fetched_at)}</p>;
}

/** 보유 표 — 배치 기록과 방금 조회가 같은 열을 쓴다 */
function HoldingsTable({ rows, cur, pct }: { rows: Array<{ code: string; name?: string | null; qty: number; avg: number | null; price: number | null; value: number | null; pnl: number | null; pnl_pct?: number | null }>; cur: "KRW" | "USD"; pct?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="mt-1 w-full min-w-[560px] text-xs">
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
          {rows.map((h) => (
            <tr key={h.code} className="border-t border-slate-100 dark:border-slate-800">
              <td className="py-1">{h.name ?? h.code} <span className="text-slate-400">{h.code}</span></td>
              <td className="text-right">{h.qty.toLocaleString("ko-KR")}</td>
              <td className="text-right">{amountText(h.avg, cur)}</td>
              <td className="text-right">{amountText(h.price, cur)}</td>
              <td className="text-right">{amountText(h.value, cur)}</td>
              <td className={`text-right ${tone(h.pnl)}`}>
                {amountText(h.pnl, cur)}
                {pct && h.pnl_pct != null ? ` (${h.pnl_pct >= 0 ? "+" : ""}${h.pnl_pct.toFixed(2)}%)` : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 방금 조회한 오늘 주문·체결 (거래내역 칸 맨 위) */
function LiveExecutions({ live }: { live: LiveView }) {
  const cur = live.currency;
  return (
    <div className="mb-3 rounded-lg border border-emerald-200 p-2 dark:border-emerald-900">
      <LiveHead live={live} />
      <p className="mt-1 text-xs font-semibold">오늘 주문·체결</p>
      {live.executions.length ? (
        <ul className="text-xs">
          {live.executions.map((e, i) => (
            <li key={`${e.code}-${e.time}-${i}`}>
              {e.time ? `주문 ${orderTimeText(e.time)} · ` : ""}{e.name ?? e.code} {e.side === "buy" ? "매수" : e.side === "sell" ? "매도" : e.side} {e.qty}주 → 체결 {e.filled}주
              {e.avg_price != null ? ` @ ${amountText(e.avg_price, cur)}` : ""}
              {e.note ? ` (${e.note})` : ""}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-slate-500">
          오늘 주문이 없습니다{cur === "USD" ? " — 미국 예약주문은 개장(한국 22:30 · 서머타임 끝나면 23:30) 뒤에야 주문이 되어 보입니다" : ""}.
        </p>
      )}
      {live.more ? <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">KIS 가 첫 쪽만 주었습니다 — 나머지 체결은 아래 배치 기록에서 보세요.</p> : null}
    </div>
  );
}

/**
 * 배치가 적은 KIS 주문 기록 — 20건씩 쪽으로(2026-10-23 사용자 지시 "거래내역 탭이 점점 길어져", 25.1767). 주문 시각은 배치가 KIS 에
 * 보낸 시각(`sent_at`, 한국 시각)이다. **체결 시각은 아직 없다** — KIS 일별 체결 조회는 주문 단위 합계라 체결 시각 칸이 있는지
 * `[확인필요]`이고, 저장하려면 마이그레이션이 필요하다(docs/paper.md 15.7 — 10월 금지는 25.1896 에 지워 이제 막는 까닭이 아니다)
 */
function KisTrades({ orders, cur }: { orders: KisOrder[]; cur: "KRW" | "USD" }) {
  const [page, setPage] = useState(0);
  if (!orders.length) return <p className="text-slate-500">아직 낸 주문이 없습니다.</p>;
  const p = pageOf(orders, page);
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[700px] text-xs">
          <thead>
            <tr className="text-left text-slate-500">
              <th className="py-1">날짜</th>
              <th>주문 시각</th>
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
            {p.rows.map((o) => (
              <tr key={o.id} className="border-t border-slate-100 dark:border-slate-800">
                <td className="py-1 whitespace-nowrap">{o.trade_date}</td>
                <td className="whitespace-nowrap text-slate-500">{sentTimeText(o.sent_at, o.trade_date)}</td>
                <td>{o.name ?? o.code}</td>
                <td className={o.side === "buy" ? "text-red-600 dark:text-red-400" : "text-blue-600 dark:text-blue-400"}>
                  {o.side === "buy" ? "매수" : "매도"}
                  {o.reason === "정리" || o.reason === "장중손절" || o.reason === "장중익절" ? `(${o.reason})` : ""}
                </td>
                <td className="text-right">{o.quantity.toLocaleString("ko-KR")}</td>
                <td className="text-right">{priceText(o, cur)}</td>
                <td>{statusText(o.status)}</td>
                <td className="text-right">{o.filled_qty ?? "-"}</td>
                <td className="text-right">{amountText(o.avg_price, cur)}</td>
                <td className="max-w-[16rem] truncate text-slate-500" title={o.message ?? ""}>
                  {o.message ?? ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager p={p} onPage={setPage} />
      <p className="mt-1 text-xs text-slate-500">
        주문 시각은 KIS 에 보낸 시각(한국)이지 체결 시각이 아닙니다 — 체결 시각은 아직 기록하지 않습니다. 장전에 보낸 국내 주문은 빠르면 09:00 시가 단일가에서, 미국 예약주문은 빠르면 개장 때 체결되지만 지정가는 더 늦게 차거나 끝내 안 찰 수 있습니다. (장중손절)·(장중익절)은 장중에 보낸 시장가입니다.
      </p>
    </>
  );
}

/**
 * 고른 칸(요약·보유내역·거래내역·성과)의 본문 (docs/infra.md 25.1524). 방금 조회(B) 결과도 칸별로 나눠 넣는다 — 예전에는 조회 상자 하나(계좌·보유·
 * 오늘 체결)가 칸 막대 바로 밑에 칸과 상관없이 붙어, 폰에서는 칸을 바꿔도 화면에 보이는 것이 그 상자뿐이라 "내용이 안 바뀐다" 로 보였다.
 */
export function KisBody({ sub, market, v, whole, live }: { sub: Sub; market: KisMarket; v: KisMarketView; whole: KisWhole | null; live?: LiveView | null }) {
  const cur = market === "US" ? "USD" : "KRW";
  return (
    <div className="mt-3 text-sm" role="tabpanel" data-sub={sub}>
      {sub === "summary" ? (
        <>
          {live ? (
            <div className="mb-2 rounded-lg border border-emerald-200 p-2 dark:border-emerald-900">
              <LiveHead live={live} />
              <p className="mt-1">
                {live.currency === "KRW" ? (
                  <>
                    총평가 <b>{amountText(live.account.total, "KRW")}</b> · 현금 {amountText(live.account.cash, "KRW")} · 보유 평가 {amountText(live.account.holdings_value, "KRW")}
                  </>
                ) : (
                  <>
                    보유 평가 <b>{amountText(live.account.holdings_value, "USD")}</b> · {usCashText(live.account)}
                  </>
                )}{" "}
                · 보유 {live.holdings.length}종목{live.more ? "+" : ""} · 오늘 주문 {live.executions.length}건
              </p>
            </div>
          ) : null}
          {v.account ? (
            market === "US" ? (
              <p>
                보유 평가 <b>{amountText(v.account.holdings_value, cur)}</b> · {usCashText(v.account)}
                <span className="text-xs text-slate-500">
                  {" "}
                  ({v.as_of} 배치 기준 — 미국 개장 전에 잰 값이라 그날 체결은 다음 미국 배치나 &quot;지금 KIS에서 조회&quot;에서 보입니다. 미국 매수 돈은 원화 예수금이 아니라 해외 자산에서
                  나갑니다)
                </span>
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
        <>
          {live ? (
            <div className="mb-3 rounded-lg border border-emerald-200 p-2 dark:border-emerald-900">
              <LiveHead live={live} />
              {live.holdings.length ? <HoldingsTable rows={live.holdings} cur={live.currency} pct /> : <p className="mt-1 text-xs text-slate-500">보유 종목 없음</p>}
              {live.more ? <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">KIS 가 첫 쪽만 주었습니다 — 나머지 보유는 아래 배치 기록에서 보세요.</p> : null}
            </div>
          ) : null}
          {live ? <p className="text-xs font-semibold text-slate-500">배치 기록</p> : null}
          {v.holdings.length ? (
            <>
              <HoldingsTable rows={v.holdings} cur={cur} />
              <p className="mt-1 text-xs text-slate-500">{v.as_of} 배치 때 KIS 잔고 조회 그대로(현재가는 그 시각 기준 — 국내는 전날 종가, 미국은 개장 전).</p>
            </>
          ) : (
            <p className="text-slate-500">{v.as_of ? `${v.as_of} 배치 기준 보유 종목이 없습니다.` : "아직 잔고 기록이 없습니다."}</p>
          )}
        </>
      ) : null}

      {sub === "realized" ? <KisRealized key={market} market={market} /> : null}

      {sub === "trades" ? (
        <>
          {live ? <LiveExecutions live={live} /> : null}
          <KisTrades key={market} orders={v.orders} cur={cur} />
        </>
      ) : null}

      {sub === "perf" ? (
        <>
          <ul className="space-y-0.5">
            {perfLines(whole?.perf ?? null).map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
          {whole && (kisSeries(whole)[0]?.points.length ?? 0) > 1 ? <EquityChart series={kisSeries(whole)} height={220} /> : null}
          {whole?.totals_error ? <p className="text-xs text-amber-700">총액 추이를 읽지 못했습니다 — 위 숫자는 마지막 배치 값입니다.</p> : null}
          <p className="mt-1 text-xs text-slate-500">
            성과는 계좌 전체 원화 총액(국내 총평가 + KIS 해외 총자산 원화 환산)으로 국내 배치가 날마다 냅니다. 모의 계좌는 입출금이 없어 총액 비가 곧 수익률입니다. 해외 총자산에는
            달러 현금이 들어 있어 환율이 움직이면 총액도 움직입니다. 고침(25.1527) 전 줄은 미국 몫이 빠진 예전 식이라 추이에 잇지 않습니다. [확인필요] 미국 매수 당일 결제 전
            해외 현금 차감이 늦는지는 아직 장중에 재 보지 않았습니다(체결 이틀 뒤 조회에서는 이미 빠져 있었습니다).
          </p>
        </>
      ) : null}
    </div>
  );
}

export default function KisPanel() {
  const [data, setData] = useState<Record<KisMarket, KisMarketView> | null>(null);
  const [whole, setWhole] = useState<KisWhole | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [market, setMarket] = useState<KisMarket>("KR");
  const [sub, setSub] = useState<Sub>("summary");
  const [live, setLive] = useState<Partial<Record<KisMarket, LiveView>>>({});
  // 조회 안내는 시장마다 — 미국에서 받은 "30초 안에…" 가 국내 칸에 남지 않게(25.1546)
  const [liveNote, setLiveNote] = useState<Partial<Record<KisMarket, string | null>>>({});
  const [liveBusy, setLiveBusy] = useState(false);

  async function fetchLive(m: KisMarket) {
    setLiveBusy(true);
    setLiveNote((x) => ({ ...x, [m]: null }));
    const r = await readJson<{ live?: LiveView; off?: string; busy?: string; wait?: string }>("/api/paper/kis/live", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ market: m }),
    });
    const body = bodyOf<{ live?: LiveView; off?: string; busy?: string; wait?: string }>(r);
    setLiveBusy(false);
    if (body.live) setLive((x) => ({ ...x, [m]: body.live }));
    const 빠짐 = body.live?.missing?.length ? `일부를 받지 못했습니다: ${body.live.missing.join(" · ")} — 그 칸은 비어 있습니다` : null;
    setLiveNote((x) => ({ ...x, [m]: body.errors?.length ? body.errors.join(" · ") : (body.off ?? body.busy ?? body.wait ?? 빠짐) }));
  }

  useEffect(() => {
    void (async () => {
      const r = await readJson<{ whole: KisWhole; markets: Record<KisMarket, KisMarketView> }>("/api/paper/kis");
      const body = bodyOf<{ whole: KisWhole; markets: Record<KisMarket, KisMarketView> }>(r);
      if (body.errors?.length) setError(body.errors.join(" · "));
      else if (!body.markets?.KR || !body.markets?.US) setError("KIS모의투자 기록을 읽지 못했습니다(응답 꼴이 다름 — 새로 고침해 주세요)");
      else {
        setData(body.markets);
        setWhole(body.whole ?? null);
      }
    })();
  }, []);

  const v = data?.[market];
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <h2 className="text-base font-semibold">KIS모의투자 — 지주사 주문 베끼기</h2>
      {whole ? (
        <div className="mt-1 text-sm">
          {whole.account_krw?.total_krw != null ? (
            <p>
              계좌 전체 <b>{amountText(whole.account_krw.total_krw, "KRW")}</b>
              <span className="text-xs text-slate-500">
                {" "}
                ({wholeBreakdownText(whole.account_krw, whole.as_of)})
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
      <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
        <button
          type="button"
          disabled={liveBusy}
          onClick={() => void fetchLive(market)}
          className="rounded border border-emerald-600 px-2 py-0.5 text-emerald-700 disabled:opacity-50 dark:text-emerald-400"
        >
          {liveBusy ? "조회 중…" : `${market === "KR" ? "국내" : "미국"} 지금 KIS에서 조회`}
        </button>
        <span className="text-xs text-slate-500">조회만 합니다 · 결과는 요약·보유내역·거래내역 칸에 나뉘어 보입니다 · 같은 시장은 30초에 한 번 · 일일 배치가 도는 동안은 막힘</span>
      </div>
      {liveNote[market] ? <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">{liveNote[market]}</p> : null}
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
      {v ? <KisBody sub={sub} market={market} v={v} whole={whole} live={live[market] ?? null} /> : null}
      <p className="mt-2 text-xs text-slate-500">
        한국투자증권 모의투자 계좌(가상 돈)에 낸 같은 주문의 실제 체결과 가상모의투자의 일봉 판정을 견줍니다. 모의 체결도 실제 시장과 같지 않아 참고로만 봅니다.
      </p>
    </section>
  );
}
