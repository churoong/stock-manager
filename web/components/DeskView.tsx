"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { bodyOf, readJson } from "@/lib/http";
import {
  MARKET_LABEL,
  allChecks,
  countsText,
  fillLine,
  kisOffText,
  kisStatusText,
  notSentText,
  orderLine,
  pctText,
  ppText,
  type DeskMarket,
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

const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" }) : "-");

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
      if (body.errors?.length) setError(body.errors.join(" · "));
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
              개설일부터 같은 구간 R 과 견줍니다
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
