/**
 * KIS모의투자 실행 품질 — 가상 체결 판정이 얼마나 낙관적인가 (docs/paper.md 15.8, docs/desk.md 4장, docs/infra.md 25.1504).
 *
 * 배치가 날마다 `paper_broker_daily.summary_json` 에 적는 28일 창 비교 요약(체결 일치율·실현율·가격 차이)을 시장마다 계열로 읽는다.
 * 웹은 계산하지 않는다 — 그래프에 넣을 때 % 를 소수로 바꾸는 것만(그래프의 % 꼴이 소수를 받는다).
 */

import type { Series } from "@/components/EquityChart";
import { apartDigits } from "@/lib/apart";

/**
 * 괴리 알림 문턱 — `batch/services/broker_mirror.py` 의 DIVERGE_* 와 같은 값(테스트가 대조, 25.1507). 화면 설명 글에만 쓴다
 */
export const DIVERGE = {
  MATCH_PCT: 80,
  KEPT_PCT: 85,
  SLIP_PCT: 0.5,
  MIN_N: 20,
  COMPARE_DAYS: 28,
} as const;

/** 체결 칸을 "잰 것" 으로 보는 거래일 수 — `broker_mirror.py` 의 CELL_MIN_DAYS 와 같은 값(테스트가 대조, 25.1514) */
export const CELL_MIN_DAYS = 20;

/** 추이를 읽는 기간(달력일) — 반년. 시장마다 하루 한 줄이라 최대 약 180행 */
export const QUALITY_DAYS = 180;

export const QUALITY_SQL = `SELECT as_of, account_id,
  json_extract(summary_json, '$.n') AS n,
  json_extract(summary_json, '$.match_pct') AS match_pct,
  json_extract(summary_json, '$.kept_pct') AS kept_pct,
  json_extract(summary_json, '$.slip_buy.avg') AS slip_buy,
  json_extract(summary_json, '$.slip_sell.avg') AS slip_sell,
  json_extract(summary_json, '$.rejected') AS rejected,
  json_extract(summary_json, '$.unknown') AS unknown,
  json_extract(summary_json, '$.cells') AS cells
FROM paper_broker_daily WHERE broker = ? AND as_of >= ? ORDER BY as_of`;

export interface QualityRow {
  as_of: string;
  account_id?: string | null;
  n: number | null;
  match_pct: number | null;
  kept_pct: number | null;
  slip_buy: number | null;
  slip_sell: number | null;
  rejected: number | null;
  unknown?: number | null;
  /** 체결 칸 JSON(25.1514 전 줄에는 없다) */
  cells?: string | null;
}

/**
 * 지금 베끼는 계좌의 줄만 — 마지막 줄과 같은 `account_id` 인 줄(25.1642). 도전자 칸(`KR2`·`US2`)은 도전자를 바꾸면
 * 같은 증권사 키에 앞 계좌와 새 계좌의 비교 요약이 이어 쌓여 한 선으로 그려졌다(배치 비교는 이미 계좌로 거른다, `COMPARE_SQL`).
 * 마지막 줄에 계좌가 없으면(옛 줄) 그대로
 */
export function currentAccountRows(rows: QualityRow[]): QualityRow[] {
  const 지금 = rows[rows.length - 1]?.account_id;
  return 지금 ? rows.filter((r) => r.account_id === 지금) : rows;
}

/** 배치 `bm.cells` 의 칸 하나 (docs/paper.md 15.9) */
export interface Cell {
  n: number;
  days: number;
  both: number;
  internal_only: number;
  kis_only: number;
  neither: number;
  unknown: number;
  miss: number | null;
  extra: number | null;
  n_price: number;
  excess: number | null;
  median: number | null;
  se: number | null;
  /** 보정에 쓰는 잣대(거래일·excess·se) — 배치가 적는다. 25.1516 전 줄에는 없어 거래일로만 본다 */
  measured?: boolean;
}
export const CELL_LABEL: Record<string, string> = {
  buy_market: "시장가 매수",
  buy_limit: "지정가 매수",
  sell_market: "시장가 매도",
  sell_limit: "지정가 매도",
};

/**
 * 체결 칸 표의 줄 — 마지막 날 요약에서. 칸 이름 순(시장가 매수·지정가 매수·매도), 모르는 칸은 키 그대로.
 * `measured` = 거래일 `CELL_MIN_DAYS` 이상 — 그 전 값은 "아직 표본 적음" 으로 흐리게 보인다
 */
export function cellRows(
  r: QualityRow | undefined,
): Array<{ key: string; label: string; cell: Cell; measured: boolean }> {
  if (!r?.cells) return [];
  let c: Record<string, Cell>;
  try {
    c = JSON.parse(r.cells) as Record<string, Cell>;
  } catch {
    return [];
  }
  if (!c || typeof c !== "object") return [];
  const 순서 = Object.keys(CELL_LABEL);
  return Object.entries(c)
    .filter(([, v]) => v && typeof v === "object")
    .sort(
      ([a], [b]) =>
        (순서.indexOf(a) < 0 ? 99 : 순서.indexOf(a)) -
          (순서.indexOf(b) < 0 ? 99 : 순서.indexOf(b)) || a.localeCompare(b),
    )
    .map(([key, cell]) => ({
      key,
      label: CELL_LABEL[key] ?? key,
      cell,
      measured: cell.measured ?? Number(cell.days) >= CELL_MIN_DAYS,
    }));
}

