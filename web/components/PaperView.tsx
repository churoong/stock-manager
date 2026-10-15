"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import CriteriaTable from "@/components/CriteriaTable";
import EquityChart, { type Series } from "@/components/EquityChart";
import { bodyOf, readJson } from "@/lib/http";
import { DEPOSIT_MAX_KRW, DEPOSIT_MIN_KRW, attrCellText, type AccountView, type Attribution } from "@/lib/paper";
import { won } from "@/lib/portfolio";

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
  positions: Array<{ stock_id: number; ticker?: string; name?: string; quantity: number; cost_krw: number; value_krw: number; price_date: string | null }>;
  unknown: Array<{ stock_id: number; why: string }>;
  attribution: Attribution | null;
}
interface Flow { id: number; flow_date: string; amount_krw: number; kind: string; cancels_id: number | null; memo: string | null; cancelled_by: number | null }
interface FillRow {
  id: number; account_id: string; ticker: string; name: string; side: string; fill_date: string; price: number; quantity: number;
  currency: string; fx_rate: number; fee: number; tax: number; slippage: number; cash_krw: number; reason: string; decided_on: string;
  rationale_json: string | null;
}
interface OrderRow { id: number; account_id: string; market: string; ticker: string; name: string; side: string; limit_price: number | null; quantity: number; currency: string; fill_session: string; tranche: number; reason: string }
interface HistoryRow { account_id: string; as_of: string; invested_krw: number; equity_krw: number; kospi_krw: number | null; sp500_krw: number | null }
interface RealCompareView { start?: string; end?: string; realPct?: number; paperPct?: Record<string, number | null>; error?: string }
interface Data { realCompare: RealCompareView | null; invested: number; accounts: Account[]; flows: Flow[]; fills: FillRow[]; orders: OrderRow[]; history: HistoryRow[] }

