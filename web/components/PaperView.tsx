"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import CriteriaTable from "@/components/CriteriaTable";
import PaperRealized from "@/components/PaperRealized";
import Pager from "@/components/Pager";
import EquityChart, { type Series } from "@/components/EquityChart";
import Link from "next/link";
import { DESK_RULES, MARKET_LABEL, adjustCheckText, adjustText, championText, pctText as deskPct, ppText, preregText, ruleSpanText, rowsById, type DeskView, type RuleSpan } from "@/lib/desk";
import { bodyOf, readJson } from "@/lib/http";
import {
  DEPOSIT_MAX_KRW,
  DEPOSIT_MIN_KRW,
  DEPT_LABEL,
  FILLS_PER_ACCOUNT,
  PAIRS,
  attrCellText,
  entryCellText,
  deptScoreText,
  ruleText,
  stanceText,
  type AccountView,
  type Attribution,
  hasCommittee,
  virtualText,
} from "@/lib/paper";
import { pageOf } from "@/lib/paging";
import { KIND_TEXT, afterText, beatBench, groupDecisions, holdSide, operationalSkip, type DecisionRow } from "@/lib/paperDecisions";
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
  entry_rule?: string | null;
  top_n?: number | null;
  selection?: string | null;
  charter_json?: string | null;
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
  /** 종목 통화 기준 평가손익 % · 원가로 둔 까닭 (배치가 적음, 25.1604) */
  unrealized_local_pct?: number | null; unknown_why?: string | null;
}
interface Flow { id: number; flow_date: string; amount_krw: number; kind: string; cancels_id: number | null; memo: string | null; cancelled_by: number | null }
export interface FillRow {
  id: number; account_id: string; ticker: string; name: string; side: string; fill_date: string; price: number; quantity: number;
  currency: string; fx_rate: number; fee: number; tax: number; slippage: number; cash_krw: number; realized_krw: number | null;
  reason: string; decided_on: string;
  rationale_json: string | null;
}
interface OrderRow { id: number; account_id: string; market: string; ticker: string; name: string; side: string; order_type?: string; limit_price: number | null; ref_price?: number | null; quantity: number; currency: string; fill_session: string; tranche: number; reason: string }
interface HistoryRow { account_id: string; as_of: string; invested_krw: number; equity_krw: number; kospi_krw: number | null; sp500_krw: number | null }
interface RealCompareView { start?: string; end?: string; realPct?: number; paperPct?: Record<string, number | null>; paperStart?: Record<string, string>; error?: string }
/** 투자위원회 회의록 한 장 (docs/company.md 3·5장) — 배치가 적은 그대로 */
interface MeetingRow { account_id: string; market: string; decided_on: string; charter_hash: string; minutes_json: string }
interface DossierRow { market: string; as_of: string; stock_id: number; dossier_json: string }
export interface Data { halted: boolean; realCompare: RealCompareView | null; invested: number; accounts: Account[]; flows: Flow[]; fills: FillRow[]; orders: OrderRow[]; history: HistoryRow[]; meetings?: MeetingRow[]; dossiers?: DossierRow[] }

/** 매매 일지의 근거는 **주문 때 얼린 것**이라 오래될수록 묵는 값이 아니다 — 기준일을 노랗게 칠하지 않는다 */
const JOURNAL_NEVER_STALE_DAYS = 100_000;

const HORIZON_LABEL: Record<string, string> = { short: "단기", mid: "중기", long: "장기" };
export type SubTab = "summary" | "holdings" | "trades" | "realized" | "orders" | "report" | "rules" | "skips" | "committee";
const SUB_TABS: Array<[SubTab, string]> = [
  ["summary", "요약"],
  ["holdings", "보유내역"],
  ["trades", "거래내역"],
  // 기간 실현손익·일자별 (2026-10-22 사용자 지시, 25.1693)
  ["realized", "실현손익"],
  ["orders", "체결 대기"],
  ["report", "성적표"],
  // 전략 화면 (docs/desk.md 3장, 25.1502) — 규칙을 바꾼 날과 전후 성적, 안 한 결정과 그 뒤
  ["rules", "판 이력"],
  ["skips", "안 산 것"],
  ["committee", "투자위원회"],
];


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

/** 계좌 선 색 — 계좌 id 로 고정, 무작위 대조(R)는 회색 계열로 눌러 둔다. 차례로 돌려 쓰면 계좌가 아홉이라 R1 이 A 색,
 *  G·H 가 R 의 회색을 가져갔다(25.1604, 웹 화면 감사 [권고] 5) */
export const ACCOUNT_COLOR: Record<string, string> = {
  A: "#dc2626", B: "#7c3aed", C: "#ea580c", D: "#16a34a", E: "#2563eb", G: "#ca8a04", H: "#db2777", R1: "#9ca3af", R2: "#cbd5e1",
};
const SPARE_COLORS = ["#0d9488", "#4f46e5", "#a16207"];

