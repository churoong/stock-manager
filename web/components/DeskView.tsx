"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { bodyOf, readJson } from "@/lib/http";
import {
  MARKET_LABEL,
  allChecks,
  DESK_RULES,
  countsText,
  fillLine,
  gateNowText,
  kisOffText,
  kisStatusText,
  notSentText,
  orderLine,
  pctText,
  ppText,
  READINESS_TEXT,
  type DeskMarket,
  type ReadinessItem,
  type DeskMarketKey,
  type DeskView as Desk,
} from "@/lib/desk";
import { amountText, perfLines, statusText } from "@/lib/kisPaper";

/**
 * 운용 상황실 — 첫 화면 (docs/desk.md 2장, docs/infra.md 25.1501).
 * 가상모의투자·KIS모의투자가 오늘 무엇을 했고 어디가 이상한지를 한 화면에. 숫자는 배치가 낸 그대로다.
 */

const CARD = "rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900";

function tone(v: number | null | undefined): string {
  if (v == null || v === 0) return "";
  return v > 0 ? "text-red-600 dark:text-red-400" : "text-blue-600 dark:text-blue-400";
}

// 한국 시간으로 고정한다(25.1722, 웹 감사 [권고] 5) — 기기 시간대로 그려 해외에선 실행 날짜가 판정일 앞날로 보였다
export const when = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short", timeZone: "Asia/Seoul" }) : "-";

/** 실전 준비 목록 진도 (docs/paper.md 14장, 25.1513) — 성적 밖에서 남은 거리 */
export function ReadinessList({ items }: { items: ReadinessItem[] }) {
  const 색: Record<ReadinessItem["status"], string> = {
    done: "text-emerald-700 dark:text-emerald-400",
    partial: "text-amber-700 dark:text-amber-400",
    rehearsal: "text-sky-700 dark:text-sky-400",
    todo: "text-slate-500",
    user: "text-violet-700 dark:text-violet-400",
  };
  return (
    <div className="mt-3 border-t border-slate-100 pt-2 dark:border-slate-800">
      <p className="text-sm font-medium">
        실전 준비 목록 — 갖춤 {items.filter((i) => i.status === "done").length}/{items.length}
      </p>
      <ul className="mt-1 space-y-1 text-xs">
        {items.map((i) => (
          <li key={i.key}>
            <span className={`font-medium ${색[i.status] ?? ""}`}>[{READINESS_TEXT[i.status] ?? i.status}]</span> {i.label} — {i.note}
            {i.evidence ? <span className="block text-slate-500">근거: {i.evidence}</span> : null}
          </li>
        ))}
      </ul>
      <p className="mt-1 text-xs text-slate-500">일곱이 모두 갖춰져도 실전 주문은 열리지 않습니다 — 단계 관문과 사용자 결정.</p>
    </div>
  );
}