/** 매매 일지의 근거는 **주문 때 얼린 것**이라 오래될수록 묵는 값이 아니다 — 기준일을 노랗게 칠하지 않는다 */
const JOURNAL_NEVER_STALE_DAYS = 100_000;

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
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        {data.accounts.map((a) => (
          <section key={a.id} className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
            <h2 className="text-base font-semibold">{a.name}</h2>
            <p className="text-xs text-slate-500">규칙서 판 {a.rule_version} · 매도: {RULE_LABEL[a.sell_rule] ?? a.sell_rule}</p>
            <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
              <dt className="text-slate-500">총 입금액</dt><dd className="text-right">{won(a.view.invested)}</dd>
              <dt className="text-slate-500">현금</dt><dd className="text-right">{won(a.view.cash)}</dd>
              <dt className="text-slate-500">미체결 매수 예약</dt><dd className="text-right">{a.view.reserved == null ? "모름(환율 없음)" : won(a.view.reserved)}</dd>
              <dt className="text-slate-500">보유 평가액{a.view.asOf ? ` (${a.view.asOf})` : ""}</dt><dd className="text-right">{won(a.view.holdings)}</dd>
              <dt className="font-medium">평가 총액</dt><dd className="text-right font-medium">{won(a.view.equity)}</dd>
              <dt className="text-slate-500">손익</dt>
              <dd className={`text-right ${tone(a.view.pnl)}`}>{won(a.view.pnl)} ({pctText(a.view.pnlPct)})</dd>
              <dt className="text-slate-500">시간가중 수익률</dt><dd className={`text-right ${tone(a.view.twrPct)}`}>{pctText(a.view.twrPct)}</dd>
              <dt className="text-slate-500">같은 돈을 KOSPI·S&amp;P500 에</dt>
              <dd className="text-right">{pctText(a.view.kospiPct)} · {pctText(a.view.sp500Pct)}</dd>
            </dl>
            {a.view.notes.map((n) => (
              <p key={n} className={`mt-2 text-xs ${n.startsWith("⚠️") ? "text-red-600 dark:text-red-400" : "text-slate-500"}`}>{n}</p>
            ))}
            {a.unknown.length ? <p className="mt-1 text-xs text-amber-600">원가로 평가한 보유 {a.unknown.length}개 (종가·환율 없음 또는 기업행위 멈춤)</p> : null}
            {a.attribution ? (
              <details className="mt-3 text-xs">
                <summary className="cursor-pointer font-medium">노하우 성적표 ({a.attribution.horizon} 거래일 뒤, 지수 대비)</summary>
                <ul className="mt-1 space-y-0.5 text-slate-600 dark:text-slate-400">
                  <li>선정: 산 후보 {attrCellText(a.attribution.selection)} · 안 산 후보 {attrCellText(a.attribution.skipped)}</li>
                  {Object.entries(a.attribution.skip_by_reason ?? {}).map(([k, v]) => (
                    <li key={k}>안 산 까닭 — {k}: {attrCellText(v)}</li>
                  ))}
                  <li>진입: 체결가 대비 그날 종가 {attrCellText(a.attribution.entry, "%")} · 안 찬 회차의 그 뒤 {attrCellText(a.attribution.missed)}</li>
                  <li>청산: 판 뒤 {attrCellText(a.attribution.sold)} · 플래그를 따르지 않고 든 뒤 {attrCellText(a.attribution.held_flag)} (판 뒤가 음수면 판 것이 옳았다)</li>
                </ul>
              </details>
            ) : null}
            {a.positions.length ? (
              <table className="mt-3 w-full text-xs">
                <thead className="text-slate-500"><tr><th className="text-left">종목</th><th className="text-right">수량</th><th className="text-right">원가</th><th className="text-right">평가</th></tr></thead>
                <tbody>
                  {a.positions.map((p) => (
                    <tr key={p.stock_id}>
                      <td>{p.name ?? p.ticker ?? p.stock_id}</td>
                      <td className="text-right">{p.quantity}</td>
                      <td className="text-right">{won(p.cost_krw)}</td>
                      <td className={`text-right ${tone(p.value_krw - p.cost_krw)}`}>{won(p.value_krw)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </section>
        ))}
      </div>

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

      {data.orders.length ? (
        <section className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
          <h2 className="text-base font-semibold">미체결 주문</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {data.orders.map((o) => (
              <li key={o.id}>
                {o.account_id} · {o.name} · {o.side === "buy" ? `매수 ${o.tranche}회차 ${o.limit_price?.toLocaleString("ko-KR")} 이하` : `매도(${o.reason})`} · {o.quantity}주 · {o.fill_session} 장
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
        <h2 className="text-base font-semibold">매매 일지</h2>
        {data.fills.length === 0 ? <p className="mt-1 text-sm text-slate-500">아직 체결이 없습니다.</p> : null}
        <ul className="mt-2 divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {data.fills.map((f) => {
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
                    {f.fill_date} · {f.account_id} · {f.side === "buy" ? "매수" : "매도"} {f.name} {f.quantity}주 @ {f.price.toLocaleString("ko-KR")}
                    {f.currency !== "KRW" ? ` ${f.currency} (환율 ${f.fx_rate})` : "원"} · 현금 {won(f.cash_krw)} · {f.reason}
                  </summary>
                  <p className="mt-1 text-xs text-slate-500">
                    주문 {f.decided_on} 리포트 · 수수료 {f.fee.toFixed(2)} · 세금 {f.tax.toFixed(2)} · 슬리피지 {f.slippage.toFixed(2)} ({f.currency})
                  </p>
                  {Array.isArray(기준) && 기준.length ? (
                    <CriteriaTable rows={기준} caption={`주문 때 근거 (${f.decided_on})`} docPath="docs/paper.md" staleAfterDays={JOURNAL_NEVER_STALE_DAYS} />
                  ) : null}
                </details>
              </li>
            );
          })}
        </ul>
      </section>

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