/** 같은 돈 흐름이라 평가 ÷ 입금으로 견준다 — 모든 계좌·KOSPI·S&P500 이 같은 날 같은 금액을 받았다 (docs/paper.md 4장) */
function chartSeries(history: HistoryRow[], ids: string[]): Series[] {
  const by = (aid: string) => history.filter((h) => h.account_id === aid && Number(h.invested_krw) > 0);
  const 첫 = by(ids[0] ?? "");
  const ratio = (rows: HistoryRow[], pick: (h: HistoryRow) => number | null) =>
    rows.flatMap((h) => {
      const v = pick(h);
      return v == null ? [] : [{ date: h.as_of, value: Number(v) / Number(h.invested_krw) }];
    });
  return [
    ...ids.map((aid, i) => ({ label: aid, color: ACCOUNT_COLOR[aid] ?? SPARE_COLORS[i % SPARE_COLORS.length], points: ratio(by(aid), (h) => h.equity_krw) })),
    { label: "KOSPI", color: "#64748b", points: ratio(첫, (h) => h.kospi_krw) },
    { label: "S&P500", color: "#0891b2", points: ratio(첫, (h) => h.sp500_krw) },
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
  // 전략 순위·판 이력은 배치가 낸 운용 요약(paper_desk)에서 — 못 읽어도 계좌 화면은 그대로 (25.1502)
  const [desk, setDesk] = useState<DeskView | null>(null);
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
    void (async () => {
      const r = await readJson<{ ready?: boolean; desk?: DeskView }>("/api/desk");
      const body = bodyOf<{ ready?: boolean; desk?: DeskView }>(r);
      if (r.ok && !body.errors?.length && body.desk) setDesk(body.desk);
    })();
  }, [load]);

  async function deposit() {
    const 금액 = Number(amount.replaceAll(",", ""));
    if (!Number.isInteger(금액) || 금액 < DEPOSIT_MIN_KRW || 금액 > DEPOSIT_MAX_KRW) {
      setNotice(`금액은 ${won(DEPOSIT_MIN_KRW)} ~ ${won(DEPOSIT_MAX_KRW)} 사이의 원 단위 정수로 넣어 주세요`);
      return;
    }
    const 지금 = data?.invested ?? 0;
    const 물음 =
      `가상모의투자 계좌에 ${won(금액)}을 넣습니다 (가상모의투자 계좌 ${data?.accounts.length ?? 0}개 모두에 같은 금액).\n\n` +
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
      ? "가상모의투자·KIS모의투자 매매를 멈춥니다. 다음 배치부터 새 주문(매수·매도)을 내지 않고 결정 원장에 '멈춤' 으로 남깁니다. 체결 판정·평가는 그대로 돕니다. 멈출까요?"
      : "가상모의투자·KIS모의투자 매매를 다시 켭니다. 다음 배치부터 규칙대로 주문합니다. 켤까요?";
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
      {data.halted ? (
        <p className="rounded-lg bg-red-50 p-3 text-sm font-medium text-red-700 dark:bg-red-950 dark:text-red-300">
          ⏸ 매매 멈춤 중 — 가상모의투자·KIS모의투자 모두 새 주문을 내지 않습니다(아래 입금 칸에서 다시 켭니다)
        </p>
      ) : null}

      <CompareCard data={data} desk={desk} />

      <AccountTabs data={data} acct={acct} setAcct={setAcct} sub={sub} setSub={setSub} desk={desk} />

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
                    {" "}· 가상 {aid} <b className={tone(v)}>{pctText(v)}</b>
                    {data.realCompare?.paperStart?.[aid] ? ` (${data.realCompare.paperStart[aid]}부터)` : ""}
                  </span>
                ))}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                같은 잣대가 아닙니다 — 실제는 보유분만(현금 없음)·배당 포함, 가상모의투자는 현금 포함·배당 없음. 현금 비중만큼 가상모의투자가 덜 움직입니다.
              </p>
            </>
          )}
        </section>
      ) : null}

      {/* KIS모의투자는 제 화면으로 옮겼다 (docs/desk.md 4장, 25.1504) */}
      <section className="rounded-xl border border-slate-200 bg-white p-4 text-sm dark:border-slate-800 dark:bg-slate-900">
        H 의 주문을 KIS모의투자에 그대로 낸 결과·실행 품질은{" "}
        <Link href="/kis" className="underline">
          KIS모의투자 화면
        </Link>
        에 있습니다.
      </section>

      {data.history.length > 1 ? (
        <section className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
          <h2 className="text-base font-semibold">평가 ÷ 입금 (같은 돈 흐름)</h2>
          <EquityChart series={chartSeries(data.history, data.accounts.map((a) => a.id))} height={240} />
        </section>
      ) : null}

      <section className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
        <h2 className="text-base font-semibold">입금</h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          장부의 총 투자금액 <b className="text-slate-900 dark:text-slate-100">{won(data.invested)}</b> — 입금 한 번이 가상모의투자 계좌 {data.accounts.length}개 모두에 같은 금액으로 들어갑니다.
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
          {data.halted ? <span className="font-medium text-red-600 dark:text-red-400">⏸ 매매 멈춤 중 — 가상모의투자·KIS모의투자 모두 새 주문을 내지 않습니다</span> : null}
          <button
            type="button"
            disabled={busy}
            onClick={() => void toggleHalt()}
            className="rounded border border-slate-300 px-2 py-1 text-xs dark:border-slate-700"
          >
            {data.halted ? "다시 켜기" : "매매 멈춤"}
          </button>
        </div>
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

