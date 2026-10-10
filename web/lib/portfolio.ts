/**
 * 매매 기록 · 배당 수령 · 포트폴리오 조회 (docs/portfolio.md, Step 12).
 *
 * 웹은 **원본만 쓴다**(trades, dividend_receipts). 손익·보유·평가는 batch/jobs/portfolio.py 가 계산해
 * 파생 표에 넣고, 웹은 그것을 읽어 그린다(CLAUDE.md "웹앱은 계산하지 않는다", 사용자 결정 2026-09-17).
 * 저장하면 GitHub Actions 를 깨워 1~2분 안에 다시 계산한다. 그 사이는 "계산 중" 이다.
 */

import { z } from "zod";
import { confirmFlags, type ConfirmKind } from "@/lib/confirm";
import { readAmount } from "@/lib/numberInput";

// ----------------------------------------------------------------------
// 입력 검증
// ----------------------------------------------------------------------

/**
 * **달력에 있는 날짜만** (docs/infra.md 25.311). 예전에는 모양만 봐서 `2026-02-30`·`2025-13-45` 가 저장됐고,
 * 그 뒤 포트폴리오 배치가 `date.fromisoformat` 에서 매번 죽어 화면이 "계산 중" 에 머물렀다.
 * 매매 기록은 사용자 것이라 배치가 고칠 수 없다(CLAUDE.md) — 들어오는 문에서 막는다.
 */
