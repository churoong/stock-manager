"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import CriteriaTable from "@/components/CriteriaTable";
import EquityChart, { type Series } from "@/components/EquityChart";
import { bodyOf, readJson } from "@/lib/http";
import { DEPOSIT_MAX_KRW, DEPOSIT_MIN_KRW, attrCellText, type AccountView, type Attribution } from "@/lib/paper";
import { money, won } from "@/lib/portfolio";

/**
 * 모의 투자 계좌 화면 (docs/paper.md 5·6장).
 *
 * 사용자가 하는 일은 **입금**뿐이다. 매수·매도는 배치의 규칙서가 하고, 이 화면은 장부를 보여 준다.
 * 총 입금액·현금은 지금 장부로, 보유 평가는 배치가 마지막으로 잰 값으로 보인다 — 둘이 어긋나면 까닭을 적는다.
 */

interface Account {
  id: string;
  name: string;
  sell_rule: string;
  rule_version: number;
  view: AccountView;
  positions: Position[];
  unknown: Array<{ stock_id: number; why: string }>;
  attribution: Attribution | null;
}
/** 보유 한 줄 — 배치가 낸 값 그대로 (25.1455). 웹은 계산하지 않는다 */
interface Position {
  stock_id: number; ticker?: string; name?: string; quantity: number; currency: string; cost_krw: number; value_krw: number;
  price_date: string | null; horizon?: string | null; avg_price?: number | null; close?: number | null; first_buy_date?: string;
  unrealized_krw?: number | null; unrealized_pct?: number | null; weight_pct?: number | null;
}
interface Flow { id: number; flow_date: string; amount_krw: number; kind: string; cancels_id: number | null; memo: string | null; cancelled_by: number | null }
interface FillRow {
  id: number; account_id: string; ticker: string; name: string; side: string; fill_date: string; price: number; quantity: number;
  currency: string; fx_rate: number; fee: number; tax: number; slippage: number; cash_krw: number; realized_krw: number | null;
  reason: string; decided_on: string;
  rationale_json: string | null;
}
interface OrderRow { id: number; account_id: string; market: string; ticker: string; name: string; side: string; limit_price: number | null; quantity: number; currency: string; fill_session: string; tranche: number; reason: string }
interface HistoryRow { account_id: string; as_of: string; invested_krw: number; equity_krw: number; kospi_krw: number | null; sp500_krw: number | null }
interface RealCompareView { start?: string; end?: string; realPct?: number; paperPct?: Record<string, number | null>; error?: string }
export interface Data { halted: boolean; realCompare: RealCompareView | null; invested: number; accounts: Account[]; flows: Flow[]; fills: FillRow[]; orders: OrderRow[]; history: HistoryRow[] }

/** 매매 일지의 근거는 **주문 때 얼린 것**이라 오래될수록 묵는 값이 아니다 — 기준일을 노랗게 칠하지 않는다 */
const JOURNAL_NEVER_STALE_DAYS = 100_000;

const HORIZON_LABEL: Record<string, string> = { short: "단기", mid: "중기", long: "장기" };
export type SubTab = "summary" | "holdings" | "trades" | "orders" | "report";
const SUB_TABS: Array<[SubTab, string]> = [
  ["summary", "요약"],
  ["holdings", "보유내역"],
  ["trades", "거래내역"],
  ["orders", "미체결"],
  ["report", "성적표"],
];