export function MarketCard({ m, k, mirror }: { m: DeskMarket & { as_of: string }; k: DeskMarketKey; mirror: string }) {
  const off = kisOffText(m.kis_step);
  const 안냄 = notSentText(m.kis_step);
  // 모양이 바뀐 옛 요약에도 터지지 않게 (25.1503, 검토 7)
  const fills = m.fills_h ?? [];
  const orders = m.orders_h ?? [];
  const kisOrders = m.kis_orders ?? [];
  return (
    <section className={CARD}>
      <h2 className="font-semibold">
        {MARKET_LABEL[k]} <span className="text-xs font-normal text-slate-500">실행 {when(m.run_at)} · 판정 {m.trade_date} · 다음 장 {m.session_date}</span>
      </h2>
      <p className="mt-2 text-sm font-medium">{mirror} 체결 (판정한 날 {m.trade_date})</p>
      {fills.length ? (
        <ul className="mt-1 space-y-0.5 text-sm">
          {fills.map((f, i) => (
            <li key={i} className={f.side === "buy" ? "text-red-700 dark:text-red-300" : "text-blue-700 dark:text-blue-300"}>
              {fillLine(f)}
              {f.fill_date && f.fill_date !== m.trade_date ? <span className="text-xs text-slate-500"> · {f.fill_date} 장(밀린 체결)</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-sm text-slate-500">없음</p>
      )}
      <p className="mt-3 text-sm font-medium">{mirror} 이 {m.session_date} 장에 낼 주문</p>
      {orders.length ? (
        <ul className="mt-1 space-y-1 text-sm">
          {orders.map((o, i) => (
            <li key={i}>
              {orderLine(o)}
              {o.fill_session && o.fill_session !== m.session_date ? <span className="text-xs text-amber-700 dark:text-amber-400"> · {o.fill_session} 장</span> : null}
              {o.reason ? <span className="block text-xs text-slate-500">{o.reason}</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-sm text-slate-500">없음</p>
      )}
      <p className="mt-3 text-xs text-slate-500">
        모든 계좌 체결 {countsText(m.fills_by_account)} · 다음 장 주문 {countsText(m.orders_by_account)}
      </p>
      <p className="mt-3 text-sm font-medium">KIS모의투자 ({mirror} 베낌)</p>
      {off ? (
        <p className="mt-1 text-sm text-amber-700 dark:text-amber-400">보내지 않음 — {off}</p>
      ) : (
        <>
          <p className="mt-1 text-sm">{kisStatusText(kisOrders)}</p>
          {kisOrders.some((o) => o.status !== "accepted" && o.status !== "filled") ? (
            <ul className="mt-1 space-y-0.5 text-xs text-slate-500">
              {kisOrders
                .filter((o) => o.status !== "accepted" && o.status !== "filled")
                .map((o, i) => (
                  <li key={i}>
                    {statusText(o.status)} · {o.name} {o.quantity}주{o.message ? ` — ${o.message}` : ""}
                  </li>
                ))}
            </ul>
          ) : null}
        </>
      )}
      {안냄 ? <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">안 낸 주문 — {안냄}</p> : null}
    </section>
  );
}

export default function DeskView() {
  const [desk, setDesk] = useState<Desk | null>(null);
  const [ready, setReady] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const r = await readJson<{ ready: boolean; desk: Desk }>("/api/desk");
      const body = bodyOf<{ ready: boolean; desk: Desk }>(r);
      if (!r.ok || body.errors?.length) setError(body.errors?.join(" · ") || r.error || "운용 요약을 읽지 못했습니다");
      else if (!body.desk) setError("운용 요약을 읽지 못했습니다(응답 꼴이 다름 — 새로 고침해 주세요)");
      else {
        setReady(body.ready !== false);
        setDesk(body.desk);
      }
    })();
  }, []);

  if (error) return <p className="text-sm text-rose-600">{error}</p>;
  if (!desk) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  if (!ready || !desk.as_of)
    return (
      <section className={CARD}>
        <p className="text-sm">아직 운용 요약이 없습니다 — 일일 배치가 한 번 돈 뒤부터 보입니다.</p>
        <p className="mt-2 text-sm">
          지금은 <Link href="/paper" className="underline">전략(가상모의투자)</Link> 화면에서 계좌별 기록을 볼 수 있습니다.
        </p>
      </section>
    );

  const checks = allChecks(desk.markets, desk.as_of);
  const lb = desk.leaderboard;
  const mirror = desk.mirror_account ?? "H";
  const kis = desk.kis_account;
  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
        {desk.as_of} 운용 요약 · 마지막 갱신 {when(desk.updated_at)} · 실전 주문은 없습니다(가상모의투자·KIS모의투자만)
      </p>

      <section className={CARD}>
        <h2 className="font-semibold">확인할 일</h2>
        {checks.length ? (
          <ul className="mt-2 space-y-1 text-sm">
            {checks.map((c, i) => (
              <li key={i} className={c.level === "high" ? "font-medium text-rose-600 dark:text-rose-400" : "text-amber-700 dark:text-amber-400"}>
                {c.level === "high" ? "● " : "○ "}
                <span className="text-xs text-slate-500">
                  {MARKET_LABEL[c.market]}
                  {c.ran ? ` ${c.ran} 실행` : ""}
                </span>{" "}
                {c.text}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-emerald-700 dark:text-emerald-400">없음 — 두 시장 마지막 실행에서 이상이 보고되지 않았습니다</p>
        )}
      </section>

      <section className={CARD}>
        <h2 className="font-semibold">전략 순위</h2>
        {lb?.rows?.length ? (
          <>
            <p className="mt-1 text-xs text-slate-500">
              공통 구간 {lb.window_start}부터의 시간가중 수익률 순 · R 대비 = 그 수익률 − 무작위 대조 R1·R2 평균
              {lb.control_pct != null ? `(${pctText(lb.control_pct)})` : "(대조 기록 없음)"} — 운인가 실력인가. 갓 연 계좌(평가 20번 전)는 아래에, 제
              개설일부터 같은 구간 R 과 견줍니다. "따로 잼"(스윙 S)은 다른 실험이라 공통 구간·챔피언 후보 밖, 제 개설일부터입니다
            </p>
            <div className="overflow-x-auto">
              <table className="mt-2 w-full min-w-[520px] text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500">
                    <th className="py-1">계좌</th>
                    <th>전략</th>
                    <th className="text-right">공통 구간</th>
                    <th className="text-right">R 대비</th>
                    <th className="text-right">개설 이후</th>
                    <th className="text-right">개설</th>
                  </tr>
                </thead>
                <tbody>
                  {lb.rows.map((r) => (
                    <tr key={r.id} className={`border-t border-slate-100 dark:border-slate-800 ${r.id === mirror ? "bg-amber-50 dark:bg-amber-950/30" : ""}`}>
                      <td className="py-1">
                        <b>{r.id}</b> {r.name !== r.id ? <span className="text-xs text-slate-500">{r.name}</span> : null}
                      </td>
                      <td className="text-xs">
                        {r.kind}
                        {r.young ? <span className="ml-1 text-amber-700 dark:text-amber-400">갓 엶 · {r.open}부터</span> : null}
                        {r.separate ? <span className="ml-1 text-sky-700 dark:text-sky-400">따로 잼 · {r.open}부터</span> : null}
                      </td>
                      <td className={`text-right ${tone(r.window_pct)}`}>{pctText(r.window_pct)}</td>
                      <td className={`text-right ${tone(r.vs_r_pp)}`}>{ppText(r.vs_r_pp)}</td>
                      <td className={`text-right ${tone(r.since_open_pct)}`}>{pctText(r.since_open_pct)}</td>
                      <td className="text-right text-xs text-slate-500">{r.open}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <p className="mt-2 text-sm text-slate-500">아직 평가 기록이 있는 계좌가 없습니다</p>
        )}
        <p className="mt-2 text-sm">
          <Link href="/paper" className="underline">계좌별 자세히 →</Link>
        </p>
      </section>

      {desk.gate ? (
        <section className={CARD}>
          <h2 className="font-semibold">
            실전 전환 {desk.gate.stage}단계 관문 —{" "}
            <span className={desk.gate.passed ? "text-emerald-700 dark:text-emerald-400" : "text-slate-600 dark:text-slate-300"}>
              {desk.gate.passed ? "통과(다음 단계는 사용자 결정)" : `${desk.gate.items.filter((i) => i.ok).length}/${desk.gate.items.length} 충족`}
            </span>
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            잣대는 KIS 가 베끼는 계좌 {desk.gate.account}
            {desk.gate.version != null ? `의 지금 판 ${desk.gate.version}` : ""}
            {desk.gate.from ? ` · ${desk.gate.from}부터(관문 자기 구간)` : ""}. 읽기 전용입니다 — 넘어도 실전 주문은 열리지 않습니다. 문턱은 첫 값(docs/desk.md 6장).
          </p>
          <ul className="mt-2 space-y-0.5 text-sm">
            {desk.gate.items.map((i) => (
              <li key={i.key} className={i.ok ? "text-emerald-700 dark:text-emerald-400" : ""}>
                {i.ok ? "✓" : "·"} {i.label}: {gateNowText(i)}
              </li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-slate-500">그 뒤: {desk.gate.next}</p>
          {desk.readiness.length ? <ReadinessList items={desk.readiness} /> : null}
        </section>
      ) : null}

      {desk.gate_error ? (
        <section className={CARD}>
          <p className="text-sm text-red-600 dark:text-red-400">실전 전환 관문 등 일부를 내지 않음 — {desk.gate_error}</p>
        </section>
      ) : null}

      {desk.risk.length ? (
        <section className={CARD}>
          <h2 className="font-semibold">위험판</h2>
          <div className="overflow-x-auto">
            <table className="mt-2 w-full min-w-[560px] text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-500">
                  <th className="py-1">계좌</th>
                  <th className="text-right">노출</th>
                  <th className="text-right">낙폭</th>
                  <th className="pl-2">가장 큰 종목</th>
                  <th className="pl-2">손절 가까움</th>
                </tr>
              </thead>
              <tbody>
                {desk.risk.map((r) => (
                  <tr key={r.id} className="border-t border-slate-100 align-top dark:border-slate-800">
                    <td className="py-1 font-semibold">{r.id}</td>
                    <td className="text-right">{r.exposure_pct == null ? "-" : `${r.exposure_pct.toFixed(0)}%`}</td>
                    <td className={`text-right ${tone(r.dd_pct)}`}>{pctText(r.dd_pct)}</td>
                    <td className="pl-2 text-xs">
                      {r.top_name ? `${r.top_name} ${r.top_weight_pct?.toFixed(1)}%` : "-"} <span className="text-slate-400">({r.positions}종목)</span>
                    </td>
                    <td className="pl-2 text-xs text-amber-700 dark:text-amber-400">
                      {r.near_stop.length ? r.near_stop.map((x) => `${x.name ?? "?"} ${x.unrealized_pct.toFixed(1)}%(선 ${x.stop_pct}%)`).join(" · ") : <span className="text-slate-400">없음</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            노출 = 보유 평가 ÷ 평가 총액, 낙폭 = 시간가중 지수의 지난 고점 대비, 손절 가까움 = 종목 통화로 종가 ÷ 평균단가가 기간 손절선까지 {DESK_RULES.RISK_NEAR_STOP_PP}%p 안(매도
            플래그와 같은 잣대 · 손절을 쓰지 않는 계좌도 같은 선으로).
          </p>
        </section>
      ) : null}

      <section className={CARD}>
        <h2 className="font-semibold">KIS모의투자 계좌</h2>
        <p className="mt-1 text-sm">
          계좌 전체 원화 총액 <b>{amountText(kis?.account_krw?.total_krw, "KRW")}</b>
          <span className="text-xs text-slate-500"> · {kis?.as_of ?? "-"} 국내 배치 기준</span>
        </p>
        <ul className="mt-1 space-y-0.5 text-xs text-slate-600 dark:text-slate-300">
          {perfLines(kis?.perf ?? null).map((l, i) => (
            <li key={i}>{l}</li>
          ))}
        </ul>
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        {(["KR", "US"] as const).map((k) => {
          const m = desk.markets[k];
          return m ? (
            <MarketCard key={k} m={m} k={k} mirror={mirror} />
          ) : (
            <section key={k} className={CARD}>
              <h2 className="font-semibold">{MARKET_LABEL[k]}</h2>
              <p className="mt-2 text-sm text-slate-500">아직 이 시장 실행 요약이 없습니다</p>
            </section>
          );
        })}
      </div>
    </div>
  );
}