export function isRealDate(s: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const d = new Date(`${s}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}

/**
 * 이보다 앞선 날짜는 오타로 본다 (docs/infra.md 25.554, 감사 재현). 미래만 막아 연도를 "26" 만 친 `0026-09-01` 이
 * 저장됐고, 가장 오래된 매수가 되어 선입선출의 첫 묶음·수익률 시작일(0026년)이 됐다. 코스피 기준일(1980-01-04)
 * 앞으로는 이 앱이 다루는 매매가 없다
 */
export const MIN_TRADE_DATE = "1980-01-01";

const isoDate = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, "날짜는 YYYY-MM-DD")
  .refine(isRealDate, "달력에 없는 날짜입니다")
  .refine((s) => s >= MIN_TRADE_DATE, `${MIN_TRADE_DATE} 보다 앞선 날짜입니다 — 연도를 확인해 주세요`);

/**
 * 금액·수량 칸의 상한 (docs/infra.md 25.316). 오타를 잡는 그물이지 실제 한계가 아니다.
 * 예전에는 양수인지만 봐서 `price: 1e308` 이 들어갔고, 배치에서 `price × quantity` 가 inf 가 되면
 * 파이썬 `json.dumps` 는 `Infinity` 를 적는데 웹의 `JSON.parse` 는 그것을 읽지 못한다 — 포트폴리오 화면이 깨진다.
 * 1조(10^12)는 국내 한 주 값·원화 총액을 모두 넉넉히 넘는다.
 */
export const MAX_AMOUNT = 1e12;
const 금액 = (msg: string) => z.number().positive(msg).max(MAX_AMOUNT, "값이 너무 큽니다 (오타인지 확인해 주세요)");
const 비용 = () => z.number().min(0).max(MAX_AMOUNT, "값이 너무 큽니다 (오타인지 확인해 주세요)");

export const tradeInputSchema = z.object({
  stock_id: z.number().int().positive(),
  side: z.enum(["buy", "sell"]),
  trade_date: isoDate,
  price: 금액("체결가는 0 보다 커야 합니다"),
  quantity: 금액("수량은 0 보다 커야 합니다"),
  /** 미국 종목만. 비우면 그날 USDKRW 종가로 채운다 */
  fx_rate: 금액("환율은 0 보다 커야 합니다").nullable().optional(),
  fee: 비용().nullable().optional(),
  tax: 비용().nullable().optional(),
  horizon: z.enum(["short", "mid", "long"]).nullable().optional(),
  memo: z.string().max(200).nullable().optional(),
  /** 방금 같은 매매가 저장됐다는 409 를 본 뒤 **그래도 저장**을 고른 요청 (docs/infra.md 25.785) */
  confirm_duplicate: z.boolean().optional(),
  /** 체결가가 종가와 100배 넘게 달라 409 를 본 뒤 **그래도 저장** (역분할 100:1 넘는 종목의 옛 매매, docs/infra.md 25.789) */
  confirm_price: z.boolean().optional(),
}).superRefine((v, ctx) => {
  // 수수료·세금이 체결금액보다 크면 오타다 — 대개 미국 종목에 원화로 적은 것 (docs/infra.md 25.399).
  // 수수료 $1,500 가 $1,000 매수에 붙으면 원가가 2.5배가 되어 평가손익 −60% 가 떴다
  const gross = v.price * v.quantity;
  // **매도는 수수료 + 세금 합도 본다** (25.554, 감사 재현) — 따로만 봐서 100 매도에 수수료 60·세금 60 이 통과했고,
  // 배치의 매도대금이 −20 이 되어 실현손익이 −100% 를 넘었다
  if (v.side === "sell" && (v.fee ?? 0) + (v.tax ?? 0) > gross) {
    ctx.addIssue({ code: "custom", path: ["tax"], message: "수수료와 세금의 합이 체결금액보다 큽니다 — 종목 통화로 적었는지 확인해 주세요" });
  }
  // 매수 세금은 배치가 쓰지 않는다 — 저장하면 기록과 계산이 조용히 어긋난다(25.265 는 폼만 막았다, 25.554)
  if (v.side === "buy" && (v.tax ?? 0) > 0) {
    ctx.addIssue({ code: "custom", path: ["tax"], message: "매수에는 세금을 적지 않습니다 (거래세는 매도에 붙습니다)" });
  }
  for (const key of ["fee", "tax"] as const) {
    const x = v[key];
    if (typeof x === "number" && x > gross) {
      ctx.addIssue({
        code: "custom",
        path: [key],
        message: `${key === "fee" ? "수수료" : "세금"}가 체결금액(체결가 × 수량)보다 큽니다 — 종목 통화로 적었는지 확인해 주세요`,
      });
    }
  }
});
export type TradeInput = z.infer<typeof tradeInputSchema>;

/**
 * **제자리에서 고칠 수 있는 칸** — 메모·수수료·세금 (docs/infra.md 25.1116, 웹 매매 감사 재현). 고치기가 "지우고 다시 넣기"
 * 뿐이라 다시 넣은 매수가 새 id 를 받았고, 배치는 같은 날 매수를 id 순으로 선입선출해(`same_day_order`) 메모 하나만
 * 고쳐도 짝짓기 순서가 바뀌었다 — 같은 날 70,000·80,000 매수 뒤 매도의 실현손익이 +5만 → −5만, 남은 평단 80,000 →
 * 70,000. 날짜·종목·수량·체결가·투자 기간은 매수 근거 스냅샷과 묶여 있어(넣는 순간의 것) 여전히 지우고 다시 넣는다
 */
export const tradeEditSchema = z.object({
  memo: z.string().max(200).nullable().optional(),
  fee: 비용().nullable().optional(),
  tax: 비용().nullable().optional(),
}).strict();

/** 기존 매매에 고친 칸을 덧씌워 **넣을 때와 같은 규칙**으로 다시 본다. 오류 문장 목록(없으면 빈 목록) */
export function tradeEditErrors(
  row: { stock_id: number; side: string; trade_date: string; price: number; quantity: number; fee: number | null; tax: number | null; memo: string | null },
  edit: z.infer<typeof tradeEditSchema>,
): string[] {
  const 합 = { ...row, ...edit, side: row.side as "buy" | "sell" };
  const r = tradeInputSchema.safeParse({
    stock_id: 합.stock_id, side: 합.side, trade_date: 합.trade_date, price: 합.price, quantity: 합.quantity,
    fee: 합.fee, tax: 합.tax, memo: 합.memo,
  });
  return r.success ? [] : r.error.issues.map((i) => i.message);
}

export const dividendInputSchema = z.object({
  stock_id: z.number().int().positive(),
  pay_date: isoDate,
  amount_per_share: 비용().nullable().optional(),
  quantity: 비용().nullable().optional(),
  gross_amount: 비용(),
  tax: 비용().default(0),
  fx_rate: 금액("환율은 0 보다 커야 합니다").nullable().optional(),
  memo: z.string().max(200).nullable().optional(),
  /** 방금 같은 배당이 있다는 확인을 보고도 저장한다 (25.829) */
  confirm_duplicate: z.boolean().optional(),
  /** 넣은 원금보다 큰 배당이라는 확인을 보고도 저장한다 — 기록 전부터 든 주식의 배당일 수 있다 (25.934) */
  confirm_size: z.boolean().optional(),
});
export type DividendInput = z.infer<typeof dividendInputSchema>;

/**
 * 방금(10분 안) 같은 배당 — 같은 종목·지급일·세전·세금 (docs/infra.md 25.829, 감사). 저장 응답이 끊긴 뒤 다시 누르면 같은 배당이 두 행이 되어
 * 배당 합계와 TWR 에 두 번 들어갔다(25.785 가 매매만 막았다). 인자: [종목, 지급일, 세전, 세금, 이후 시각]
 */
export const RECENT_SAME_DIVIDEND = `SELECT created_at FROM dividend_receipts
WHERE stock_id = ? AND pay_date = ? AND gross_amount = ? AND tax = ? AND created_at >= ?
ORDER BY created_at DESC LIMIT 1`;

/**
 * 매매 폼이 보내는 본문 — **화면에 보이는 칸만 싣는다** (docs/infra.md 25.265).
 *
 * 세금 칸은 매도에서만, 환율 칸은 원화가 아닌 종목에서만 보인다. 그런데 폼은 상태에 남은 값을 그대로 보냈다 —
 * 매도에서 세금을 적고 매수로 바꾸면 칸은 사라져도 세금이 매수 기록에 저장됐고(배치는 매수 세금을 무시한다),
 * 음수를 적어 두었다면 **보이지 않는 칸 때문에** 저장이 거절됐다. 미국 종목에서 원화 종목으로 바꾼 뒤의 환율도 같다.
 * trades 는 사용자 입력만 담는 표다(CLAUDE.md) — 사용자가 볼 수 없는 값을 넣지 않는다.
 */
export function tradeBody(f: {
  stockId: number; currency: string; side: "buy" | "sell"; date: string;
  price: number | null; quantity: number | null; fx: number | null; fee: number | null; tax: number | null;
  horizon: "short" | "mid" | "long"; memo: string;
  /** 사용자가 확인 창에서 "그래도 저장" 한 종류들 (docs/infra.md 25.945, `lib/confirm`) */
  confirmed?: ReadonlySet<ConfirmKind>;
}) {
  return {
    stock_id: f.stockId, side: f.side, trade_date: f.date, price: f.price, quantity: f.quantity,
    fx_rate: f.currency !== "KRW" ? f.fx : null,
    fee: f.fee,
    tax: f.side === "sell" ? f.tax : null,
    horizon: f.horizon, memo: f.memo || null,
    ...confirmFlags(f.confirmed ?? new Set()),
  };
}

/** 배당 폼의 본문. 환율 칸은 원화가 아닌 종목에서만 보인다 (docs/infra.md 25.265) */
export function dividendBody(f: {
  stockId: number; currency: string; date: string; gross: number | null; tax: number | null; fx: number | null;
  confirmed?: ReadonlySet<ConfirmKind>;
}) {
  return {
    stock_id: f.stockId, pay_date: f.date, gross_amount: f.gross, tax: f.tax ?? 0,
    fx_rate: f.currency !== "KRW" ? f.fx : null,
    ...confirmFlags(f.confirmed ?? new Set()),
  };
}

export { readAmount };

/** 채운 칸 가운데 숫자로 못 읽은 첫 칸의 오류 문장. 없으면 null */
export function unreadableAmount(칸: Record<string, string>): string | null {
  for (const [이름, 글] of Object.entries(칸)) {
    const v = readAmount(글);
    if (v !== null && !Number.isFinite(v)) return `${이름} "${글}" 를 숫자로 읽지 못했습니다`;
  }
  return null;
}

/** 미래 날짜 매매는 받지 않는다. 오늘은 한국 시각 기준. */
export function isFutureDate(isoDay: string, now: Date = new Date()): boolean {
  const kst = new Date(now.getTime() + 9 * 3600 * 1000).toISOString().slice(0, 10);
  return isoDay > kst;
}

// ----------------------------------------------------------------------
// 환율 자동 채움 (docs/portfolio.md 3장)
// ----------------------------------------------------------------------

/** 이보다 오래된 환율은 자동으로 채우지 않는다. batch/services/fx.MAX_STALE_DAYS 와 같다. */
export const FX_MAX_STALE_DAYS = 7;

export const FX_ON_OR_BEFORE = `SELECT date, rate FROM fx_rates WHERE pair = 'USDKRW' AND date <= ? ORDER BY date DESC LIMIT 1`;

export function fxUsable(rateDate: string | null, tradeDate: string): boolean {
  if (!rateDate) return false;
  const days = (Date.parse(tradeDate) - Date.parse(rateDate)) / 86_400_000;
  return days >= 0 && days <= FX_MAX_STALE_DAYS;
}

/**
 * 이 기록에 쓸 환율을 정한다 (사용자 결정 2026-09-17).
 *
 * 원화는 1. 미국은 **입력값(증권사 체결 환율)이 우선**, 없으면 그날 USDKRW 종가.
 * 쓸 만한 종가도 없으면 거절한다 — 없는 환율로 환차손익을 계산하지 않는다.
 *
 * **왜 한 곳으로 모았나** (2026-09-21, docs/infra.md 25.72). 이 규칙이 매매 기록과 배당
 * 수령 두 경로에 **똑같이 베껴져** 있었다. 지금은 같지만 한쪽만 고쳐지면 같은 날 같은 종목의
 * 두 기록이 **서로 다른 환율**로 남는다. 환차손익은 그 환율로 계산되므로 조용히 어긋난다.
 * `fx_rate` 는 스키마가 `NOT NULL CHECK (fx_rate > 0)` 이라 잘못 넘기면 DB 가 막지만,
 * 막아 주는 것은 "비었는가" 뿐이고 "맞는 값인가" 는 아니다.
 */
export function decideFx(
  currency: string,
  manual: number | null | undefined,
  /** `FX_ON_OR_BEFORE` 가 찾아 준 행. 없으면 undefined */
  stored: { date: string; rate: number } | undefined,
  /** 기록의 날짜 (체결일·지급일) */
  onDate: string,
): { rate: number; source: "none" | "manual" | "auto" } | { error: "no-rate" } {
  if (currency === "KRW") return { rate: 1, source: "none" };
  if (manual) return { rate: manual, source: "manual" };
  if (!stored || !fxUsable(stored.date, onDate)) return { error: "no-rate" };
  return { rate: stored.rate, source: "auto" };
}

/**
 * 손으로 넣은 원·달러 환율이 정상 범위 밖이면 경고 (docs/infra.md 25.296).
 *
 * 13.9 처럼 자릿수를 빠뜨리면 원화 손익이 100배 틀린다. **막지는 않는다** — 매매 기록은 사용자 것이다(CLAUDE.md).
 * 저장하고 그 자리에서 말한다. 범위 500~3,000 원은 1997년 외환위기 고점(약 1,960)과 그 뒤 저점(약 900)을
 * 넉넉히 감싼 값이다 — 실측 범위가 아니라 오타를 잡는 그물이다 [확인필요: 다른 통화를 받게 되면 통화별로]
 */
export const FX_SANE_KRW = { min: 500, max: 3000 } as const;

export function fxRangeWarning(currency: string, source: string, rate: number): string | null {
  if (currency !== "USD" || source !== "manual") return null;
  if (rate >= FX_SANE_KRW.min && rate <= FX_SANE_KRW.max) return null;
  return `입력한 환율 ${rate.toLocaleString("ko-KR")}원이 보통 범위(${FX_SANE_KRW.min.toLocaleString("ko-KR")}~${FX_SANE_KRW.max.toLocaleString("ko-KR")}원) 밖입니다. 저장은 했으니 오타면 지우고 다시 넣어 주세요`;
}

/** 저장 뒤 이만큼 지나도 다시 계산되지 않았으면 "계산 중" 이 아니라 멈춘 것으로 본다 (25.554). 재계산은 1~2분이다 */
export const RECALC_OVERDUE_MINUTES = 10;
/** 재계산 실행이 이보다 오래 `running` 이면 멈춘 것으로 본다 (25.561). portfolio.yml 의 timeout-minutes 15 에 여유 */
export const RECALC_RUNNING_MAX_MINUTES = 30;

/** 원본(매매·배당)을 마지막으로 고친 시각. **지운 것은 보이지 않는다** — 남은 행의 시각이다 (25.555) */
export const LAST_EDIT = `SELECT MAX(u) AS u FROM (SELECT MAX(updated_at) AS u FROM trades
  UNION ALL SELECT MAX(updated_at) FROM dividend_receipts)`;

/** 마지막 포트폴리오 재계산 실행 */
export const LAST_RECALC_RUN = `SELECT status, started_at, finished_at, error_text FROM batch_runs
WHERE job_name = 'portfolio' ORDER BY id DESC LIMIT 1`;

/**
 * 재계산이 멈췄으면 그 말 (docs/infra.md 25.554·25.555). 없으면 null.
 *
 * `stale` 은 지문 둘만 견줘 **실패와 진행 중을 가리지 못했다.** 토큰이 없거나, 워크플로의 환율 단계가 실패해(yfinance 0행)
 * 재계산 단계가 돌지 않거나, 재계산이 죽으면 화면은 "계산 중입니다…" 를 끝없이 띄우고 30초마다 다섯 경로를 다시 읽었다.
 *
 * - **마지막 실행이 실패했고 마지막 계산(`summaryAt`) 뒤에 시작했으면** 그 까닭을 댄다 — 진행 중(`running`)이면 기다린다
 * - 그 밖에는 **고친 시각이 마지막 계산보다 뒤일 때만** 시간으로 판정한다. 지우기는 `updated_at` 을 남기지 않아
 *   남은 행의 며칠 전 시각이 잡혀, 지우자마자 "4860분 지났다" 가 떴다 (25.555, 교차검증)
 */
export function recalcStuckNote(
  stale: boolean,
  lastEdit: string | null,
  lastRun: { status: string; started_at: string | null; error_text: string | null } | null,
  now: Date,
  summaryAt: string | null = null,
): string | null {
  if (!stale) return null;
  const 기준 = [lastEdit, summaryAt].filter((x): x is string => Boolean(x)).sort().at(-1) ?? null;
  if (lastRun && lastRun.status === "failed" && 기준 && (lastRun.started_at ?? "") >= 기준) {
    return `재계산이 실패했습니다: ${lastRun.error_text ?? "까닭 기록 없음"}. 다음 일일 배치 때 다시 계산합니다`;
  }
  // **진행 중이라도 한도가 있다** (25.561, 교차검증). 닫는 쓰기가 실패하거나 워크플로가 강제 종료되면 `running` 이 남아
  // (정리는 6시간 뒤 다음 실행 때) 멈춤을 영영 알리지 않았다. 재계산은 1~2분, 워크플로 한도는 15분이다
  if (lastRun && lastRun.status === "running" && lastRun.started_at && 기준 && lastRun.started_at >= 기준) {
    const 돈분 = Math.floor((now.getTime() - Date.parse(lastRun.started_at)) / 60_000);
    return 돈분 >= RECALC_RUNNING_MAX_MINUTES
      ? `재계산이 ${돈분}분째 끝나지 않았습니다 — 중간에 멈췄을 수 있습니다. 다음 일일 배치 때 다시 계산합니다`
      : null;
  }
  // 지운 뒤(고친 시각을 모름)에는 시간으로 판정하지 않는다 — 깨어나지 않은 재계산은 여기서 못 잡는다(알고 두는 것, 25.561)
  if (!lastEdit || (summaryAt && lastEdit <= summaryAt)) return null;
  const 지난분 = Math.floor((now.getTime() - Date.parse(lastEdit)) / 60_000);
  if (!(지난분 >= RECALC_OVERDUE_MINUTES)) return null;
  return `저장 뒤 ${지난분}분이 지났는데 다시 계산되지 않았습니다 — `
    + "재계산이 깨어나지 않았거나 앞 단계(환율)에서 멈췄을 수 있습니다. 다음 일일 배치 때 다시 계산합니다";
}

/**
 * "계산 중" 띠를 보일까 (docs/infra.md 25.329).
 *
 * 지난 계산이 지금 매매와 다르면(stale) 보인다 — **지금 매매가 0건이어도 지난 계산이 있으면** 보인다.
 * 예전에는 `stale && has_trades` 라, 마지막 매수를 지우면 띠가 숨고 [보유]·[실현손익] 탭이 옛 보유를 그대로 그렸다.
 * 매매가 한 번도 없어 계산도 없는 처음 상태(as_of 없음)에는 보이지 않는다.
 */
export function recalcPending(s: { stale: boolean; has_trades: boolean; as_of: string | null } | null | undefined): boolean {
  if (!s || !s.stale) return false;
  return s.has_trades || s.as_of !== null;
}

// ----------------------------------------------------------------------
// 질의
// ----------------------------------------------------------------------

// **거래한 적 있는 종목은 상태와 상관없이 찾는다** (docs/infra.md 25.643, 감사). 예전에는 `status = 'active'` 만이라
// 들고 있던 종목이 폐지·제외(`delisted`)되면 정리매매 매도·뒤늦은 배당을 기록할 수 없어 보유가 마지막 종가로 영원히 남았다
export const STOCK_SEARCH = `SELECT id, ticker, market, country, currency, COALESCE(name_ko, name_en, ticker) AS name, status, asset_type
FROM stocks
WHERE (status = 'active' OR id IN (SELECT stock_id FROM trades))
  AND (ticker LIKE ? OR yahoo_symbol LIKE ? OR name_ko LIKE ? OR name_en LIKE ?)
ORDER BY CASE WHEN ticker = ? OR yahoo_symbol = ? THEN 0 ELSE 1 END, country, ticker
LIMIT 20`;

/**
 * 검색 대상 목록 — **서버 메모리에 잠시 둔다** (docs/infra.md 25.1032, 2026-10-08 사용자 "웹 화면 DB 사용량도 줄여줘").
 * `STOCK_SEARCH` 는 이름 가운데를 찾는 LIKE 라 색인을 못 타 **검색 한 번에 종목 표 전체**(약 1만 행)를 훑었다. 입력이 멈출 때마다
 * 한 번이라 한 종목을 찾는 데 몇 번씩 불렀다. 목록을 `SEARCH_LIST_TTL_MS` 동안 두고 같은 규칙(`filterSearch`)으로 거른다 —
 * 결과는 `STOCK_SEARCH` 와 같다(`__tests__/stockSearchCache1032.test.ts` 가 대 본다). 투자 수치 계산이 아니다(표시할 목록 고르기)
 */
export const STOCK_SEARCH_LIST = `SELECT id, ticker, yahoo_symbol, name_ko, name_en, market, country, currency, status, asset_type
FROM stocks WHERE status = 'active' OR id IN (SELECT stock_id FROM trades)`;
/** 목록을 다시 읽는 간격. 종목 마스터는 하루 한 번 바뀐다 — 매매를 넣은 상장폐지 종목이 늦게 보일 수 있는 시간이다 */
export const SEARCH_LIST_TTL_MS = 30 * 60_000;

export interface SearchRow {
  id: number;
  ticker: string;
  yahoo_symbol: string | null;
  name_ko: string | null;
  name_en: string | null;
  market: string;
  country: string;
  currency: string;
  status: string;
  asset_type: string | null;
}

/** SQLite LIKE 처럼 — ASCII 만 대소문자를 가리지 않는다 */
function likeFold(s: string): string {
  return s.replace(/[A-Z]/g, (c) => c.toLowerCase());
}

/** `STOCK_SEARCH` 와 같은 규칙으로 거르고 줄 세운다 (앞부분 일치: 티커·야후 심볼, 가운데 일치: 이름) */
export function filterSearch(rows: SearchRow[], q: string): Array<Record<string, unknown>> {
  const term = q.trim();
  const t = likeFold(term);
  const up = term.toUpperCase();
  const hit = rows.filter((r) =>
    likeFold(r.ticker ?? "").startsWith(t) || likeFold(r.yahoo_symbol ?? "").startsWith(t)
    || likeFold(r.name_ko ?? "").includes(t) || likeFold(r.name_en ?? "").includes(t));
  const exact = (r: SearchRow) => (r.ticker === up || r.yahoo_symbol === up ? 0 : 1);
  const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
  hit.sort((a, b) => exact(a) - exact(b) || cmp(a.country, b.country) || cmp(a.ticker, b.ticker));
  return hit.slice(0, 20).map((r) => ({
    id: r.id, ticker: r.ticker, market: r.market, country: r.country, currency: r.currency,
    name: r.name_ko ?? r.name_en ?? r.ticker, status: r.status, asset_type: r.asset_type,
  }));
}

export function stockSearchArgs(q: string): string[] {
  const term = q.trim();
  const like = `%${term}%`;
  return [`${term}%`, `${term}%`, like, like, term.toUpperCase(), term.toUpperCase()];
}

/** 배당 지급일까지 그 종목에 넣은 매수 원금(종목 통화, 수수료 제외). 배당 크기 확인용 (docs/infra.md 25.404) */
export const BUY_COST_UNTIL = `SELECT COALESCE(SUM(price * quantity), 0) AS cost
FROM trades WHERE stock_id = ? AND side = 'buy' AND trade_date <= ?`;

/**
 * 배당 세전 금액이 **넣은 원금에 비해 말이 되는가** (docs/infra.md 25.404).
 *
 * 매매의 수수료(25.399)·관심 목표가(25.360)와 같은 구멍이 배당에만 남아 있었다. 미국 종목 10주(약 $1,000)에
 * 배당 3,500 을 원화로 생각해 적으면 $3,500 로 저장되어 시간가중 수익률 지수가 그날 1.0 → 4.075 로 뛰었다.
 *
 * - 원금보다 크면 거절한다 — 한 번의 배당이 넣은 돈 전부보다 크다는 것은 사실상 통화·자릿수 오타다
 * - 원금의 20% 를 넘으면 저장하되 경고한다 — 특별배당·오래 든 저가 매수일 수 있다
 * - 매수 기록이 없으면 보지 않는다(비교할 원금이 없다)
 */
export const DIVIDEND_WARN_RATIO = 0.2;
export function dividendSizeCheck(gross: number, buyCost: number, currency: string): { error?: string; warning?: string } {
  if (!(buyCost > 0)) return {};
  const 비율 = gross / buyCost;
  const 말 = `배당 세전 ${gross.toLocaleString()} ${currency} 가 넣은 원금 ${buyCost.toLocaleString()} ${currency} 의 ${(비율 * 100).toFixed(0)}%`;
  if (비율 > 1) return { error: `${말}입니다 — 종목 통화(${currency})로 적었는지 확인해 주세요` };
  if (비율 > DIVIDEND_WARN_RATIO) return { warning: `${말}입니다. 특별배당이 아니면 통화·자릿수를 확인하세요` };
  return {};
}

/**
 * 체결가 크기 검사의 기준 — 체결일 또는 그 앞 10일 안의 마지막 시세 (docs/infra.md 25.785). 인자: 종목, 체결일, 체결일
 */
export const TRADE_PRICE_BASIS = `SELECT date, close, high, low FROM prices
WHERE stock_id = ? AND date <= ? AND date >= date(?, '-10 days') AND close IS NOT NULL
ORDER BY date DESC LIMIT 1`;

/** 종가와 이만큼 넘게 벌어지면 **통화 단위 실수**(원↔달러, 약 1,300배)로 보고 묻는다. 가장 큰 정분할(50:1)보다 크게 둔다 —
 * 100:1 넘는 역분할 전 매매는 "그래도 저장" 으로 넣는다 (25.789) */
export const TRADE_PRICE_UNIT_RATIO = 100;
/** 그날 고가·저가 밖으로 이만큼(시간외 단일가 ±10%)까지는 정상으로 본다 */
const 범위_여유 = 0.1;
/** 그날 시세가 없고 앞 10일 안의 종가만 있으면 이 배수 밖을 경고한다 */
const 최근종가_배수 = 2;

/**
 * 체결가가 **단위를 잘못 넣은 값**인가 (docs/infra.md 25.785, 매매 입력 감사 #2).
 *
 * 관심 목표가(25.360)·수수료(25.399)·배당(25.404)에는 같은 그물이 있는데 매매 체결가에만 없었다. AAPL 10주 체결가 칸에 원화
 * 350,000 을 넣으면 그대로 저장돼 평가손익 −99.93%·TWR 0.0007 이 되고, 다음 날부터 거래일마다 거짓 "손절" 매도 플래그와 장중
 * 손절 알림이 나갔다. **막는 것은 100배 넘게 벌어진 통화 실수만**이다 — 미국 `prices.close` 는 받은 때의 분할 반영 값이라
 * 분할 전 매매는 분할 비율(최대 50:1 실측 사례)만큼 벌어질 수 있어, 그보다 좁게 막으면 정당한 옛 매매를 못 넣는다.
 * 그 안쪽(체결금액을 단가 칸에 넣은 10배 등)은 **저장하되 경고**한다: 그날 시세가 있으면 고가~저가(±10%, 시간외 단일가) 밖,
 * 없으면 앞 10일 안의 마지막 종가의 0.5~2배 밖. 시세를 모르면 보지 않는다.
 */
export function tradePriceCheck(
  price: number,
  basis: { date: string; close: number | null; high: number | null; low: number | null } | undefined,
  tradeDate: string,
  currency: string,
): { error: string | null; warning: string | null } {
  if (!basis || !basis.close || basis.close <= 0) return { error: null, warning: null };
  const unit = currency === "USD" ? "달러" : currency === "KRW" ? "원" : currency;
  const ratio = price / basis.close;
  if (ratio > TRADE_PRICE_UNIT_RATIO || ratio < 1 / TRADE_PRICE_UNIT_RATIO) {
    return {
      error: `체결가 ${price.toLocaleString()} 이 ${basis.date} 종가 ${basis.close.toLocaleString()}${unit} 와 100배 넘게 다릅니다. 이 종목은 ${unit} 단위입니다 — 통화를 바꿔 넣지 않았는지 확인해 주세요`,
      warning: null,
    };
  }
  const 뒤 = "체결가 칸에 체결금액(단가 × 수량)이나 다른 단위를 넣지 않았는지, 분할 전 가격인지 확인해 주세요. 저장은 했습니다";
  if (basis.date === tradeDate && basis.high && basis.low && basis.high > 0 && basis.low > 0) {
    if (price > basis.high * (1 + 범위_여유) || price < basis.low * (1 - 범위_여유)) {
      return {
        error: null,
        warning: `체결가 ${price.toLocaleString()} 이 그날 가격 범위(${basis.low.toLocaleString()}~${basis.high.toLocaleString()}${unit}) 밖입니다 — ${뒤}`,
      };
    }
    return { error: null, warning: null };
  }
  if (ratio > 최근종가_배수 || ratio < 1 / 최근종가_배수) {
    return {
      error: null,
      warning: `체결가 ${price.toLocaleString()} 이 ${basis.date} 종가 ${basis.close.toLocaleString()}${unit} 의 절반~두 배 밖입니다 — ${뒤}`,
    };
  }
  return { error: null, warning: null };
}

/**
 * 한 종목의 매매 — **배치와 같은 순서**(날짜 → 같은 날 매수 먼저 → id, `services/portfolio.same_day_order`)로 (docs/infra.md 25.793).
 */
export const TRADES_FOR_HELD = `SELECT id, side, trade_date, quantity FROM trades WHERE stock_id = ?
ORDER BY trade_date, CASE side WHEN 'buy' THEN 0 ELSE 1 END, id`;

type 보유행 = { id: number; side: string; trade_date: string; quantity: number };

/** 순서대로 돌며 **보유를 넘는 매도는 보유분까지만** 뺀다(배치 `match_fifo` 와 같다). 매도 id → 모자란 수량 */
function 돌리기(rows: 보유행[], upTo: string | null): { held: number; short: Map<number, number> } {
  let held = 0;
  const short = new Map<number, number>();
  for (const r of rows) {
    if (upTo !== null && r.trade_date > upTo) break;
    const q = Number(r.quantity);
    if (r.side === "buy") held += q;
    else {
      const 판 = Math.min(q, held);
      held -= 판;
      if (q - 판 > 1e-9) short.set(r.id, q - 판);
    }
  }
  return { held, short };
}

/**
 * 그날까지의 보유 — 배치가 계산하는 보유와 같게 (docs/infra.md 25.793, 매매 입력 감사 #4). 예전 `HELD_QUANTITY` 는 매수·매도를 단순 합산해,
 * 짝 없는 매도(매수를 지운 뒤 남은 매도)가 있으면 화면 [보유] 는 10주인데 새 매도가 "보유 0주보다 많이 팔 수 없습니다" 로 거절됐다.
 */
export function heldOn(rows: 보유행[], upTo: string): number {
  return 돌리기(rows, upTo).held;
}

/**
 * 이 매도를 넣으면 **뒤의 매도 가운데 새로 모자라게 되는 것** (25.201 의 배치 기준판, 25.793). 이미 짝이 없던 매도는 이 매도 탓이 아니다
 */
export function laterShortfall(rows: 보유행[], sell: { trade_date: string; quantity: number }): { d: string; short: number } | null {
  const 전 = 돌리기(rows, null).short;
  const 새 = { id: -1, side: "sell", trade_date: sell.trade_date, quantity: sell.quantity };
  const 넣은 = [...rows, 새].sort((a, b) =>
    a.trade_date < b.trade_date ? -1 : a.trade_date > b.trade_date ? 1
      : (a.side === "buy" ? 0 : 1) - (b.side === "buy" ? 0 : 1) || (a.id === -1 ? 1 : b.id === -1 ? -1 : a.id - b.id));
  const 후 = 돌리기(넣은, null).short;
  for (const r of 넣은) {
    if (r.id === -1 || r.side !== "sell") continue;
    const 늘어남 = (후.get(r.id) ?? 0) - (전.get(r.id) ?? 0);
    if (늘어남 > 1e-9) return { d: r.trade_date, short: 늘어남 };
  }
  return null;
}

/**
 * 이 매수를 지우면 **새로 모자라게 되는 매도** (25.150 의 배치 기준판, docs/infra.md 25.795, 교차검증). 단순 합으로 보면 이미 짝 없던 매도가 있는 종목은
 * 매수를 지울 때마다 "그 매도가 보유보다 많아진다" 는 거짓 경고가 붙었다 — 늘어난 모자람만 말한다
 */
export function deleteShortfallWarnings(rows: 보유행[], deleteId: number): string[] {
  const 전 = 돌리기(rows, null).short;
  const 후 = 돌리기(rows.filter((r) => r.id !== deleteId), null).short;
  const 늘어남: Array<{ d: string; short: number }> = [];
  for (const r of rows) {
    if (r.side !== "sell" || r.id === deleteId) continue;
    const 차 = (후.get(r.id) ?? 0) - (전.get(r.id) ?? 0);
    if (차 > 1e-9) 늘어남.push({ d: r.trade_date, short: 차 });
  }
  if (늘어남.length === 0) return [];
  const 처음 = 늘어남[0];
  return [
    `이 기록을 지우면 ${처음.d} 매도가 보유보다 ${qtyText(처음.short)}주 많아집니다` +
      (늘어남.length > 1 ? ` (그런 매도 ${늘어남.length}건)` : "") +
      ". 손익 계산은 보유분까지만 하고 나머지는 빠집니다 — 그 매도 기록도 함께 정리하세요",
  ];
}

/** 그날까지의 보유 수량. 매도가 보유를 넘지 않는지 볼 때만 쓴다(손익 계산이 아니다). */
export const HELD_QUANTITY = `SELECT COALESCE(SUM(CASE side WHEN 'buy' THEN quantity ELSE -quantity END), 0) AS held
FROM trades WHERE stock_id = ? AND trade_date <= ? AND id != ?`;

/**
 * **이 기록을 지우면 그 뒤 매도가 보유를 넘기는가** (docs/infra.md 25.150).
 *
 * 넣을 때는 막는다 — `POST /api/trades` 가 보유보다 많이 파는 매도를 거절한다.
 * 그런데 **지울 때는 아무것도 안 봤다.** 매수를 지우면 그 뒤 매도가 그대로 남아
 * 같은 상태가 된다. 그리고 `[id]` 경로의 설명문은 **"고치기는 지우고 다시 넣는다"** 고
 * 권하고 있으니, 이 자리는 드문 길이 아니라 **평범한 길**이다.
 *
 * 배치는 이 상태를 견딘다 — `services/portfolio.match_fifo` 가 보유분까지만 짝짓고
 * 경고를 남긴다. 다만 그 경고는 **다음 재계산이 끝나야** 보인다(Actions 가 멈춰 있으면
 * 영영). 지우는 그 자리에서 말해 주는 편이 낫다.
 *
 * 매도 날짜마다 그때까지의 누적 수량을 센다. 음수면 그날 매도가 보유를 넘긴 것이다.
 * 같은 날 안의 순서(id)까지는 보지 않는다 — 배치도 같은 날은 매수를 먼저 처리한다(docs/infra.md 25.397).
 */
export const BALANCE_AT_SELLS = `SELECT t.trade_date AS d,
  (SELECT COALESCE(SUM(CASE x.side WHEN 'buy' THEN x.quantity ELSE -x.quantity END), 0)
     FROM trades x WHERE x.stock_id = t.stock_id AND x.trade_date <= t.trade_date AND x.id != ?) AS balance
FROM trades t WHERE t.stock_id = ? AND t.side = 'sell' ORDER BY t.trade_date`;

/** 위 질의의 결과를 사람이 읽는 한 줄로. 넘긴 곳이 없으면 빈 목록 */
export function oversellWarnings(rows: Array<{ d: string; balance: number }>): string[] {
  const 넘긴것 = rows.filter((r) => Number(r.balance) < -1e-9);
  if (넘긴것.length === 0) return [];
  const 처음 = 넘긴것[0];
  const 모자람 = Math.abs(Number(처음.balance));
  return [
    `이 기록을 지우면 ${처음.d} 매도가 보유보다 ${qtyText(모자람)}주 많아집니다` +
      (넘긴것.length > 1 ? ` (그런 날 ${넘긴것.length}일)` : "") +
      ". 손익 계산은 보유분까지만 하고 나머지는 빠집니다 — 그 매도 기록도 함께 정리하세요",
  ];
}

/**
 * **이 매도를 넣으면 그 뒤 매도가 보유를 넘기는가** (docs/infra.md 25.201).
 *
 * `HELD_QUANTITY` 는 "그날까지" 의 보유만 본다. 과거 날짜로 매도를 끼워 넣으면 그날에는 보유가
 * 충분해도, **이미 있는 뒤 날짜의 매도**가 보유를 넘게 된다(9/1 10주 매수 · 9/10 10주 매도가 있는데
 * 9/5 에 5주 매도를 넣는 경우). 배치는 보유분까지만 짝지으니 손익이 조용히 빠진다 — 25.150 이
 * 지울 때 막은 것과 같은 상태를 **넣을 때** 만든다.
 *
 * `BALANCE_AT_SELLS`(아무것도 빼지 않고, id -1) 결과에서 새 매도 날짜 **이후** 매도 날들의 잔고에
 * 새 수량을 빼 본다. 처음 음수가 되는 날을 돌려준다. 없으면 null.
 */
export function laterOversell(
  rows: Array<{ d: string; balance: number }>,
  tradeDate: string,
  quantity: number,
): { d: string; short: number } | null {
  for (const r of rows) {
    if (r.d < tradeDate) continue;
    const after = Number(r.balance) - quantity;
    if (after < -1e-9) return { d: r.d, short: Math.abs(after) };
  }
  return null;
}

/**
 * 매수 당시 근거를 얼리는 스냅샷. **체결일 전** 가장 최근 점수와, 같은 날짜의 신호 종류.
 * 기준일 D 의 점수는 D+1 아침 배치가 만들어 D 에 살 때는 볼 수 없다 — `<=` 였을 때는 9/24 매수를 9/26 에 입력하면 9/24 점수가,
 * 9/24 저녁에 입력하면 9/23 점수가 얼려져 입력 시점에 따라 복기가 달랐다 (docs/infra.md 25.678, 감사. docs/portfolio.md 1장 "이전").
 * 신호는 **그 나라·그날의 가장 새 판**만 (docs/infra.md 25.423·25.466) — 새 판이 걸러 낸 종목의 옛 판 신호 종류를
 * 얼리지 않는다. 점수 행을 먼저 하나로 줄인 뒤(pick) 신호를 찾는다 — 판 하위질의가 점수 행마다 돌지 않게(25.428).
 * 인자는 예전 그대로 [기간, 종목, 날짜] — 번호 붙은 자리표(?1)는 D1 에서 확인하지 않아 쓰지 않는다.
 */
export const SNAPSHOT_AT = `WITH pick AS (
  SELECT ? AS want_horizon, sc.stock_id, sc.as_of_date, sc.total_score, sc.factor_scores, sc.sentiment_score, s.country
  FROM scores sc JOIN stocks s ON s.id = sc.stock_id
  WHERE sc.stock_id = ? AND sc.as_of_date < ?
  ORDER BY sc.as_of_date DESC, sc.calc_version DESC LIMIT 1
),
-- 판을 **한 번만** 센다. MATERIALIZED 가 없으면 COALESCE 두 갈래에 각각 펼쳐져 그날 신호 전부를 두 번 읽었다(25.467)
newest_pick AS MATERIALIZED (
  SELECT pick.*, (SELECT MAX(c.calc_version) FROM signals c JOIN stocks s3 ON s3.id = c.stock_id
    WHERE s3.country = pick.country AND c.as_of_date = pick.as_of_date) AS newest FROM pick
)
SELECT p.as_of_date, p.total_score, p.factor_scores, p.sentiment_score,
  COALESCE(
    (SELECT sg.signal_type FROM signals sg WHERE sg.stock_id = p.stock_id AND sg.as_of_date = p.as_of_date
       AND sg.calc_version = p.newest AND sg.horizon = p.want_horizon),
    (SELECT sg.signal_type FROM signals sg WHERE sg.stock_id = p.stock_id AND sg.as_of_date = p.as_of_date
       AND sg.calc_version = p.newest ORDER BY sg.horizon LIMIT 1)
  ) AS signal_type
FROM newest_pick p`;
// 같은 날 계산 판이 둘이면 **새 판**을 얼린다 (docs/infra.md 25.210). 예전에는 아무거나 집었고,
// 매도 플래그(25.209)는 그 점수 행의 판과 지금 판을 대 보므로 옛 판을 집으면 비교가 꺼진다

/**
 * 방금 저장된 같은 매매 (docs/infra.md 25.785, 매매 입력 감사 #3). 인자: 종목, 구분, 날짜, 체결가, 수량, 이 시각 이후(created_at).
 * 기록을 먼저 넣고 재계산 호출을 기다리는 사이 응답이 늦거나 폰 네트워크가 끊기면 화면은 실패로 보이고 입력칸이 남는다 —
 * 다시 누르면 같은 매수가 두 번 들어가 보유·원가가 두 배가 됐다. 같은 체결이 정말 두 번 있을 수 있어 막지 않고 **묻는다**
 */
export const RECENT_SAME_TRADE = `SELECT created_at FROM trades
WHERE stock_id = ? AND side = ? AND trade_date = ? AND price = ? AND quantity = ? AND created_at >= ?
ORDER BY created_at DESC LIMIT 1`;
/** 같은 매매를 "방금" 으로 볼 시간 */
export const DUPLICATE_WINDOW_MS = 10 * 60_000;

export const TRADE_INSERT = `INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,
  fee, tax, horizon, memo, snapshot_as_of, score_at_trade, signal_type_at_trade, sentiment_at_trade,
  factor_scores_at_trade, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`;

/**
 * **같은 매매가 방금 없을 때만 넣는다 — 한 문장으로** (docs/infra.md 25.1112, 웹 매매 감사 재현). 중복 확인(`RECENT_SAME_TRADE`)은
 * 조회만 하고 실제 INSERT 는 DB 조회 너덧 번 뒤라, 망이 끊겨 다시 누른 두 요청이 동시에 오면 둘 다 확인을 지나 두 행이
 * 생겼다(보유·원가 두 배, 10주 보유에서 10주 매도 둘이 다 저장). SQLite 는 쓰기를 한 줄로 세우므로 한 문장 안의
 * `NOT EXISTS` 는 앞 요청의 행을 본다. 인자: TRADE_INSERT 의 19개 + [stock_id, side, trade_date, price, quantity, 이후]
 */
export const TRADE_INSERT_UNLESS_RECENT = `INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,
  fee, tax, horizon, memo, snapshot_as_of, score_at_trade, signal_type_at_trade, sentiment_at_trade,
  factor_scores_at_trade, created_at, updated_at)
SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
WHERE NOT EXISTS (SELECT 1 FROM trades WHERE stock_id = ? AND side = ? AND trade_date = ? AND price = ? AND quantity = ?
  AND created_at >= ?)`;

/**
 * 목록 상한 (docs/infra.md 25.591, 감사). 예전에는 매매·배당 300·체결 묶음 200 에서 **말없이** 잘라, 탭 건수가 전부처럼 보이고
 * [실현손익] 합이 요약과 달랐으며 가장 오래된 매매는 지우거나 확인할 수 없었다(고치기는 지우고 다시 넣기뿐). 개인 한 사람의 기록이라
 * 넉넉히 올리고, 한 건 더 읽어 넘치면 경로가 `truncated` 로 알린다
 */
export const LIST_LIMIT = 2000;

export const TRADES_LIST = `SELECT t.id, t.stock_id, s.ticker, s.country, COALESCE(s.name_ko, s.name_en, s.ticker) AS name,
  t.side, t.trade_date, t.price, t.quantity, t.currency, t.fx_rate, t.fx_rate_source, t.fee, t.tax, t.horizon, t.memo,
  t.snapshot_as_of, t.score_at_trade, t.signal_type_at_trade, t.factor_scores_at_trade
FROM trades t JOIN stocks s ON s.id = t.stock_id
ORDER BY t.trade_date DESC, t.id DESC
LIMIT ${LIST_LIMIT + 1}`;

export const DIVIDENDS_LIST = `SELECT d.id, d.stock_id, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name, d.pay_date,
  d.amount_per_share, d.quantity, d.gross_amount, d.tax, d.net_amount, d.currency, d.fx_rate, d.fx_rate_source, d.memo
FROM dividend_receipts d JOIN stocks s ON s.id = d.stock_id
ORDER BY d.pay_date DESC, d.id DESC
LIMIT ${LIST_LIMIT + 1}`;

/**
 * 보유 목록. **평가손익률(원화) 내림차순** (docs/infra.md 25.958, 2026-10-06 사용자 요청 "내계좌에 종목 정렬은 손익금액이 아니라
 * 손익률 내림차순으로 해줘"). 손익률 = 원화 평가손익 ÷ 원화 원가 — 화면 줄의 % (`pctSuffix(unrealized_pnl_krw, cost_krw)`)와 같은 식이다.
 * 예전(25.897)에는 손익 금액 순이라 많이 산 종목이 위로 왔다. 평가를 못 한 종목(손익 NULL)·원가 0 이하는 맨 아래, 손익률이 같으면 손익 금액 순
 */
export const POSITIONS = `SELECT p.*, s.ticker, s.country, s.market, s.sector, COALESCE(s.name_ko, s.name_en, s.ticker) AS name
FROM positions p JOIN stocks s ON s.id = p.stock_id
ORDER BY CASE WHEN p.cost_krw > 0 THEN p.unrealized_pnl_krw * 1.0 / p.cost_krw END DESC NULLS LAST,
  p.unrealized_pnl_krw DESC NULLS LAST, p.market_value_krw DESC NULLS LAST`;

export const LOTS = `SELECT l.*, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name,
  (SELECT trade_date FROM trades WHERE id = l.sell_trade_id) AS sell_date
FROM trade_lots l JOIN stocks s ON s.id = l.stock_id
ORDER BY sell_date DESC, l.id DESC
LIMIT ${LIST_LIMIT + 1}`;

/** 오늘(가장 최근 판정일) 걸려 있는 매도 플래그 */
export const ACTIVE_FLAGS = `SELECT f.id, f.stock_id, f.level, f.reason_code, f.rationale_text, f.rationale_data, f.first_seen_date,
  f.dismissed_at, f.as_of_date
FROM sell_flags f
WHERE f.is_active = 1 AND f.as_of_date = (SELECT MAX(as_of_date) FROM sell_flags)
ORDER BY CASE f.level WHEN 'red' THEN 0 WHEN 'yellow' THEN 1 ELSE 2 END, f.stock_id`;

export const SUMMARY = `SELECT * FROM portfolio_summary WHERE id = 1`;

/** batch/jobs/portfolio.trades_version 과 같은 지문. 다르면 아직 다시 계산되지 않은 것이다. */
export const TRADES_VERSION = `SELECT
  (SELECT 't' || COUNT(*) || ':' || COALESCE(MAX(updated_at), '') FROM trades) || '|' ||
  (SELECT 'd' || COUNT(*) || ':' || COALESCE(MAX(updated_at), '') FROM dividend_receipts) AS version`;

// ----------------------------------------------------------------------
// 다시 계산 요청
// ----------------------------------------------------------------------

/**
 * 포트폴리오 재계산이 읽는 설정 키 (`batch/jobs/portfolio.py`). 이 가운데 하나가 바뀌면 설정 저장이 재계산을 깨운다 (25.629).
 * 배치가 새 키를 읽기 시작하면 여기에도 더한다 — `web/__tests__/settings.test.ts` 가 배치 소스와 대조한다.
 */
export const PORTFOLIO_SETTING_KEYS = [
  "fees",
  "taxes",
  "horizon_targets",
  "max_weight_per_sector",
  "total_investable_amount",
  "risk_free_manual",
] as const;

export interface DispatchResult {
  dispatched: boolean;
  reason?: string;
}

/** 재계산 요청(GitHub dispatch)의 시간 제한 */
export const RECALC_TIMEOUT_MS = 5_000;

/**
 * GitHub Actions 의 포트폴리오 재계산을 깨운다(repository_dispatch, event_type=portfolio).
 * 토큰이 없으면 깨우지 않고 이유를 돌려준다. 그래도 다음 일일 배치가 계산한다.
 */
export async function requestRecalc(fetchImpl: typeof fetch = fetch): Promise<DispatchResult> {
  const token = process.env.GH_DISPATCH_TOKEN;
  const repo = process.env.GH_REPO;
  if (!token || !repo) {
    return { dispatched: false, reason: "GH_DISPATCH_TOKEN·GH_REPO 가 설정되지 않아 다음 일일 배치 때 반영됩니다" };
  }
  try {
    const response = await fetchImpl(`https://api.github.com/repos/${repo}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ event_type: "portfolio" }),
      cache: "no-store",
      // **시간 제한** (docs/infra.md 25.785). 기록은 이미 들어갔는데 이 호출이 늘어지면 화면이 실패로 보여 다시 누르게 된다.
      // 텔레그램 호출(`lib/telegram.ts`)과 같은 모양이다. 늘어지면 다음 일일 배치가 반영한다
      signal: AbortSignal.timeout(RECALC_TIMEOUT_MS),
    });
    // 성공은 204. 이벤트 이름이 워크플로와 안 맞아도 204 라서, 이름은 테스트로 고정한다(docs/deploy.md)
    if (response.status === 204) return { dispatched: true };
    return { dispatched: false, reason: `GitHub 응답 ${response.status}. 다음 일일 배치 때 반영됩니다` };
  } catch {
    return { dispatched: false, reason: "GitHub 호출 실패. 다음 일일 배치 때 반영됩니다" };
  }
}

