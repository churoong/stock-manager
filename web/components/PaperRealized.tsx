"use client";

import { useEffect, useState } from "react";
import { bodyOf, readJson } from "@/lib/http";
import { kstToday } from "@/lib/paper";
import { PERIODS, periodRange, realizedReport, type PeriodKey, type RealizedRow } from "@/lib/paperRealized";
import { won } from "@/lib/portfolio";

/** 매도 수 칸 — 실현손익을 모르는 매도가 있으면 따로(25.1722) */
export function countCell(g: { n: number; unknown: number }): string {
  return g.unknown ? `${g.n} + 모름 ${g.unknown}` : String(g.n);
}

/** 실현손익 칸 — 아는 매도가 없으면 0원이 아니라 "모름"(25.1722, 웹 감사 [필수] 1) */
export function realizedCell(g: { n: number; unknown: number; realized: number }): string {
  return !g.n && g.unknown ? "모름" : won(g.realized);
}

/** 묶음의 환차 칸 — 모르는 매도가 섞였으면 아는 것만 더했다고 밝힌다(25.1695) */
function fxCell(g: { fx: number; fxUnknown: number }): string {
  if (g.fxUnknown) return `${g.fx ? won(g.fx) : ""}${g.fx ? " + " : ""}모름 ${g.fxUnknown}건`;
  return g.fx ? won(g.fx) : "—";
}

/**
 * 머리 칸 이름 — KIS모의투자는 비용 전 값이라 숫자 옆에 밝힌다(25.1698, 25.1694 교차검증 [권고] 2:
 * 비용을 뺀 가상과 견주면 KIS 가 왕복 비용만큼 늘 좋아 보인다)
 */
export function headline(costs: boolean): string {
  return costs ? "기간 실현손익" : "기간 실현손익(비용 전)";
}

function tone(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v) || v === 0) return "";
  return v > 0 ? "text-red-600 dark:text-red-400" : "text-blue-600 dark:text-blue-400";
}

/**
 * 가상모의투자 실현손익 탭 (docs/paper.md 6장, docs/infra.md 25.1693, 2026-10-22 사용자 지시 "실현손익이나 기간실현손익" ·
 * "기간을 조회하면 해당 기간 내 일자별 실현손익도"). 값은 배치가 매도 때 적은 그대로 — 화면은 기간 안을 더하고 묶기만 한다.
 */
export default function PaperRealized({ account }: { account: string }) {
  return (
    <RealizedView
      url={(from, to) => `/api/paper/realized?account=${encodeURIComponent(account)}&from=${from}&to=${to}`}
      costs
      note="실현손익 = 판 금액 − 평균 원가(매수·매도 수수료·세금·슬리피지 반영)이고, 해외 종목은 환차손익을 주가 손익과 나눠 적습니다(환차 = 현지 통화 원가 × (판 날 환율 − 산 평균 환율)). 값은 배치가 매도 때 적은 그대로입니다."
      unknownNote="팔 때 장부에 보유가 없던 것"
    />
  );
}

/**
 * KIS모의투자 실현손익 (docs/paper.md 15장, docs/infra.md 25.1694) — **우리 주문 기록**(체결 수량·평균 체결가)의 평균 원가.
 * KIS 가 매긴 수수료·세금은 기록에 없어 빠진다 `[확인필요]`
 */
export function KisRealized({ market }: { market: "KR" | "US" }) {
  return (
    <RealizedView
      url={(from, to) => `/api/paper/kis/realized?market=${market}&from=${from}&to=${to}`}
      costs={false}
      byTicker
      note="KIS모의투자 계좌에서 실제로 찬 매도의 실현손익 = 체결 금액 − 평균 원가(우리가 낸 주문의 체결 수량·평균 체결가로 셈). KIS 가 매긴 수수료·세금은 기록에 없어 빠집니다(비용 전 값). 미국은 체결일 환율로 원화로 바꾸고 환차손익을 나눠 적습니다. 값은 일일 배치가 맞추기·잔고 조회 뒤 적은 그대로입니다."
      unknownNote="아직 세지 않았거나(다음 배치) 원가를 모르는 것 — 환율이 없던 미국 매수, KIS 잔고가 우리 기록보다 많던 동안(기록 밖 매수·기록 전 보유)의 매도. KIS 잔고가 0 이 된 뒤 새로 산 것부터 다시 셉니다"
    />
  );
}