const RULE_LABEL: Record<string, string> = { all: "모든 매도 플래그를 따른다", core: "손절·목표도달·기간초과만 따른다" };

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `k${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function pctText(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "-";
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

function tone(v: number | null | undefined): string {
  if (v == null || v === 0) return "";
  return v > 0 ? "text-red-600 dark:text-red-400" : "text-blue-600 dark:text-blue-400";
}

/** 같은 돈 흐름이라 평가 ÷ 입금으로 견준다 — A·B·KOSPI·S&P500 이 같은 날 같은 금액을 받았다 (docs/paper.md 4장) */
function chartSeries(history: HistoryRow[]): Series[] {
  const by = (aid: string) => history.filter((h) => h.account_id === aid && Number(h.invested_krw) > 0);
  const A = by("A");
  const ratio = (rows: HistoryRow[], pick: (h: HistoryRow) => number | null) =>
    rows.flatMap((h) => {
      const v = pick(h);
      return v == null ? [] : [{ date: h.as_of, value: Number(v) / Number(h.invested_krw) }];
    });
  return [
    { label: "A", color: "#dc2626", points: ratio(A, (h) => h.equity_krw) },
    { label: "B", color: "#7c3aed", points: ratio(by("B"), (h) => h.equity_krw) },
    { label: "KOSPI", color: "#64748b", points: ratio(A, (h) => h.kospi_krw) },
    { label: "S&P500", color: "#0891b2", points: ratio(A, (h) => h.sp500_krw) },
  ];
}

export default function PaperView() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [acct, setAcct] = useState<string>("A");
  const [sub, setSub] = useState<SubTab>("summary");
  // 한 번 입력에 한 키 — 두 번 눌러도, 응답이 끊겨 다시 보내도 같은 키라 장부에 한 줄만 들어간다
  const key = useRef<string>(newKey());

  const load = useCallback(async () => {
    const r = await readJson<Data>("/api/paper");
    const body = bodyOf<Data>(r);
    if (body.errors?.length) setError(body.errors.join(" · "));
    else {
      setError(null);
      setData(body);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function deposit() {
    const 금액 = Number(amount.replaceAll(",", ""));
    if (!Number.isInteger(금액) || 금액 < DEPOSIT_MIN_KRW || 금액 > DEPOSIT_MAX_KRW) {
      setNotice(`금액은 ${won(DEPOSIT_MIN_KRW)} ~ ${won(DEPOSIT_MAX_KRW)} 사이의 원 단위 정수로 넣어 주세요`);
      return;
    }
    const 지금 = data?.invested ?? 0;
    const 물음 =
      `모의 계좌에 ${won(금액)}을 넣습니다 (A·B 두 계좌에 같은 금액).\n\n` +
      `지금 총 투자금액: ${won(지금)}\n입금 뒤 총 투자금액: ${won(지금 + 금액)}\n\n` +
      "매수는 다음 아침 리포트부터 배치가 규칙대로 합니다. 넣을까요?";
    if (!confirm(물음)) return;
    setBusy(true);
    const r = await readJson<{ ok?: boolean; invested?: number; already?: boolean }>("/api/paper/deposits", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ amount_krw: 금액, memo, request_key: key.current }),
    });
    const body = bodyOf<{ ok?: boolean; invested?: number; already?: boolean }>(r);
    setBusy(false);
    if (body.errors?.length) {
      setNotice(body.errors.join(" · "));
      return;
    }
    key.current = newKey();
    setAmount("");
    setMemo("");
    setNotice(`${body.already ? "이미 들어간 입금입니다" : "입금했습니다"} — 장부의 총 투자금액 ${won(body.invested ?? null)}`);
    await load();
  }

  async function toggleHalt() {
    const 켜기 = !data?.halted;
    const 물음 = 켜기
      ? "모의 매매를 멈춥니다. 다음 배치부터 새 주문(매수·매도)을 내지 않고 결정 원장에 '멈춤' 으로 남깁니다. 체결 판정·평가는 그대로 돕니다. 멈출까요?"
      : "모의 매매를 다시 켭니다. 다음 배치부터 규칙대로 주문합니다. 켤까요?";
    if (!confirm(물음)) return;
    setBusy(true);
    const r = await readJson<{ ok?: boolean }>("/api/paper/halt", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ on: 켜기 }),
    });
    const body = bodyOf<{ ok?: boolean }>(r);
    setBusy(false);
    setNotice(body.errors?.length ? body.errors.join(" · ") : 켜기 ? "멈췄습니다 — 다음 배치부터" : "다시 켰습니다 — 다음 배치부터");
    await load();
  }

  async function cancel(flow: Flow) {
    const 지금 = data?.invested ?? 0;
    if (!confirm(`입금 #${flow.id} (${flow.flow_date}, ${won(flow.amount_krw)})을 취소합니다.\n장부에는 취소 행이 더해지고 원래 행은 남습니다.\n\n취소 뒤 총 투자금액: ${won(지금 - Number(flow.amount_krw))}\n\n취소할까요?`)) return;
    setBusy(true);
    const r = await readJson<{ ok?: boolean; invested?: number }>(`/api/paper/deposits/${flow.id}/cancel`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ request_key: newKey() }),
    });
    const body = bodyOf<{ ok?: boolean; invested?: number }>(r);
    setBusy(false);
    setNotice(body.errors?.length ? body.errors.join(" · ") : `취소했습니다 — 장부의 총 투자금액 ${won(body.invested ?? null)}`);
    await load();
  }

  if (error) return <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">{error}</p>;
  if (!data) return <p className="text-sm text-slate-500">불러오는 중…</p>;

  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
        <h2 className="text-base font-semibold">입금</h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          장부의 총 투자금액 <b className="text-slate-900 dark:text-slate-100">{won(data.invested)}</b> — 입금 한 번이 A·B 두 계좌에 같은 금액으로 들어갑니다.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <input
            inputMode="numeric"
            placeholder="금액(원)"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            className="w-40 rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950"
          />
          <input
            placeholder="메모(선택)"
            value={memo}
            maxLength={100}
            onChange={(e) => setMemo(e.target.value)}
            className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950"
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => void deposit()}
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900"
          >
            입금
          </button>
        </div>
        {notice ? <p className="mt-2 text-sm text-slate-700 dark:text-slate-300">{notice}</p> : null}
        <div className="mt-3 flex items-center gap-2 text-sm">
          {data.halted ? <span className="font-medium text-red-600 dark:text-red-400">⏸ 모의 매매 멈춤 중 — 새 주문을 내지 않습니다</span> : null}
          <button
            type="button"
            disabled={busy}
            onClick={() => void toggleHalt()}
            className="rounded border border-slate-300 px-2 py-1 text-xs dark:border-slate-700"
          >
            {data.halted ? "다시 켜기" : "모의 매매 멈춤"}
          </button>
        </div>
      </section>

      <AccountTabs data={data} acct={acct} setAcct={setAcct} sub={sub} setSub={setSub} />

      {data.realCompare ? (
        <section className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
          <h2 className="text-base font-semibold">실제 투자와 비교</h2>
          {data.realCompare.error ? (
            <p className="mt-1 text-sm text-amber-600">{data.realCompare.error}</p>
          ) : (
            <>
              <p className="mt-1 text-sm">
                {data.realCompare.start} ~ {data.realCompare.end} 시간가중 수익률 · 실제{" "}
                <b className={tone(data.realCompare.realPct)}>{pctText(data.realCompare.realPct)}</b>
                {Object.entries(data.realCompare.paperPct ?? {}).map(([aid, v]) => (
                  <span key={aid}>
                    {" "}· 모의 {aid} <b className={tone(v)}>{pctText(v)}</b>
                  </span>
                ))}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                같은 잣대가 아닙니다 — 실제는 보유분만(현금 없음)·배당 포함, 모의는 현금 포함·배당 없음. 현금 비중만큼 모의가 덜 움직입니다.
              </p>
            </>
          )}
        </section>
      ) : null}

      {data.history.length > 1 ? (
        <section className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
          <h2 className="text-base font-semibold">평가 ÷ 입금 (같은 돈 흐름)</h2>
          <EquityChart series={chartSeries(data.history)} height={240} />
        </section>
      ) : null}

      <section className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
        <h2 className="text-base font-semibold">입금 장부</h2>
        <p className="text-xs text-slate-500">지우지 않습니다 — 잘못 넣은 입금은 취소 행을 더합니다.</p>
        <ul className="mt-2 divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {data.flows.map((f) => (
            <li key={f.id} className="flex items-center gap-2 py-2">
              <span className="min-w-0 flex-1">
                #{f.id} · {f.flow_date} · <span className={Number(f.amount_krw) < 0 ? "text-blue-600" : ""}>{won(f.amount_krw)}</span>
                {f.kind === "cancel" ? ` (입금 #${f.cancels_id} 취소)` : ""}
                {f.memo && f.kind === "deposit" ? ` · ${f.memo}` : ""}
                {f.cancelled_by ? " · 취소됨" : ""}
              </span>
              {f.kind === "deposit" && !f.cancelled_by ? (
                <button type="button" disabled={busy} onClick={() => void cancel(f)} className="rounded border border-slate-300 px-2 py-1 text-xs dark:border-slate-700">
                  취소
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

const CARD = "rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900";

/**
 * 계좌별 화면 (2026-10-15 사용자 지시 "각 모의A나 모의B의 보유내역, 거래내역, 평가손익 등을 확인할 수 있어야해", 25.1455).
 * 위 탭으로 계좌를, 아래 탭으로 요약·보유내역·거래내역·미체결·성적표를 고른다. 값은 모두 배치가 적은 그대로다.
 */
export function AccountTabs({ data, acct, setAcct, sub, setSub }: {
  data: Data; acct: string; setAcct: (a: string) => void; sub: SubTab; setSub: (s: SubTab) => void;
}) {
  const a = data.accounts.find((x) => x.id === acct) ?? data.accounts[0];
  if (!a) return null;
  const 체결 = data.fills.filter((f) => f.account_id === a.id);
  const 주문 = data.orders.filter((o) => o.account_id === a.id);
  return (
    <section className={CARD}>
      <div className="flex gap-2" role="tablist" aria-label="모의 계좌">
        {data.accounts.map((x) => (
          <button
            key={x.id}
            type="button"
            role="tab"
            aria-selected={x.id === a.id}
            onClick={() => setAcct(x.id)}
            className={`min-w-0 flex-1 rounded-lg border px-3 py-2 text-left text-sm ${x.id === a.id ? "border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900" : "border-slate-300 dark:border-slate-700"}`}
          >
            <span className="block font-semibold">모의 {x.id}</span>
            <span className="block truncate text-xs opacity-80">
              {won(x.view.equity)} · {pctText(x.view.pnlPct)}
            </span>
          </button>
        ))}
      </div>
      <p className="mt-2 text-xs text-slate-500">
        {a.name} · 규칙서 판 {a.rule_version} · 매도: {RULE_LABEL[a.sell_rule] ?? a.sell_rule}
      </p>
      <div className="mt-3 flex gap-1 overflow-x-auto border-b border-slate-200 text-sm dark:border-slate-800" role="tablist" aria-label="계좌 화면">
        {SUB_TABS.map(([k, label]) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={sub === k}
            onClick={() => setSub(k)}
            className={`shrink-0 px-3 py-2 ${sub === k ? "border-b-2 border-slate-900 font-medium dark:border-slate-100" : "text-slate-500"}`}
          >
            {label}
            {k === "holdings" ? ` ${a.positions.length}` : k === "trades" ? ` ${체결.length}` : k === "orders" ? ` ${주문.length}` : ""}
          </button>
        ))}
      </div>
      <div className="mt-3">
        {sub === "summary" ? <SummaryPane a={a} /> : null}
        {sub === "holdings" ? <HoldingsPane a={a} /> : null}
        {sub === "trades" ? <TradesPane fills={체결} /> : null}
        {sub === "orders" ? <OrdersPane orders={주문} /> : null}
        {sub === "report" ? <ReportPane a={a} /> : null}
      </div>
    </section>
  );
}

function SummaryPane({ a }: { a: Account }) {
  const v = a.view;
  const 줄: Array<[string, string, number | null | undefined]> = [
    ["총 입금액", won(v.invested), null],
    ["현금", won(v.cash), null],
    ["미체결 매수 예약", v.reserved == null ? "모름(환율 없음)" : won(v.reserved), null],
    [`보유 평가액${v.asOf ? ` (${v.asOf})` : ""}`, won(v.holdings), null],
    ["평가 총액", won(v.equity), null],
    ["총 손익 (평가 총액 − 총 입금액)", `${won(v.pnl)} (${pctText(v.pnlPct)})`, v.pnl],
    ["평가손익 (보유분, 아직 안 판 것)", won(v.unrealized), v.unrealized],
    ["실현손익 (판 것, 비용 반영)", won(v.realized), v.realized],
    ["시간가중 수익률", pctText(v.twrPct), v.twrPct],
    ["같은 돈을 KOSPI · S&P500 에", `${pctText(v.kospiPct)} · ${pctText(v.sp500Pct)}`, null],
  ];
  return (
    <div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
        {줄.map(([k, val, 부호]) => (
          <div key={k} className="contents">
            <dt className="text-slate-500">{k}</dt>
            <dd className={`text-right ${tone(부호)}`}>{val}</dd>
          </div>
        ))}
      </dl>
      {v.notes.map((n) => (
        <p key={n} className={`mt-2 text-xs ${n.startsWith("⚠️") ? "text-red-600 dark:text-red-400" : "text-slate-500"}`}>{n}</p>
      ))}
      {a.unknown.length ? <p className="mt-1 text-xs text-amber-600">원가로 평가한 보유 {a.unknown.length}개 (종가·환율 없음 또는 기업행위 멈춤)</p> : null}
    </div>
  );
}

function HoldingsPane({ a }: { a: Account }) {
  if (!a.positions.length) return <p className="text-sm text-slate-500">보유 종목이 없습니다{a.view.asOf ? ` (${a.view.asOf} 평가)` : ""}.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-xs">
        <thead className="text-slate-500">
          <tr>
            <th className="py-1 text-left">종목</th>
            <th className="text-right">수량</th>
            <th className="text-right">평균단가</th>
            <th className="text-right">현재가</th>
            <th className="text-right">원가(원)</th>
            <th className="text-right">평가액(원)</th>
            <th className="text-right">평가손익</th>
            <th className="text-right">비중</th>
            <th className="text-right">첫 매수 · 기간</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {a.positions.map((p) => (
            <tr key={p.stock_id}>
              <td className="py-1">{p.name ?? p.ticker ?? p.stock_id}</td>
              <td className="text-right">{p.quantity.toLocaleString("ko-KR")}</td>
              <td className="text-right">{money(p.avg_price ?? null, p.currency)}</td>
              <td className="text-right">
                {money(p.close ?? null, p.currency)}
                {p.price_date ? <span className="block text-[10px] text-slate-400">{p.price_date}</span> : null}
              </td>
              <td className="text-right">{won(p.cost_krw)}</td>
              <td className="text-right">{won(p.value_krw)}</td>
              <td className={`text-right ${tone(p.unrealized_krw)}`}>
                {won(p.unrealized_krw ?? null)}
                <span className="block">{pctText(p.unrealized_pct)}</span>
              </td>
              <td className="text-right">{p.weight_pct == null ? "-" : `${p.weight_pct.toFixed(1)}%`}</td>
              <td className="text-right">
                {p.first_buy_date ?? "-"}
                <span className="block text-[10px] text-slate-400">{HORIZON_LABEL[p.horizon ?? ""] ?? "기간 없음"}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-[11px] text-slate-500">원가는 수수료 포함, 달러 종목은 체결일 환율로 원화 환산. 현재가는 배치가 마지막으로 받은 종가입니다.</p>
    </div>
  );
}

function TradesPane({ fills }: { fills: FillRow[] }) {
  if (!fills.length) return <p className="text-sm text-slate-500">아직 체결이 없습니다.</p>;
  return (
    <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
      {fills.map((f) => {
        const 근거 = (() => {
          try {
            return f.rationale_json ? JSON.parse(f.rationale_json) : null;
          } catch {
            return null;
          }
        })();
        const 기준 = 근거?.signal?.criteria ?? 근거?.flags?.flatMap((x: { criteria?: unknown[] }) => x.criteria ?? []) ?? [];
        return (
          <li key={f.id} className="py-2">
            <details>
              <summary className="cursor-pointer">
                <span className={f.side === "buy" ? "text-red-600 dark:text-red-400" : "text-blue-600 dark:text-blue-400"}>{f.side === "buy" ? "매수" : "매도"}</span>{" "}
                {f.fill_date} · {f.name} {f.quantity.toLocaleString("ko-KR")}주 @ {money(f.price, f.currency)}
                {f.currency !== "KRW" ? ` (환율 ${f.fx_rate})` : ""} · {f.side === "buy" ? "지급" : "입금"} {won(Math.abs(f.cash_krw))}
                {f.side === "sell" && f.realized_krw != null ? (
                  <span className={tone(f.realized_krw)}> · 실현손익 {won(f.realized_krw)}</span>
                ) : null}
              </summary>
              <p className="mt-1 text-xs text-slate-500">
                사유 {f.reason} · 주문 {f.decided_on} 리포트 · 수수료 {money(f.fee, f.currency)} · 세금 {money(f.tax, f.currency)} · 슬리피지{" "}
                {money(f.slippage, f.currency)}
              </p>
              {Array.isArray(기준) && 기준.length ? (
                <CriteriaTable rows={기준} caption={`주문 때 근거 (${f.decided_on})`} docPath="docs/paper.md" staleAfterDays={JOURNAL_NEVER_STALE_DAYS} />
              ) : null}
            </details>
          </li>
        );
      })}
    </ul>
  );
}

function OrdersPane({ orders }: { orders: OrderRow[] }) {
  if (!orders.length) return <p className="text-sm text-slate-500">미체결 주문이 없습니다.</p>;
  return (
    <ul className="space-y-1 text-sm">
      {orders.map((o) => (
        <li key={o.id}>
          {o.name} · {o.side === "buy" ? `매수 ${o.tranche}회차 ${money(o.limit_price, o.currency)} 이하` : `매도(${o.reason}) 시장가`} ·{" "}
          {o.quantity.toLocaleString("ko-KR")}주 · {o.fill_session} 장에서 판정
        </li>
      ))}
    </ul>
  );
}

function ReportPane({ a }: { a: Account }) {
  const t = a.attribution;
  if (!t) return <p className="text-sm text-slate-500">아직 성과가 찬 결정이 없습니다 — 결정 뒤 20 거래일이 지나야 채워집니다.</p>;
  return (
    <div className="text-sm">
      <p className="text-xs text-slate-500">노하우 성적표 — {t.horizon} 거래일 뒤, 지수 대비 초과(%p). 표본 30건 미만은 참고만.</p>
      <ul className="mt-1 space-y-0.5 text-slate-700 dark:text-slate-300">
        <li>선정: 주문 낸 후보 {attrCellText(t.selection)} · 안 낸 새 후보 {attrCellText(t.skipped)}</li>
        {Object.entries(t.skip_by_reason ?? {}).map(([k, v]) => (
          <li key={k}>안 낸 까닭 — {k}: {attrCellText(v)}</li>
        ))}
        <li>진입: 체결가 대비 그날 종가 {attrCellText(t.entry, "%")} · 안 찬 회차의 그 뒤 {attrCellText(t.missed)}</li>
        <li>청산: 판 뒤 {attrCellText(t.sold)} · 플래그를 따르지 않고 든 뒤 {attrCellText(t.held_flag)} (판 뒤가 음수면 판 것이 옳았다)</li>
        {t.blocked?.n ? <li>위험 점검이 막은 것의 그 뒤: {attrCellText(t.blocked)}</li> : null}
      </ul>
    </div>
  );
}