/** 장부 가정 넘는 비용 — "+0.20% ±0.06 (중앙 +0.18, 가격 n=34)". 지정가 매수는 가격 개선이라 음수가 정상 */
export function excessText(c: Cell): string {
  if (c.excess == null) return "가격 표본 없음";
  const f = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
  const se = c.se == null ? "" : ` ±${(2 * c.se).toFixed(2)}`;
  return `${f(c.excess)}${se} (중앙 ${c.median == null ? "-" : f(c.median)}, 가격 n=${c.n_price})`;
}

/** 일치율·실현율 추이 — 끝난 주문이 없는 날(n 0·없음)은 뺀다 */
export function qualitySeries(rows: QualityRow[]): Series[] {
  const 찬 = rows.filter((r) => Number(r.n) > 0);
  const 선 = (
    label: string,
    color: string,
    pick: (r: QualityRow) => number | null,
  ): Series => ({
    label,
    color,
    points: 찬.flatMap((r) => {
      const v = pick(r);
      return v == null ? [] : [{ date: r.as_of, value: Number(v) / 100 }];
    }),
  });
  return [
    선("체결 일치율", "#2563eb", (r) => r.match_pct),
    선("실현율(가상 체결이 KIS 에서도 찬 비율)", "#dc2626", (r) => r.kept_pct),
  ].filter((s) => s.points.length);
}

/** 가격 차이 추이 — 불리한 쪽이 +(가상이 낙관). 표본 없는 날은 뺀다 */
export function slipSeries(rows: QualityRow[]): Series[] {
  const 선 = (
    label: string,
    color: string,
    pick: (r: QualityRow) => number | null,
  ): Series => ({
    label,
    color,
    points: rows.flatMap((r) => {
      const v = pick(r);
      return v == null ? [] : [{ date: r.as_of, value: Number(v) / 100 }];
    }),
  });
  return [
    선("매수 가격 차이", "#ea580c", (r) => r.slip_buy),
    선("매도 가격 차이", "#7c3aed", (r) => r.slip_sell),
  ].filter((s) => s.points.length);
}

/**
 * 마지막 날 한 줄 — "지난 28일 끝난 주문 34 · 체결 일치 82% · 실현율 90%(…) · 모름 2건(빠짐)".
 * 25.1504 에는 "할인율" 이라 불렀다 — 100 이 "깎을 것 없음" 이라 이름이 뜻과 반대였다(25.1507, 검토 3).
 * 모름(미국 예약이 개장 때 전송 안 됨 등)은 일치·실현율에서 빠져 실현율을 높게 기울인다 — 건수를 따로 보인다(검토 6)
 */
export function qualityLine(r: QualityRow | undefined): string {
  if (!r || !Number(r.n)) {
    // 견줄 주문이 0 이어도 배치가 모름·거부로 닫은 주문은 있다 — 미국 예약이 개장 때 전송 안 된 날이 그렇다(25.1617, KIS 웹 감사 [필수])
    const 닫힘 = [
      Number(r?.unknown) > 0 ? `모름 ${r?.unknown}건` : "",
      Number(r?.rejected) > 0 ? `거부 ${r?.rejected}건` : "",
    ].filter(Boolean);
    if (r && 닫힘.length)
      return `${r.as_of} 기준 지난 ${DIVERGE.COMPARE_DAYS}일 체결을 견줄 주문 없음 · ${닫힘.join(" · ")}(일치·실현율에서 빠짐)`;
    return "아직 끝난 베낀 주문이 없습니다 — 낸 다음 거래일의 같은 시장 배치가 체결을 맞춥니다.";
  }
  // 알림 문턱과 같아 보이지 않게(79.6 이 "80%" 로 — 바로 위 설명은 "80% 아래로 내려가면 알립니다", 25.1637)
  const pct = (v: number | null, t: number) =>
    v == null ? "-" : `${Number(v).toFixed(apartDigits(Number(v), t, 0))}%`;
  const 실현 =
    r.kept_pct == null
      ? "실현율 모름(가상이 찬 주문 없음)"
      : `실현율 ${pct(r.kept_pct, DIVERGE.KEPT_PCT)}(가상 체결 가운데 KIS 에서도 찬 비율)`;
  const 모름 =
    Number(r.unknown) > 0 ? ` · 모름 ${r.unknown}건(일치·실현율에서 빠짐)` : "";
  return `${r.as_of} 기준 지난 ${DIVERGE.COMPARE_DAYS}일 끝난 주문 ${r.n} · 체결 일치 ${pct(r.match_pct, DIVERGE.MATCH_PCT)} · ${실현}${모름}`;
}