function RealizedView({ url, costs, note, unknownNote, byTicker = false }: {
  url: (from: string, to: string) => string; costs: boolean; note: string; unknownNote: string; byTicker?: boolean;
}) {
  const 오늘 = kstToday();
  const [period, setPeriod] = useState<PeriodKey>("this_month");
  const [custom, setCustom] = useState({ from: `${오늘.slice(0, 7)}-01`, to: 오늘 });
  // 읽은 기간과 함께 둔다 — 기간을 바꾼 첫 그림에 앞 기간 숫자가 새 제목 아래 한 번 보이지 않게(25.1706)
  const [읽음, setRows] = useState<{ key: string; rows: RealizedRow[] } | null>(null);
  const [오류, setError] = useState<{ key: string; text: string } | null>(null);
  const [from, to] = periodRange(period, 오늘, custom);
  const 거꾸로 = from > to;
  useEffect(() => {
    if (거꾸로) return;
    let 살아있음 = true;
    setRows(null);
    setError(null);
    void (async () => {
      const r = await readJson<{ rows?: RealizedRow[] }>(url(from, to));
      const body = bodyOf<{ rows?: RealizedRow[] }>(r);
      if (!살아있음) return;
      // 못 읽은 것을 "실현손익 0" 으로 말하지 않는다(25.163·25.1505 검토 5 와 같은 규칙)
      const 열쇠 = `${from}|${to}`;
      if (!r.ok || body.errors?.length) setError({ key: 열쇠, text: body.errors?.join(" · ") || r.error || "읽지 못했습니다" });
      else if (!Array.isArray(body.rows)) setError({ key: 열쇠, text: "응답 꼴이 다름 — 새로 고침해 주세요" });
      else setRows({ key: `${from}|${to}`, rows: body.rows });
    })();
    return () => {
      살아있음 = false;
    };
    // `url` 은 부를 때마다 새 함수라 의존에 넣지 않는다 — 바뀌는 것은 계좌·시장(부모가 key 로 다시 그린다)과 기간뿐
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, to, 거꾸로]);
  // 시작 > 끝이면 앞 기간 숫자를 그 제목 아래 보이지 않는다(25.1695, 교차검증 [권고] 1)
  const rows = 읽음 && 읽음.key === `${from}|${to}` ? 읽음.rows : null;
  // 오류도 그 기간 것만(25.1708, 25.1706 교차검증 [참고]) — 앞 기간 오류가 새 기간·뒤집힌 기간 아래 남지 않게
  const error = 오류 && 오류.key === `${from}|${to}` && !거꾸로 ? 오류.text : null;
  const 보고 = rows && !거꾸로 ? realizedReport(rows, byTicker) : null;
  return (
    <div className="text-sm">
      <div className="flex flex-wrap gap-1" role="tablist" aria-label="기간">
        {PERIODS.map(([k, label]) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={period === k}
            onClick={() => setPeriod(k)}
            className={`rounded px-3 py-1 text-xs ${period === k ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900" : "border border-slate-300 dark:border-slate-700"}`}
          >
            {label}
          </button>
        ))}
      </div>
      {period === "custom" ? (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <label className="flex items-center gap-1">
            시작
            <input type="date" value={custom.from} max={오늘} onChange={(e) => setCustom((c) => ({ ...c, from: e.target.value }))}
              className="rounded border border-slate-300 px-2 py-1 dark:border-slate-700 dark:bg-slate-900" />
          </label>
          <label className="flex items-center gap-1">
            끝
            <input type="date" value={custom.to} max={오늘} onChange={(e) => setCustom((c) => ({ ...c, to: e.target.value }))}
              className="rounded border border-slate-300 px-2 py-1 dark:border-slate-700 dark:bg-slate-900" />
          </label>
        </div>
      ) : null}
      <p className="mt-2 text-xs text-slate-500">
        {from} ~ {to} 에 체결한 매도(체결일은 그 시장 날짜). {note}
      </p>
      {거꾸로 ? <p className="mt-3 text-amber-700 dark:text-amber-400">시작이 끝보다 늦습니다.</p> : null}
      {error ? <p className="mt-3 text-red-600 dark:text-red-400">실현손익을 읽지 못했습니다 — {error}</p> : null}
      {!거꾸로 && !error && !보고 ? <p className="mt-3 text-slate-500">읽는 중…</p> : null}
      {보고 ? (
        <>
          <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1">
            <dt className="text-slate-500">{headline(costs)}</dt>
            {/* 모르는 매도뿐이면 "매도 없음" 이 아니라 "모름"(25.1722, 웹 감사 [필수] 1 — KIS 기록 밖 보유 동안 자주 생긴다) */}
            <dd className={`text-right font-semibold ${tone(보고.n ? 보고.realized : null)}`}>
              {보고.n ? won(보고.realized) : 보고.unknown ? `모름 ${보고.unknown}건` : "매도 없음"}
            </dd>
            {보고.hasForeign ? (
              <>
                <dt className="text-slate-500">그 가운데 주가 손익 · 환차손익</dt>
                <dd className="text-right">
                  {!보고.n ? (
                    "—"
                  ) : 보고.fxUnknown ? (
                    `환차 몫 모름 ${보고.fxUnknown}건(다음 배치가 채우거나, 다른 원가로 적힌 옛 매도라 비어 있음)`
                  ) : (
                    <>
                      <span className={tone(보고.price)}>{won(보고.price)}</span> · <span className={tone(보고.fx)}>{won(보고.fx)}</span>
                    </>
                  )}
                </dd>
              </>
            ) : null}
            <dt className="text-slate-500">매도 건수 (이익 · 손실)</dt>
            <dd className="text-right">
              {보고.n}건 ({보고.wins} · {보고.losses}){보고.n ? ` · 이긴 비율 ${Math.round((보고.wins / 보고.n) * 100)}%` : ""}
              {보고.unknown ? ` + 실현손익 모름 ${보고.unknown}건` : ""}
            </dd>
            {costs ? (
              <>
                <dt className="text-slate-500">매도 비용(수수료·세금·슬리피지)</dt>
                <dd className="text-right">
                  {Object.keys(보고.costs).length
                    ? Object.entries(보고.costs).map(([c, v]) => (c === "KRW" ? won(v) : `${v.toFixed(2)} ${c}`)).join(" · ")
                    : "—"}
                </dd>
              </>
            ) : null}
          </dl>
          {보고.unknown ? (
            <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">실현손익이 비어 있는 매도 {보고.unknown}건은 합에 넣지 않았습니다({unknownNote}).</p>
          ) : null}
          {보고.byDay.length ? (
            <div className="mt-4 overflow-x-auto">
              <h3 className="text-xs font-semibold text-slate-600 dark:text-slate-300">일자별 실현손익</h3>
              <table className="mt-1 w-full text-xs">
                <thead className="text-slate-500">
                  <tr>
                    <th className="text-left font-normal">체결일</th>
                    <th className="text-right font-normal">매도</th>
                    <th className="text-right font-normal">실현손익</th>
                    {보고.hasForeign ? <th className="text-right font-normal">그 가운데 환차</th> : null}
                    <th className="text-right font-normal">누적</th>
                  </tr>
                </thead>
                <tbody>
                  {보고.byDay.map((d) => (
                    <tr key={d.key} className="border-t border-slate-100 dark:border-slate-800">
                      <td className="py-1">{d.label}</td>
                      <td className="text-right">{countCell(d)}</td>
                      <td className={`text-right ${tone(d.n ? d.realized : null)}`}>{realizedCell(d)}</td>
                      {보고.hasForeign ? <td className={`text-right ${tone(d.fx)}`}>{fxCell(d)}</td> : null}
                      <td className={`text-right ${tone(d.cumulative)}`}>{won(d.cumulative)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {보고.byStock.length ? (
            <div className="mt-4 overflow-x-auto">
              <h3 className="text-xs font-semibold text-slate-600 dark:text-slate-300">종목별 실현손익</h3>
              <table className="mt-1 w-full text-xs">
                <thead className="text-slate-500">
                  <tr>
                    <th className="text-left font-normal">종목</th>
                    <th className="text-right font-normal">매도</th>
                    <th className="text-right font-normal">실현손익</th>
                    {보고.hasForeign ? <th className="text-right font-normal">그 가운데 환차</th> : null}
                  </tr>
                </thead>
                <tbody>
                  {보고.byStock.map((s) => (
                    <tr key={s.key} className="border-t border-slate-100 dark:border-slate-800">
                      <td className="py-1">{s.label}</td>
                      <td className="text-right">{countCell(s)}</td>
                      <td className={`text-right ${tone(s.n ? s.realized : null)}`}>{realizedCell(s)}</td>
                      {보고.hasForeign ? <td className={`text-right ${tone(s.fx)}`}>{fxCell(s)}</td> : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
