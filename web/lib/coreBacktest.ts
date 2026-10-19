/**
 * 핵심 H 다년 백테스트 결과 (docs/backtest.md 10장, docs/infra.md 25.1545) — 배치가 낸 값을 글로만 만든다.
 *
 * 읽는 것: 마지막 핵심 H 실행 기록 한 줄(`batch_runs.job_name = 'backtest_core'`, 국내)과 그 묶음의 결과 행 다섯.
 * 판정(`step_log.verdict`)은 **첫 국내 기본 실행 하나로만** 본다는 사전 등록 규칙이 있다 — 화면은 그 말을 함께 적는다.
 */

import { luckText, metricNumber } from "@/lib/backtest";

export const CORE_JOB = "backtest_core";
/** 마지막 핵심 H 실행 기록 — 색인 `idx_batch_runs_job (job_name, started_at DESC)` 로 한 줄 */
export const LATEST_CORE_RUN = `SELECT id, status, started_at, finished_at, step_log, error_text
FROM batch_runs WHERE job_name = '${CORE_JOB}' AND market = 'KR' ORDER BY started_at DESC LIMIT 1`;

/** 변형 이름 — 운영 계좌와 같은 뜻(docs/backtest.md 10장 변형 표) */
export const CORE_LABEL: Record<string, string> = {
  core_h: "핵심 H (합의)",
  core_g: "G (B 상위 5 + 추적 매도)",
  core_b: "B (상위 5 + 기본 매도)",
  core_r1: "R1 (무작위 대조)",
  core_r2: "R2 (무작위 대조)",
};

export interface CoreRunRow {
  strategy: string;
  metrics_json: string;
  excess_cagr: number | null;
}

export interface CoreLine {
  strategy: string;
  label: string;
  cagr: number | null;
  mdd: number | null;
  excess: number | null;
  luck: { cell: string; title: string | null };
  /** 무작위 대조 분위 — 평균·한쪽 하한·묶음 수 */
  pick: string;
  /** 관문과 같은 잣대(같은 날 안 산 새 후보 안 분위) */
  gate: string;
  /** 판 뒤 20 거래일 KOSPI 대비(낮을수록 잘 판 것) */
  afterSell: string;
}

function obj(raw: unknown): Record<string, unknown> {
  if (typeof raw === "string") {
    try {
      const p = JSON.parse(raw);
      return p && typeof p === "object" ? (p as Record<string, unknown>) : {};
    } catch {
      return {};
    }
  }
  return raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const f1 = (v: number | null): string => (v == null ? "-" : v.toFixed(1));
const signed = (v: number | null, unit = "%p"): string => (v == null ? "-" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}${unit}`);

/** 분위 글 — "평균 54.2 · 하한 51.0 (묶음 8)". 묶음이 모자라 하한이 없으면 "하한 -" */
export function quantileText(e: unknown): string {
  const o = obj(e);
  const n = num(o.n_blocks);
  if (!n) return "아직 없음";
  return `평균 ${f1(num(o.mean))} · 하한 ${f1(num(o.lower))} (묶음 ${n})`;
}

export function afterSellText(e: unknown): string {
  const o = obj(e);
  const k = num(o.sells);
  if (!k) return "매도 없음";
  return `${signed(num(o.mean))} · 매도 ${k}건 (묶음 ${num(o.n_blocks) ?? 0})`;
}

/** 결과 행 다섯 → 표 줄(변형 순서는 CORE_LABEL 순) */
export function coreLines(runs: CoreRunRow[]): CoreLine[] {
  const order = Object.keys(CORE_LABEL);
  return runs
    .filter((r) => r.strategy in CORE_LABEL)
    .sort((a, b) => order.indexOf(a.strategy) - order.indexOf(b.strategy))
    .map((r) => {
      const m = obj(r.metrics_json);
      const ev = obj(m.evidence);
      return {
        strategy: r.strategy,
        label: CORE_LABEL[r.strategy],
        cagr: metricNumber(m, "cagr"),
        mdd: metricNumber(m, "mdd"),
        excess: r.excess_cagr,
        luck: luckText(m),
        pick: quantileText(ev),
        gate: quantileText(ev.gate),
        afterSell: afterSellText(ev.after_sell),
      };
    });
}

/** 판정·짝·가중 대조 몇 줄 (실행 기록 `step_log`) */
export function coreNotes(stepLog: unknown): string[] {
  const s = obj(stepLog);
  const out: string[] = [];
  const params = obj(s.params);
  const years = num(params.years);
  const 기본 = years === 5 && params.risk_every === "week";
  if (typeof s.verdict === "string") {
    out.push(`판정: ${s.verdict}${기본 ? "" : " — 기본 실행(5년·리스크 주마다)이 아니라 참고만"} (사전 등록 — 첫 국내 기본 실행 하나로만 본다)`);
  }
  const pair = obj(obj(s.pairs)["core_h-core_g"]);
  if (num(pair.n_blocks)) {
    out.push(`H−G 짝(합의 선정의 몫, 20 거래일 KOSPI 대비): 평균 ${signed(num(pair.mean))} · 하한 ${signed(num(pair.lower))} (묶음 ${num(pair.n_blocks)})`);
  }
  const eq = obj(s.holding_eq);
  if (typeof eq.error === "string") out.push(`가중 대 동일가중: 진단 오류 — ${eq.error}`);
  else if (typeof eq.verdict === "string") {
    const 뜻: Record<string, string> = { keep: "가중 유지", equal: "동일가중 후보(판 올림 절차)", none: "지금대로", undecidable: "판정 불가" };
    out.push(
      `가중 대 동일가중: ${뜻[eq.verdict] ?? eq.verdict} — 고른 종목이 갈린 날 ${num(eq.differ_days) ?? 0}일(${f1(num(eq.differ_pct))}%), 짝 ${num(eq.pairs) ?? 0}, 하한 ${signed(num(eq.lower))}`,
    );
  }
  const t = obj(s.timing);
  const 변형초 = Object.values(obj(t.variants_s)).map(num).filter((x): x is number => x != null);
  if (변형초.length) out.push(`걸린 시간: 읽기 ${f1(num(t.load_s))}초 · 변형 ${f1(변형초.reduce((a, b) => a + b, 0))}초 · 판정 ${f1(num(t.evidence_s))}초`);
  return out;
}