const CARD = "rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900";

/**
 * 계좌 비교 (25.1458, 2026-10-15 사용자 지시 "두개의 모의투자자로는 부족할거 같다") — 일곱 계좌를 한 표에.
 * 값은 계좌 화면과 같은 배치 값이고, 짝은 "무엇을 재는 차이인가" 의 안내다(차이 계산은 주간 요약이 한다).
 */
/** 순위표의 KIS 체결 보정 칸 (docs/desk.md 8장, 25.1514) — 값은 배치, 글만 여기서 */
function AdjustCell({ x }: { x: Parameters<typeof adjustText>[0] }) {
  const t = adjustText(x);
  return (
    <td className={`text-right ${x?.close_call ? "text-amber-700 dark:text-amber-400" : ""}`}>
      {t.main}
      {t.sub ? <span className="block text-[10px] text-slate-500">{t.sub}</span> : null}
    </td>
  );
}

export function CompareCard({ data, desk }: { data: Data; desk?: DeskView | null }) {
  if (data.accounts.length < 2) return null;
  const 있음 = new Set(data.accounts.map((a) => a.id));
  // 전략 순위 (docs/desk.md 3장, 25.1502) — 공통 구간·R 대비는 배치가 낸 운용 요약 값. 있으면 그 순서로, 없으면 계좌 순
  const 순위 = rowsById(desk?.leaderboard);
  const 차례 = desk?.leaderboard?.rows.map((r) => r.id) ?? [];
  const 계좌들 = [...data.accounts].sort((x, y) => {
    const i = 차례.indexOf(x.id), j = 차례.indexOf(y.id);
    return (i < 0 ? 1e9 : i) - (j < 0 ? 1e9 : j) || x.id.localeCompare(y.id);
  });
  const lb = desk?.leaderboard;
  return (
    <section className={CARD}>
      <h2 className="text-base font-semibold">전략 순위</h2>
      {/* 기준 날짜 — 공통 구간·R 대비는 운용 요약, 시간가중·손익·평가 총액은 평가 행에서 와 날이 다를 수 있다(25.1604) */}
      <p className="mt-1 text-[11px] text-slate-400">
        운용 요약 {desk?.as_of ?? "없음"} · 평가 {[...new Set(data.accounts.map((a) => a.view.asOf).filter(Boolean))].sort().join("·") || "없음"} 기준
      </p>
      {lb?.window_start ? (
        <p className="mt-1 text-xs text-slate-500">
          공통 구간 {lb.window_start}부터의 시간가중 수익률 순 · R 대비 = 그 수익률 − 무작위 대조 R1·R2 평균
          {lb.control_pct != null ? `(${deskPct(lb.control_pct)})` : ""} — 늦게 연 계좌도 같은 잣대. "갓 엶" 계좌(평가 20번 전)는 제 개설일부터 잰 값입니다
        </p>
      ) : null}
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[680px] text-xs">
          <thead className="text-slate-500">
            <tr>
              <th className="py-1 text-left">계좌</th>
              <th className="text-right">공통 구간</th>
              <th className="text-right">R 대비</th>
              {desk?.fill_adjust ? (
                <th className="text-right" title="KIS모의 체결 기준 보정(근사·참고) — 순위는 바꾸지 않습니다">
                  KIS 체결 보정
                </th>
              ) : null}
              <th className="text-right">시간가중</th>
              <th className="text-right">손익</th>
              <th className="text-right">평가 총액</th>
              <th className="pl-2 text-left">규칙</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {계좌들.map((a) => {
              const r = 순위.get(a.id);
              return (
                <tr key={a.id}>
                  <td className="py-1 font-semibold">
                    {a.id}
                    {r?.young ? <span className="block text-[10px] font-normal text-amber-700 dark:text-amber-400">갓 엶 · {r.open}부터</span> : null}
                  </td>
                  <td className={`text-right ${tone(r?.window_pct)}`}>{deskPct(r?.window_pct)}</td>
                  <td className={`text-right ${tone(r?.vs_r_pp)}`}>{ppText(r?.vs_r_pp)}</td>
                  {desk?.fill_adjust ? <AdjustCell x={desk.fill_adjust.rows[a.id]} /> : null}
                  <td className={`text-right ${tone(a.view.twrPct)}`}>{pctText(a.view.twrPct)}</td>
                  <td className={`text-right ${tone(a.view.pnlPct)}`}>{pctText(a.view.pnlPct)}</td>
                  <td className="text-right">{won(a.view.equity)}</td>
                  <td className="pl-2 text-slate-600 dark:text-slate-400">{ruleText(a)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {desk?.fill_adjust ? (
        <p className="mt-2 text-xs text-slate-500">
          KIS 체결 보정(근사·참고) = 순위 수익률(갓 연 계좌는 개설일부터) − 회전 × KIS모의투자가 잰 &quot;장부 가정을 넘는 체결 비용&quot; + 지정가 매수가 시가로 더 싸게 찼을 몫.
          KIS 칸은 거래일 {desk.fill_adjust.min_days}일 이상 쌓인 것만 쓰고 지정가 매수 칸은 쓰지 않습니다. ± 는 칸 값의 불확실성, &quot;안 잰 회전&quot; 이 크면 보정이
          말해 주는 것이 적습니다. KIS모의투자도 실제 시장과 다르고, 순위는 바꾸지 않습니다.
          {adjustCheckText(desk.fill_adjust.h_check, desk.mirror_account ?? "H") ? (
            <span className="block">대보기 — {adjustCheckText(desk.fill_adjust.h_check, desk.mirror_account ?? "H")}</span>
          ) : null}
        </p>
      ) : desk?.fill_adjust_error ? (
        <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">KIS 체결 보정 계산 실패 — {desk.fill_adjust_error}</p>
      ) : null}
      {desk?.champion?.length ? (
        <div className="mt-3 rounded-lg border border-slate-200 p-2 text-xs dark:border-slate-800">
          <p className="font-medium">챔피언 교체 후보 — 지금 챔피언 {desk.mirror_account ?? "H"}(KIS 가 베낌)를 꾸준히 앞서는 전략</p>
          {desk.champion.some((r) => r.candidate) ? (
            <ul className="mt-1 space-y-0.5 text-emerald-700 dark:text-emerald-400">
              {desk.champion.filter((r) => r.candidate).map((r) => (
                <li key={r.id}>{championText(r, desk.mirror_account ?? "H")}</li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-slate-500">
              아직 없음 — 가장 가까운 {championText(desk.champion[0], desk.mirror_account ?? "H")}
            </p>
          )}
          <p className="mt-1 text-slate-500">
            최근 끝에서부터 겹치지 않는 {DESK_RULES.CHAMPION_BLOCK} 평가 블록 {DESK_RULES.CHAMPION_MIN_BLOCKS}개 이상, 그 가운데 {Math.round(DESK_RULES.CHAMPION_WIN_SHARE * 100)}% 이상 이기고 전체도
            앞서고, <b>결정 단위로도</b> 앞서야(28일 묶음마다 산 종목의 지수 대비 초과를 챔피언과 짝지어, 차이의 한쪽 하한 &gt; 0 — 도전자 수로 유의수준
            {Math.round(DESK_RULES.CHAMPION_ALPHA * 100)}% 를 나눔, 묶음 {DESK_RULES.CHAMPION_EVIDENCE_MIN_BLOCKS}개 이상) 후보입니다. 블록 규칙만으로는 실력이 없어도 석 달 안에
            거의 늘 후보가 나왔습니다. 교체는 사용자 결정 — 후보가 나와도 바꾸지 않습니다.
          </p>
        </div>
      ) : desk?.gate_error ? (
        <p className="mt-3 text-xs text-amber-700 dark:text-amber-400">챔피언 교체 후보를 내지 않음 — {desk.gate_error}</p>
      ) : null}
      <p className="mt-2 text-xs text-slate-500">
        짝마다 규칙 하나만 다릅니다 —{" "}
        {PAIRS.filter(([x, y]) => 있음.has(x) && 있음.has(y))
          .map(([x, y, 무엇]) => `${x}−${y}: ${무엇}`)
          .join(" · ")}
        . 몇 달 차이는 대부분 운입니다(R1·R2 가 그 크기를 보여 줍니다).
      </p>
    </section>
  );
}

/**
 * 계좌별 화면 (2026-10-15 사용자 지시 "각 모의A나 모의B의 보유내역, 거래내역, 평가손익 등을 확인할 수 있어야해", 25.1455).
 * 위 탭으로 계좌를, 아래 탭으로 요약·보유내역·거래내역·미체결·성적표를 고른다. 값은 모두 배치가 적은 그대로다.
 */
export function AccountTabs({ data, acct, setAcct, sub, setSub, desk }: {
  data: Data; acct: string; setAcct: (a: string) => void; sub: SubTab; setSub: (s: SubTab) => void; desk?: DeskView | null;
}) {
  const a = data.accounts.find((x) => x.id === acct) ?? data.accounts[0];
  if (!a) return null;
  const 체결 = data.fills.filter((f) => f.account_id === a.id);
  const 주문 = data.orders.filter((o) => o.account_id === a.id);
  return (
    <section className={CARD}>
      <div className="flex gap-2 overflow-x-auto pb-1" role="tablist" aria-label="가상모의투자 계좌">
        {data.accounts.map((x) => (
          <button
            key={x.id}
            type="button"
            role="tab"
            aria-selected={x.id === a.id}
            onClick={() => setAcct(x.id)}
            className={`min-w-[7rem] flex-1 shrink-0 rounded-lg border px-3 py-2 text-left text-sm ${x.id === a.id ? "border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900" : "border-slate-300 dark:border-slate-700"}`}
          >
            <span className="block font-semibold">가상 {x.id}</span>
            <span className="block truncate text-xs opacity-80">
              {won(x.view.equity)} · {pctText(x.view.pnlPct)}
            </span>
          </button>
        ))}
      </div>
      <p className="mt-2 text-xs text-slate-500">
        {a.name} · 규칙서 판 {a.rule_version} · {ruleText(a)}
      </p>
      <div className="mt-3 flex gap-1 overflow-x-auto border-b border-slate-200 text-sm dark:border-slate-800" role="tablist" aria-label="계좌 화면">
        {SUB_TABS.filter(([k]) => k !== "committee" || hasCommittee(a)).map(([k, label]) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={sub === k}
            onClick={() => setSub(k)}
            className={`shrink-0 px-3 py-2 ${sub === k ? "border-b-2 border-slate-900 font-medium dark:border-slate-100" : "text-slate-500"}`}
          >
            {label}
            {k === "holdings" ? ` ${a.positions.length}` : k === "trades" ? ` ${체결.length >= FILLS_PER_ACCOUNT ? `${FILLS_PER_ACCOUNT}+` : 체결.length}` : k === "orders" ? ` ${주문.length}` : ""}
          </button>
        ))}
      </div>
      <div className="mt-3">
        {sub === "summary" ? <SummaryPane a={a} /> : null}
        {sub === "holdings" ? <HoldingsPane a={a} /> : null}
        {sub === "trades" ? <TradesPane key={a.id} fills={체결} /> : null}
        {sub === "realized" ? <PaperRealized key={a.id} account={a.id} /> : null}
        {sub === "orders" ? <OrdersPane orders={주문} /> : null}
        {sub === "report" ? <ReportPane a={a} /> : null}
        {sub === "rules" ? <RulesPane a={a} spans={desk ? (desk.rule_history?.[a.id] ?? []) : null} asOf={desk?.as_of ?? null} /> : null}
        {sub === "skips" ? <SkipsPane key={a.id} account={a.id} /> : null}
        {sub === "committee" && hasCommittee(a) ? (
          <CommitteePane a={a} meetings={(data.meetings ?? []).filter((m) => m.account_id === a.id)} dossiers={data.dossiers ?? []} />
        ) : null}
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
    // 원화 합이라 해외 종목은 환차가 섞여 있다 — 나눈 값은 보유 표(USD 환차 뺀 %)와 실현손익 탭(25.1724, 웹 감사 [권고] 4)
    ["평가손익 (보유분, 아직 안 판 것 · 해외는 환차 포함)", won(v.unrealized), v.unrealized],
    ["실현손익 (판 것, 비용 반영 · 해외는 환차 포함)", won(v.realized), v.realized],
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
              <td className={`text-right ${p.unknown_why ? "text-slate-400" : tone(p.unrealized_krw)}`}>
                {p.unknown_why ? (
                  <span className="text-xs">원가로 평가 — {p.unknown_why}</span>
                ) : (
                  <>
                    {won(p.unrealized_krw ?? null)}
                    <span className="block">
                      {pctText(p.unrealized_pct)}
                      {p.currency && p.currency !== "KRW" ? " 원화" : ""}
                    </span>
                    {p.currency && p.currency !== "KRW" && p.unrealized_local_pct != null ? (
                      <span className={`block text-[10px] ${tone(p.unrealized_local_pct)}`}>
                        {pctText(p.unrealized_local_pct)} {p.currency}(환차 뺌)
                      </span>
                    ) : null}
                  </>
                )}
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

/** 거래내역 — 20건씩 쪽으로(2026-10-23 사용자 지시 "거래내역 탭이 점점 길어져", 25.1767). 계좌를 바꾸면 첫 쪽(부르는 쪽이 `key`) */
export function TradesPane({ fills }: { fills: FillRow[] }) {
  const [page, setPage] = useState(0);
  if (!fills.length) return <p className="text-sm text-slate-500">아직 체결이 없습니다.</p>;
  const p = pageOf(fills, page);
  return (
    <>
    <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
      {p.rows.map((f) => {
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
    <Pager p={p} onPage={setPage} />
    </>
  );
}

function OrdersPane({ orders }: { orders: OrderRow[] }) {
  if (!orders.length) return <p className="text-sm text-slate-500">체결 대기 주문이 없습니다.</p>;
  return (
    <div className="text-sm">
      <p className="mb-1 text-xs text-slate-500">
        가상모의투자는 장중에 체결하지 않습니다. 판정할 장이 끝난 뒤 그 시장의 다음 배치(국내는 다음 거래일 아침, 미국은 다음 미국 배치)가 그날 일봉(시가·저가·종가)으로
        찼는지 정합니다 — 그 전까지는 모두 여기 남습니다. 실제 주문 경로의 체결은 KIS모의투자(docs/paper.md 15장)가 따로 잽니다.
      </p>
      <ul className="space-y-1">
      {orders.map((o) => (
        <li key={o.id}>
          {o.name} ·{" "}
          {o.side === "sell"
            ? `매도(${o.reason}) 시장가`
            : o.order_type === "market"
              ? `매수 시가 일괄 (기준가 ${money(o.ref_price ?? null, o.currency)}${o.reason === "무작위 대조" ? " · 무작위 대조" : ""})`
              : `매수 ${o.tranche}회차 ${money(o.limit_price, o.currency)} 이하`}{" "}
          ·{" "}
          {o.quantity.toLocaleString("ko-KR")}주 · {o.fill_session} 장의 일봉으로 다음 배치가 판정
        </li>
      ))}
      </ul>
    </div>
  );
}

interface MinutesItem {
  stock_id: number; ticker: string; vote: number; favor: number; issue: boolean; passed: boolean; approved: boolean; reason: string;
  vetoed_by: string[]; minority: string[]; opinions: Record<string, unknown[]>;
}

/**
 * 투자위원회 (docs/company.md, 2026-10-15 사용자 지시 "부서들의 연구와 조사를 통해 서로 회의하고 토론해서 결정", 25.1469).
 * 헌장(철학·부서 가중·주관 부서·거부권)과 시장마다 최근 회의록 — 안건마다 부서 입장(▲찬성·▼반대 + 확신, · 중립, – 자료 없음)·표·결정·소수의견.
 * 값은 배치가 적은 그대로. 부서 근거(조사 자료)는 행을 펼쳐 본다.
 */
export function CommitteePane({ a, meetings, dossiers }: { a: Account; meetings: MeetingRow[]; dossiers: DossierRow[] }) {
  let 헌장: { name?: string; philosophy?: string; weights?: Record<string, number>; lead?: string; veto?: string[];
    threshold?: number; agenda?: number; top_n?: number; max_mdd?: number | null } = {};
  try {
    헌장 = JSON.parse(a.charter_json ?? "{}");
  } catch {
    헌장 = {};
  }
  const 부서 = Object.entries(헌장.weights ?? {}).filter(([, w]) => Number(w) > 0);
  return (
    <div className="space-y-4 text-sm">
      <div>
        <p className="font-semibold">{헌장.name}</p>
        <p className="text-slate-600 dark:text-slate-400">“{헌장.philosophy}”</p>
        <p className="mt-1 text-xs text-slate-500">
          부서 가중: {부서.map(([k, w]) => `${DEPT_LABEL[k] ?? k} ${w}`).join(" · ")} · 주관 {DEPT_LABEL[헌장.lead ?? ""] ?? 헌장.lead}
          {헌장.veto?.length ? ` · 거부권 ${헌장.veto.map((k) => DEPT_LABEL[k] ?? k).join("·")}` : ""}
          {헌장.max_mdd != null ? ` (MDD ${Math.round(헌장.max_mdd * 100)}%)` : ""} · 가결 표 ≥ {헌장.threshold} · 찬성 부서 ≥ 2 · 안건 {헌장.agenda}
          개 중 최대 {헌장.top_n}종목
        </p>
      </div>
      {meetings.length === 0 ? <p className="text-slate-500">아직 회의록이 없습니다 — 다음 아침 배치부터 날마다 열립니다.</p> : null}
      {meetings.map((m) => {
        let 록: { memo?: string; items?: MinutesItem[]; as_of?: string } = {};
        try {
          록 = JSON.parse(m.minutes_json);
        } catch {
          록 = {};
        }
        // 근거는 시장의 **최신** 조사 자료 한 벌뿐 — 이 회의의 조사 기준일(`as_of`, 25.1582 부터 회의록에 적힘)과 다르면 싣지 않는다.
        // 그 계좌의 회의가 하루 빠졌으면 다른 날 근거를 이 회의의 것처럼 보였다(25.1582, 위원회 감사 [참고])
        const 시장근거 = dossiers.filter((d) => d.market === m.market);
        const 근거날 = 시장근거[0]?.as_of ?? null;
        const 맞음 = !록.as_of || 록.as_of === 근거날;
        const 근거 = new Map(맞음 ? 시장근거.map((d) => [Number(d.stock_id), d.dossier_json]) : []);
        return (
          <section key={`${m.market}-${m.decided_on}`}>
            <h3 className="font-medium">
              {m.market} · {m.decided_on} 회의
            </h3>
            {록.memo ? <p className="text-xs text-slate-600 dark:text-slate-400">{록.memo}</p> : null}
            {!맞음 && 근거날 ? (
              <p className="text-xs text-amber-700 dark:text-amber-400">
                이 회의의 조사 기준일은 {록.as_of} — 남은 조사 자료는 {근거날} 것뿐이라 근거를 펼치지 않습니다.
              </p>
            ) : null}
            <div className="mt-1 overflow-x-auto">
              <table className="w-full min-w-[640px] text-xs">
                <thead className="text-slate-500">
                  <tr>
                    <th className="py-1 text-left">종목</th>
                    {부서.map(([k]) => (
                      <th key={k} className="text-center">{(DEPT_LABEL[k] ?? k).replace("부", "")}</th>
                    ))}
                    <th className="text-right">표</th>
                    <th className="text-left">결정</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {(록.items ?? []).map((i) => (
                    <tr key={i.stock_id} className={i.approved ? "font-medium" : ""}>
                      <td className="py-1">
                        {i.ticker}
                        {근거.has(Number(i.stock_id)) ? (
                          <details>
                            <summary className="cursor-pointer text-slate-400">근거 {근거날 ? `(조사 기준 ${근거날})` : ""}</summary>
                            <pre className="whitespace-pre-wrap text-[10px] text-slate-500">{근거.get(Number(i.stock_id))}</pre>
                          </details>
                        ) : null}
                      </td>
                      {부서.map(([k]) => (
                        <td key={k} className="text-center">{stanceText(i.opinions[k])}</td>
                      ))}
                      <td className="text-right">{Number(i.vote).toFixed(2)}</td>
                      <td>
                        {i.approved ? "가결·집행" : i.passed ? "가결(상위 밖)" : i.reason.replace(/^위원회:\s*/, "부결 — ")}
                        {i.minority.length ? ` · 소수의견 ${i.minority.map((k) => DEPT_LABEL[k] ?? k).join("·")}` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
      <div>
        <h3 className="font-medium">부서 성적표</h3>
        <p className="text-xs text-slate-500">
          어느 부서가 맞았나 — 그 부서가 찬성한 안건과 반대한 안건의 20 거래일 뒤 지수 대비 초과. 가름(찬성 − 반대)이 클수록 잘 가린 부서입니다. 같은 종목은 28일에 한 번만 셉니다.
        </p>
        {a.attribution?.departments?.length ? (
          <ul className="mt-1 space-y-0.5 text-xs">
            {a.attribution.departments.map((d) => (
              <li key={d.dept}>
                {DEPT_LABEL[d.dept] ?? d.dept}: {deptScoreText(d)}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-slate-500">아직 표본이 없습니다 — 회의 뒤 20 거래일이 지나야 채워집니다.</p>
        )}
      </div>
      <p className="text-xs text-slate-500">부서는 규칙으로 판단하는 에이전트입니다 — 같은 자료면 같은 의견이 나옵니다(docs/company.md 0장).</p>
    </div>
  );
}

/**
 * 판 이력 (docs/desk.md 3장, 25.1502·25.1505) — 배치가 판을 처음 본 결정일로 나눈 구간과 그 구간 성적. 배치가 낸 값 그대로.
 * `spans` null = 운용 요약을 못 읽음, [] = 요약은 있는데 이 계좌의 판 이력이 아직 없음
 */
export function RulesPane({ a, spans, asOf }: { a: Account; spans: RuleSpan[] | null; asOf: string | null }) {
  return (
    <div className="text-sm">
      <p>
        지금 규칙서 판 <b>{a.rule_version}</b> — {ruleText(a)}
      </p>
      {spans === null ? (
        <p className="mt-2 text-xs text-slate-500">운용 요약을 읽지 못했거나 아직 없습니다 — 판 이력은 운용 요약에서 읽습니다.</p>
      ) : spans.length === 0 ? (
        <p className="mt-2 text-xs text-slate-500">아직 이 계좌의 판 이력이 없습니다 — 다음 일일 배치가 지금 판부터 적습니다.</p>
      ) : (
        <ul className="mt-2 space-y-1">
          {spans.map((r) => (
            <li key={r.version} className={r.vs_r_pp == null ? "" : tone(r.vs_r_pp)}>
              {ruleSpanText(r)}
              {preregText(r) ? <span className="block text-[11px] text-slate-500">{preregText(r)}</span> : null}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-xs text-slate-500">
        판은 규칙서·헌장을 바꿀 때마다 올립니다(docs/paper.md 2장). 구간은 배치가 그 판을 처음 본 결정일의 종가부터 다음 판의 그날 종가까지("처음" = 계좌를 연
        날부터) — 몇 주 차이는 대부분 운이라
        같은 구간 무작위 대조(R) 평균과 함께 봅니다{asOf ? ` · ${asOf} 운용 요약 기준` : ""}.
      </p>
    </div>
  );
}

/** 안 산 것 (왜 안 샀나, docs/desk.md 3장, 25.1502) — 결정 원장의 안 한 결정과 그 뒤. 탭을 열 때만 읽는다 */
function SkipsPane({ account }: { account: string }) {
  const [market, setMarket] = useState<"KR" | "US">("KR");
  const [rows, setRows] = useState<DecisionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let 살아있음 = true;
    setRows(null);
    setError(null);
    void (async () => {
      const r = await readJson<{ rows?: DecisionRow[] }>(`/api/paper/decisions?account=${encodeURIComponent(account)}&market=${market}`);
      const body = bodyOf<{ rows?: DecisionRow[] }>(r);
      if (!살아있음) return;
      // 못 읽은 것을 없는 것으로 말하지 않는다 — 로그인이 끊기면 401 {error} 라 errors 가 없다(25.163, 25.1505 검토 5)
      if (!r.ok || body.errors?.length) setError(body.errors?.join(" · ") || r.error || "읽지 못했습니다");
      else if (!Array.isArray(body.rows)) setError("응답 꼴이 다름 — 새로 고침해 주세요");
      else setRows(body.rows);
    })();
    return () => {
      살아있음 = false;
    };
  }, [account, market]);
  return (
    <div className="text-sm">
      <div className="flex gap-1" role="tablist" aria-label="시장">
        {(["KR", "US"] as const).map((m) => (
          <button
            key={m}
            type="button"
            role="tab"
            aria-selected={market === m}
            onClick={() => setMarket(m)}
            className={`rounded px-3 py-1 text-xs ${market === m ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900" : "border border-slate-300 dark:border-slate-700"}`}
          >
            {MARKET_LABEL[m]}
          </button>
        ))}
      </div>
      <p className="mt-2 text-xs text-slate-500">
        안 낸 후보·위험 점검이 막은 것·안 찬 회차·매도 플래그를 따르지 않고 든 것과 그 뒤(지수가 있는 가장 긴 찬 창). 안 산 것이 지수보다 올랐으면 놓친 것(빨강),
        덜 올랐으면 잘 거른 것(파랑). <b>보유 쪽</b> 줄(플래그 무시·매도 막힘·이미 들고 있어 더 안 삼)은 거꾸로 — 오르면 들고 간 것이 옳았으니 초록. 같은 종목·같은 까닭이
        되풀이되면(까닭의 수만 다르면 같은 까닭) 한 줄로 묶고 그 뒤는 첫 결정 것. 하나하나는 운이고, 까닭별 묶음 성적은 성적표 탭에 있습니다. 최근 60줄과 그 뒤가 찬 최근 60줄(결정 뒤 7~120일, 사이 날은 읽지 않아 묶음의 "보인 줄" 수는 실제보다 적을 수 있습니다).
      </p>
      {error ? <p className="mt-2 text-red-600">{error}</p> : null}
      {!rows && !error ? <p className="mt-2 text-slate-500">불러오는 중…</p> : null}
      {rows && rows.length === 0 ? <p className="mt-2 text-slate-500">이 시장에서 안 한 결정이 아직 없습니다.</p> : null}
      {rows?.length ? (
        <ul className="mt-2 divide-y divide-slate-100 dark:divide-slate-800">
          {groupDecisions(rows).map(({ first, last, count }) => {
            const 이김 = beatBench(first);
            const 보유 = holdSide(last);
            const 운영 = operationalSkip(last);
            const 색 = 이김 == null || 운영 ? "text-slate-500" : 보유 ? (이김 ? "text-emerald-700 dark:text-emerald-400" : "text-amber-700 dark:text-amber-400") : 이김 ? "text-red-600 dark:text-red-400" : "text-blue-600 dark:text-blue-400";
            return (
              <li key={last.id} className="py-1.5">
                <span className="text-xs text-slate-500">
                  {count > 1 ? `${first.decided_on}~${last.decided_on} · 보인 줄 ${count}번` : last.decided_on} · {KIND_TEXT[last.kind] ?? last.kind}
                  {last.rule_version != null ? ` · 판 ${last.rule_version}` : ""}
                  {보유 ? " · 보유 쪽" : ""}
                  {운영 ? " · 운영 사정(고른 종목)" : ""}
                </span>{" "}
                <b>{last.name}</b> <span className="text-xs text-slate-400">{last.ticker}</span>
                <span className="block text-xs text-slate-600 dark:text-slate-400">{last.reason}</span>
                {first.reason !== last.reason ? (
                  <span className="block text-xs text-slate-500">첫 결정({first.decided_on}) 까닭: {first.reason}</span>
                ) : null}
                <span className={`block text-xs ${색}`}>
                  {count > 1 ? "첫 결정 뒤 " : "그 뒤 "}
                  {afterText(first)}
                </span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

function ReportPane({ a }: { a: Account }) {
  const t = a.attribution;
  if (!t) return <p className="text-sm text-slate-500">아직 성과가 찬 결정이 없습니다 — 결정 뒤 20 거래일이 지나야 채워집니다.</p>;
  return (
    <div className="text-sm">
      <p className="text-xs text-slate-500">
        노하우 성적표 — {t.horizon} 거래일 뒤, 지수 대비 초과(%p). 표본 30건 미만은 참고만.
        {t.as_of ? ` 기준 ${t.as_of}.` : ""}
        {t.rule_version != null ? ` 판 ${t.rule_version} 결정만 셉니다 — 판을 올리면 처음부터 다시 쌓입니다.` : ""}
      </p>
      <ul className="mt-1 space-y-0.5 text-slate-700 dark:text-slate-300">
        <li>
          선정: 주문 낸 새 후보 {attrCellText(t.selection)} · 안 낸 새 후보 {attrCellText(t.skipped)}
          {t.add_on?.n ? ` · 이미 든 종목 추가 매수 ${attrCellText(t.add_on)}` : ""}
        </li>
        {Object.entries(t.skip_by_reason ?? {}).map(([k, v]) => (
          <li key={k}>안 낸 까닭 — {k}: {attrCellText(v)}</li>
        ))}
        <li>진입: 체결가 대비 그날 종가 {entryCellText(t.entry)} · 안 찬 회차의 그 뒤 {attrCellText(t.missed)}</li>
        <li>청산: 판 뒤 {attrCellText(t.sold)} · 플래그를 따르지 않고 든 뒤 {attrCellText(t.held_flag)} (판 뒤가 음수면 판 것이 옳았다)</li>
        {t.blocked?.n ? <li>위험 점검이 막은 매수의 그 뒤: {attrCellText(t.blocked)} (오르면 막은 것이 비용)</li> : null}
        {t.blocked_sell?.n ? <li>위험 점검이 막은 매도의 그 뒤: {attrCellText(t.blocked_sell)} (내리면 막은 것이 비용)</li> : null}
        {virtualText(t.virtual) ? <li>가상 대조: 산 종목이 {virtualText(t.virtual)}</li> : null}
      </ul>
    </div>
  );
}