// ----------------------------------------------------------------------
// 표시
// ----------------------------------------------------------------------

/**
 * 숫자로 쓸 수 있는 값인가. 아니면 `-` 로 그린다.
 *
 * `null`·`undefined` 만 걸러서는 모자라다. 나눗셈 하나가 어긋나면 `NaN`·`Infinity` 가
 * 흘러오고, 그러면 화면에 **"NaN원"** 이 뜬다. 사람은 그걸 보고 앱이 고장 났다고
 * 생각하지, 그 값이 없다고 읽지 않는다 (2026-09-21 추가).
 */
function 그릴수있나(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * 반올림 결과의 `-0` 을 `0` 으로 되돌린다.
 *
 * 자바스크립트에서 `Math.round(-0.4)` 는 `-0` 이고, `(-0).toLocaleString()` 은
 * **`"-0"`** 이다. 400원 손실이 반올림으로 사라졌는데 화면에는 `-0원` 이 남는다.
 * 반올림으로 없어진 부호를 남겨 둘 이유가 없다.
 */
function 반올림(value: number): number {
  return Math.round(value) || 0;
}

/**
 * 감성처럼 **부호가 있는 점수**를 정수로. 먼저 반올림한 뒤 적는다 — `(-0.3).toFixed(0)` 은 "-0" 이다
 * (docs/infra.md 25.432, 배치 리포트 25.421 과 같은 규칙).
 */
export function signedInt(value: number): string {
  const n = Math.round(value);
  // Math.round(-0.3) 은 -0 이다 — 0 으로 적는다. 양수는 +를 붙인다(리포트 `:+d` 와 같은 모양, 25.437)
  return n === 0 ? "0" : n > 0 ? `+${n}` : String(n);
}

export function won(value: number | null | undefined): string {
  if (!그릴수있나(value)) return "-";
  return `${반올림(value).toLocaleString("ko-KR")}원`;
}

export function signedWon(value: number | null | undefined): string {
  if (!그릴수있나(value)) return "-";
  const 반올림한것 = 반올림(value);
  // 부호는 **보이는 숫자**를 따른다. 0.4원을 "+0원" 으로 적으면 늘었다는 말도 아니고
  // 아니라는 말도 아니다 — 반올림해서 0 이면 그냥 0 이다
  return `${반올림한것 > 0 ? "+" : ""}${반올림한것.toLocaleString("ko-KR")}원`;
}

export function money(value: number | null | undefined, currency: string): string {
  if (!그릴수있나(value)) return "-";
  if (currency === "KRW") return won(value);
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/**
 * 부호 없는 비율 — 수익이 아닌 크기(변동성·이긴 달 비율). `pct` 는 양수에 "+" 를 붙여 "변동성 +18.0%" 가
 * 이익처럼 읽혔다 (docs/infra.md 25.930)
 */
export function sizePct(ratio: number | null | undefined, digits = 1): string {
  if (!그릴수있나(ratio)) return "-";
  return `${(ratio * 100).toFixed(digits)}%`;
}

export function pct(ratio: number | null | undefined, digits = 1): string {
  if (!그릴수있나(ratio)) return "-";
  const 보이는값 = Number((ratio * 100).toFixed(digits));
  return `${보이는값 > 0 ? "+" : ""}${보이는값.toFixed(digits)}%`;
}

/**
 * 비율 값의 색 — **`pct` 가 보여 주는 숫자**를 따른다 (docs/infra.md 25.71·25.741). 0.0004 가 "0.0%" 로 보이는데
 * 빨갛거나, −0.0001 이 "0.0%" 인데 파랗던 곳(복기 행·백테스트 창)이 반올림 전 부호로 칠했다.
 */
export function pctClass(ratio: number | null | undefined, digits = 1): string {
  if (!그릴수있나(ratio)) return "";
  const 보이는값 = Number((ratio * 100).toFixed(digits));
  if (보이는값 === 0) return "";
  return 보이는값 > 0 ? "text-rose-600" : "text-blue-600";
}

/**
 * 두 값의 비(比). 하나라도 모르거나 분모가 0 이면 **null** 이다.
 *
 * **왜 따로 두나** (2026-09-21, docs/infra.md 25.71). `pct()` 는 null 을 받으면 `-` 로 그리게
 * 해 두었는데, 부르는 쪽에서 `(손익 ?? 0) / 원가` 처럼 **미리 0 으로 바꿔** 넘기면 그 장치가
 * 무력해진다. 화면에는 `평가손익 - (0.0%)` 가 뜬다 — 금액은 모른다면서 비율은 0% 라고
 * 말하는 셈이다. 보는 사람은 "본전" 으로 읽는다.
 */
export function ratioOf(
  numerator: number | null | undefined,
  denominator: number | null | undefined,
): number | null {
  if (!그릴수있나(numerator) || !그릴수있나(denominator) || denominator === 0) return null;
  return numerator / denominator;
}

/** ` (+3.2%)` 꼴 꼬리표. **모르면 빈 문자열** — 0% 로 꾸미지 않는다 */
export function pctSuffix(
  numerator: number | null | undefined,
  denominator: number | null | undefined,
): string {
  const 비 = ratioOf(numerator, denominator);
  return 비 === null ? "" : ` (${pct(비)})`;
}

/**
 * 손익 색. **모르면 색을 칠하지 않는다.**
 *
 * 붉은색은 "올랐다" 는 뜻이다. 모르는 것에 칠하면 거짓말이 된다.
 * 부호는 `signedWon` 과 같이 **보이는 숫자**(반올림한 값)를 따른다 — 0.4원 이익을
 * "0원" 으로 적어 놓고 붉게 칠하면 앞뒤가 안 맞는다.
 */
export function pnlClass(value: number | null | undefined): string {
  if (!그릴수있나(value)) return "";
  const 보이는값 = 반올림(value);
  if (보이는값 === 0) return "";
  return 보이는값 > 0 ? "text-rose-600" : "text-blue-600";
}

export function parseJson<T>(raw: unknown, fallback: T): T {
  if (typeof raw !== "string" || !raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

// ----------------------------------------------------------------------
// 배당 원천징수 (docs/portfolio.md 4장, docs/infra.md 25.127)
// ----------------------------------------------------------------------

/**
 * 그 통화의 배당 원천징수율(%). 설정에 없으면 `null`.
 *
 * **설정 화면의 세율 칸 넷 중 셋이 아무 데도 안 쓰이고 있었다** (25.127). 사용자가
 * "배당소득세 15.4" 를 적고 저장해도 바뀌는 숫자가 하나도 없었다. 여기가 그중 둘을 쓴다.
 *
 * `us_capital_gains_pct`(해외 양도소득세)는 **일부러 안 쓴다.** 그것은 거래마다 매기는
 * 세금이 아니라 **한 해 양도차익을 합산해** 기본공제(연 250만원)를 뺀 뒤 매기는 세금이다.
 * 체결묶음마다 22% 를 곱하면 실제와 다른 숫자가 나온다 — 없는 것보다 나쁘다.
 * 연 단위 추정은 배치가 `portfolio_summary.totals_json.us_cgt_estimates` 로 낸다 (docs/infra.md 25.617).
 */
/** 배당 세율 상한(%) — 배치 `batch/core/settings_range.py` `kr_dividend_pct`·`us_dividend_pct` (0, 50) 와 같다 */
export const DIVIDEND_TAX_MAX_PCT = 50;

export function withholdingRate(
  currency: string,
  taxes: { kr_dividend_pct?: number | null; us_dividend_pct?: number | null } | null | undefined,
): number | null {
  if (!taxes) return null;
  const rate = currency === "KRW" ? taxes.kr_dividend_pct : taxes.us_dividend_pct;
  // 배치 범위(0~50%, `settings_range`)를 넘으면 모름으로 본다 — 백업 복원으로 들어온 60% 를 폼이 그대로 채웠다 (25.765, 설정 감사)
  return typeof rate === "number" && Number.isFinite(rate) && rate >= 0 && rate <= DIVIDEND_TAX_MAX_PCT ? rate : null;
}

/**
 * 세전 금액에 세율을 매겨 **채워 넣을** 원천징수액. 못 채우면 `null`.
 *
 * **추정해서 저장하지 않는다.** `dividend_receipts` 는 사용자 입력 원본이고
 * "시스템이 고치지 않는다"(migrations/0017). 그래서 화면의 칸을 **미리 채워 주고**,
 * 사용자가 보고 고칠 수 있게 한다 — 저장되는 값은 언제나 사용자가 본 그 값이다.
 *
 * 원화는 원 단위, 외화는 소수 둘째 자리. 증권사의 반올림과 다를 수 있으니 고치라고 둔다.
 */
export function estimateWithholding(gross: number | null, rate: number | null, currency: string): number | null {
  if (gross === null || !Number.isFinite(gross) || gross <= 0 || rate === null) return null;
  const raw = (gross * rate) / 100;
  return currency === "KRW" ? Math.round(raw) : Math.round(raw * 100) / 100;
}

/**
 * 수량을 **입력한 자릿수 그대로** 적는다 (docs/infra.md 25.591, 감사). 기본 `toLocaleString()` 은 소수 셋째 자리에서 반올림해
 * 소수점 매수 0.123456주가 "0.123주" 로 보였고, 보이는 대로 0.123주를 팔면 0.000456주가 "0주" 짜리 보유로 남았다.
 * 부동소수 찌꺼기(0.30000000000000004)는 소수 8자리에서 자른다 — 증권사 소수점 거래의 최소 단위보다 잘다 [확인필요: 증권사별 최소 단위]
 */
export function qtyText(n: number): string {
  // `+ 0` 으로 -0 을 0 으로 — 매수 0.3 → 매도 0.1·0.2 의 잔량 −5e-17 이 "-0주" 로 찍혔다 (25.594, 교차검증)
  return (Number(n.toFixed(8)) + 0).toLocaleString(undefined, { maximumFractionDigits: 8 });
}

/** 상한까지 자르고 잘렸는지 알린다 (25.591) */
export function capList<T>(rows: T[]): { rows: T[]; truncated: boolean } {
  return { rows: rows.slice(0, LIST_LIMIT), truncated: rows.length > LIST_LIMIT };
}

/**
 * 자동 환율이 **그날 것이 아니면** 어느 날 종가인지 알린다 (docs/infra.md 25.591, 감사). 체결 당일 밤에 넣으면 환율은 다음 재계산에서야
 * 받아 전날 이하(7일 안) 환율이 들어가는데, 화면은 "그날 종가" 라고만 했다. 기록에 환율 날짜 칸은 없다(스키마를 늘리지 않았다) —
 * 저장하는 순간에 말해 두는 것이 할 수 있는 전부다
 */
/**
 * **주말 체결일은 말한다** (docs/infra.md 25.1114, 웹 매매 감사 재현). 날짜 검사는 달력에 있는 날·미래만 보아 토요일·일요일
 * 체결이 경고 없이 저장됐다 — 날짜 오타면 보유 기간·시간가중수익률 날짜가 어긋나고 미국 종목은 자동 환율이 앞 날짜 값이
 * 된다. 국내·미국 모두 주말 정규장은 없다. 막지는 않는다(매매 기록은 사용자 것 — 해외 결제일로 적는 사람도 있다)
 * 휴장일(설·추석 등)은 시장 달력이 웹에 없어 보지 않는다 `[확인필요]`
 */
export function weekendNote(isoDay: string): string | null {
  const d = new Date(`${isoDay}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return null;
  const 요일 = d.getUTCDay();
  if (요일 !== 0 && 요일 !== 6) return null;
  return `${isoDay} 은 ${요일 === 6 ? "토요일" : "일요일"}입니다 — 정규장이 없는 날입니다. 체결일이 맞는지 확인해 주세요(틀렸으면 지우고 다시 넣기)`;
}

export function fxDateNote(source: string, stored: { date: string } | undefined, onDate: string): string | null {
  if (source !== "auto" || !stored || stored.date === onDate) return null;
  // 휴일이면 그날 환율은 앞으로도 없다 — "아직 없다" 로 단정하지 않는다 (25.594, 교차검증)
  return `자동 환율은 ${onDate} 이 아니라 ${stored.date} 종가입니다(그날 환율이 없습니다 — 휴일이거나 아직 받지 않았습니다). 체결 환율과 다르면 지우고 환율을 직접 넣어 다시 저장하세요`;
}


/** 배치가 계산한 계좌 전체 노출 (docs/portfolio.md 8장, 25.1002). 보유 ETF 가 없으면 배치가 null 을 둔다 */
export interface Lookthrough {
  etf_pct: number;
  covered_pct: number;
  unknown_etfs: string[];
  by_stock: Array<{ stock_id: number; name: string; direct_pct: number; via_pct: number; total_pct: number }>;
  by_sector: Record<string, number>;
  as_of: string | null;
}

/** 화면 문장 — 계산하지 않고 배치 값을 글로만 바꾼다. 종목은 ETF 속 몫이 있는 것 위주로 앞 8개 */
export function lookthroughLines(lt: Lookthrough | null | undefined): string[] {
  if (!lt) return [];
  const 종목 = lt.by_stock.slice(0, 8).map((r) =>
    r.via_pct > 0 && r.direct_pct > 0
      ? `${r.name} ${r.total_pct.toFixed(1)}%(직접 ${r.direct_pct.toFixed(1)} + ETF ${r.via_pct.toFixed(1)})`
      : r.via_pct > 0
        ? `${r.name} ${r.total_pct.toFixed(1)}%(ETF)`
        : `${r.name} ${r.total_pct.toFixed(1)}%`,
  );
  const 업종 = Object.entries(lt.by_sector).slice(0, 6).map(([k, v]) => `${k} ${v.toFixed(1)}%`);
  const 모름 = lt.unknown_etfs.length ? ` · 구성을 모르는 ETF: ${lt.unknown_etfs.join(", ")}` : "";
  return [
    `ETF 를 펼친 실제 노출 — ETF ${lt.etf_pct.toFixed(1)}% 중 구성을 아는 몫 ${lt.covered_pct.toFixed(0)}%` +
      `${lt.as_of ? ` (구성 기준 ${lt.as_of})` : ""}${모름}`,
    `종목: ${종목.join(" · ")}`,
    `업종: ${업종.join(" · ")}`,
  ];
}
