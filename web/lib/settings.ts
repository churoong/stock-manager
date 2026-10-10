/**
 * 설정 스키마와 검증.
 *
 * 설정은 웹앱만 쓴다. 배치는 읽기만 한다. 따라서 검증은 여기 한 곳에 둔다.
 *
 * 기본값에 [확인필요] 가 붙은 항목은 사용자가 직접 넣어야 하는 값이다.
 * 세율과 수수료가 그렇다. 임의의 숫자를 넣어 두면 실현손익이 조용히 틀린다.
 */

import { z } from "zod";
import { readAmount } from "@/lib/numberInput";

const percent = z.number().min(0).max(100);

/** 팩터 가중치. 다섯 개의 합이 100이어야 한다. */
/** 목표·손절을 바꿨을 때 알리는 말 (docs/infra.md 25.764) — 장중 감시·매도 플래그는 다음 일일 배치부터 새 값을 쓴다 */
export const TARGETS_DELAY_NOTE =
  "목표·손절 변경은 장중 감시에는 그 시장의 다음 일일 배치(국내 08:27 KST · 미국 개장 1시간 전)부터, 매도 플래그에는 " +
  "다음 일일 배치(국내·미국 어느 쪽이든 — 전 보유를 다시 판정)부터 반영됩니다. 장중 감시만 바로 바꾸려면 Actions 의 '장중 감시 준비'를 돌립니다";

export const factorWeightsSchema = z
  .object({
    value: percent,
    quality: percent,
    growth: percent,
    momentum: percent,
    risk: percent,
  })
  .refine(
    (w) => Math.abs(w.value + w.quality + w.growth + w.momentum + w.risk - 100) < 0.01,
    { message: "다섯 팩터의 가중치 합이 100이어야 합니다" },
  );

/** 기간별 목표수익률과 손절선. 목표는 양수, 손절은 음수다. */
const horizonTargetSchema = z
  .object({
    target_pct: z.number().min(0).max(1000),
    stop_pct: z.number().min(-100).max(0),
  })
  .refine((t) => t.target_pct > 0, { message: "목표수익률은 0보다 커야 합니다" })
  .refine((t) => t.stop_pct < 0, { message: "손절선은 0보다 작아야 합니다" });

export const settingsSchema = z.object({
  /** 총 투자가능금액. 원화 단일 풀로 관리한다. */
  total_investable_amount: z.number().min(0),

  base_currency: z.literal("KRW"),

  factor_weights: factorWeightsSchema,

  /** 센티먼트 축 가중치. 5팩터와 별도로 종합 점수에 더해진다. */
  sentiment_weight: percent,

  horizon_targets: z.object({
    short: horizonTargetSchema,
    mid: horizonTargetSchema,
    long: horizonTargetSchema,
  }),

  max_weight_per_stock: z.number().min(0.1).max(100),
  max_weight_per_sector: z.number().min(0.1).max(100),

  /**
   * 최소 주문 금액(원). 이보다 작은 권장 금액은 2부에서 **빼고 사유를 적는다**
   * (`batch/services/report_picks`: `최소 주문 단위 미만`). 미국 종목에는 그날 USDKRW 로
   * 나눈 달러 값이 쓰인다(docs/signals.md 3.4).
   *
   * 2026-09-21 까지 **화면에 없었다.** 파이썬은 `min_order_amount` 를 설정에서 읽는데
   * 그 키를 쓰는 화면이 없어 기본값 10만 원에서 바꿀 길이 없었다(docs/infra.md 25.96).
   * 같은 날 이 문턱이 2부에서 **종목을 빼는** 힘을 갖게 돼 더 중요해졌다.
   */
  min_order_amount: z.number().min(0).max(100_000_000),

  /**
   * 시장 추세 필터 (docs/signals.md 3.5). 지수가 200일선 아래면 신규 매수 권장 비중에
   * bear_factor 를 곱한다. 점수·신호는 바꾸지 않는다. 기본값은 batch/services/trend.py 와 같아야 한다.
   */
  trend_filter: z.object({
    enabled: z.boolean(),
    bear_factor: z.number().min(0).max(1),
  }),

  /** 수수료율과 세율. 확인 전에는 null 로 두고 화면에 경고를 띄운다. */
  fees: z.object({
    kr_buy_pct: z.number().min(0).max(5).nullable(),
    kr_sell_pct: z.number().min(0).max(5).nullable(),
    us_buy_pct: z.number().min(0).max(5).nullable(),
    us_sell_pct: z.number().min(0).max(5).nullable(),
  }),

  taxes: z.object({
    kr_transaction_pct: z.number().min(0).max(10).nullable(),
    kr_dividend_pct: z.number().min(0).max(50).nullable(),
    us_dividend_pct: z.number().min(0).max(50).nullable(),
    us_capital_gains_pct: z.number().min(0).max(50).nullable(),
    // 계좌별 세후 적립 시뮬레이션에만 쓴다 (docs/etf.md 11.7, 25.1003). 옛 설정에는 없다 → null
    pension_income_pct: z.number().min(0).max(20).nullable().default(null),
    pension_credit_pct: z.number().min(0).max(30).nullable().default(null),
  }),

  /** 무위험수익률. 자동 수집 전까지 직접 넣는다. */
  risk_free_manual: z.object({
    kr_pct: z.number().min(0).max(30).nullable(),
    us_pct: z.number().min(0).max(30).nullable(),
  }),

  alert_thresholds: z.object({
    spike_pct: z.number().min(0.1).max(50),
    volume_multiple: z.number().min(1).max(50),
  }),

  /** 야간 알림. 끄면 조용시간에 쌓아뒀다 해제 시각에 묶어 보낸다. */
  quiet_hours: z.object({
    enabled: z.boolean(),
    start: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, "HH:MM 형식이어야 합니다"),
    end: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, "HH:MM 형식이어야 합니다"),
    deliver_on_release: z.boolean(),
  })
    // **시작 = 끝은 받지 않는다** (docs/infra.md 25.254). `inQuietHours` 는 같으면 "조용시간 없음" 으로 보는데
    // 사용자는 "하루 종일 조용" 을 뜻했을 수 있다. 어느 쪽인지 지어내지 않고 되묻는다 — 끄려면 사용을 끈다
    // **켜져 있을 때만** 본다 (25.573, 감사). 끈 설정까지 거절해, 9/26 전에 시작=해제로 저장한 사용자의 **꺼 둔** 조용시간이
    // 장중 경로(`readSetting`)에서 기본값(00:00–07:00 켜짐)으로 되살아나 미국장 알림이 07:00 까지 묶였다
    .refine((q) => !q.enabled || q.start !== q.end, {
      message: "조용시간의 시작과 해제가 같습니다. 끄려면 '사용' 을 끄세요",
      path: ["end"],
    }),

  /** 뉴스 감성을 수집할 상위 종목 수. 0이면 감성 기능을 끈다. */
  sentiment_target_top_n: z.number().int().min(0).max(500),

  backfill_years: z.object({
    prices: z.number().int().min(1).max(30),
    financials: z.number().int().min(1).max(30),
  }),
});

export type Settings = z.infer<typeof settingsSchema>;

/** 비중 상한이 섹터 상한보다 크면 앞뒤가 맞지 않는다. */
export function checkWeightConsistency(s: Settings): string[] {
  const problems: string[] = [];
  if (s.max_weight_per_stock > s.max_weight_per_sector) {
    problems.push("종목 비중 상한이 섹터 비중 상한보다 클 수 없습니다");
  }
  if (s.sentiment_weight > 50) {
    problems.push(
      // 식은 `팩터합 + (감성 − 50) × 가중치` 다(docs/factors.md 5장, 25.639) — 가중치는 섞는 비율이 아니라 움직이는 폭
      "센티먼트 가중치가 50%를 넘습니다. 뉴스 감성만으로 종합 점수가 ±25점 넘게 움직입니다",
    );
  }
  return problems;
}

/** 값이 비어 있어 계산이 틀어질 수 있는 항목을 모은다. */
export function missingCriticalValues(s: Settings): string[] {
  const missing: string[] = [];
  const feeEntries: Array<[string, number | null]> = [
    ["국내 매수 수수료", s.fees.kr_buy_pct],
    ["국내 매도 수수료", s.fees.kr_sell_pct],
    ["미국 매수 수수료", s.fees.us_buy_pct],
    ["미국 매도 수수료", s.fees.us_sell_pct],
    ["국내 증권거래세", s.taxes.kr_transaction_pct],
    // 배당 원천징수율도 **쓰인다** — 배당 입력 폼의 세금 칸을 이 비율로 채운다
    // (docs/infra.md 25.127). 비어 있으면 못 채우고, 사용자가 비워 두면 0 으로 기록된다
    ["국내 배당소득세", s.taxes.kr_dividend_pct],
    ["미국 배당원천징수", s.taxes.us_dividend_pct],
  ];
  for (const [label, value] of feeEntries) {
    if (value === null) missing.push(label);
  }
  if (s.risk_free_manual.kr_pct === null) missing.push("국내 무위험수익률");
  if (s.risk_free_manual.us_pct === null) missing.push("미국 무위험수익률");
  return missing;
}

/**
 * 기본값.
 *
 * 세율과 수수료는 일부러 null 이다. 증권사와 계좌 종류에 따라 다르고,
 * 틀린 값이 들어가면 실현손익이 조용히 어긋난다. 사용자가 직접 넣어야 한다.
 */
export const DEFAULT_SETTINGS: Settings = {
  total_investable_amount: 0,
  base_currency: "KRW",
  factor_weights: { value: 20, quality: 20, growth: 20, momentum: 20, risk: 20 },
  sentiment_weight: 10,
  horizon_targets: {
    short: { target_pct: 10, stop_pct: -7 },
    mid: { target_pct: 25, stop_pct: -15 },
    long: { target_pct: 50, stop_pct: -25 },
  },
  max_weight_per_stock: 10,
  max_weight_per_sector: 30,
  // 국내 주식 한 주 값이 대개 이 아래다. 너무 작은 금액은 수수료 비중이 커진다
  // [확인필요: 실제로 쓰면서 조정]. 파이썬 기본값(`jobs/signals.DEFAULT_MIN_ORDER`)과 같아야 한다
  min_order_amount: 100_000,
  trend_filter: { enabled: true, bear_factor: 0.5 },
  fees: {
    kr_buy_pct: null,
    kr_sell_pct: null,
    us_buy_pct: null,
    us_sell_pct: null,
  },
  taxes: {
    kr_transaction_pct: null,
    kr_dividend_pct: null,
    us_dividend_pct: null,
    us_capital_gains_pct: null,
    pension_income_pct: null,
    pension_credit_pct: null,
  },
  risk_free_manual: { kr_pct: null, us_pct: null },
  alert_thresholds: { spike_pct: 5, volume_multiple: 3 },
  quiet_hours: {
    enabled: true,
    start: "00:00",
    end: "07:00",
    deliver_on_release: true,
  },
  // **파이썬 기본값(`jobs/monitor_targets.NEWS_TOP_N`)과 같아야 한다**
  // (2026-09-23, docs/infra.md 25.185). 50 이었다. 이 칸은 설정 화면에 **입력칸이 없는데**
  // `PUT /api/settings` 는 `SETTINGS_KEYS` 를 전부 쓴다 — 사용자가 **아무 설정이나 한 번
  // 저장하면** 이 기본값 50 이 DB 에 박히고, 배치가 그 뒤로 뉴스 후보를 200 대신 50 종목만
  // 고른다. 사용자는 건드린 적도 없는 값이다
  sentiment_target_top_n: 200,
  backfill_years: { prices: 10, financials: 5 },
};

export type ValidationResult =
  | { ok: true; value: Settings; warnings: string[] }
  | { ok: false; errors: string[] };

/** 설정 표에 들어 있을 수 있는 키. `DEFAULT_SETTINGS` 가 단일 정의처다 */
export const SETTINGS_KEYS = Object.keys(DEFAULT_SETTINGS) as Array<keyof Settings>;

/**
 * 저장된 key-value 를 설정 하나로 합친다.
 *
 * 모르는 키는 버리고, **깨진 JSON 은 기본값으로 둔다** — 화면이 무너지는 것보다 낫다.
 *
 * **왜 여기 있나** (2026-09-21, docs/infra.md 25.73). 이 합치기가 `app/settings/page.tsx` 와
 * `app/api/settings/route.ts` 두 곳에 **따로 적혀** 있었다. 글자는 달랐지만 하는 일은 같았고,
 * 이미 **돌려주는 것이 갈라져** 있었다 — 한쪽은 검증 오류를 사용자에게 보여 주고 다른 쪽은
 * `invalid` 라는 아무도 안 읽는 칸에 넣었다.
 */
export function mergeStoredSettings(rows: Array<{ key: string; value: string }>): Settings {
  const merged: Record<string, unknown> = { ...DEFAULT_SETTINGS };
  for (const row of rows) {
    if (!SETTINGS_KEYS.includes(row.key as keyof Settings)) continue;
    try {
      const value = JSON.parse(row.value);
      // **화면에 칸이 없는 설정은 규칙을 어기면 기본값으로 둔다** (docs/infra.md 25.355). 저장(PUT)은 모든 키를
      // 검증하므로, 되살리기·이주로 들어온 `sentiment_target_top_n=1000` 하나 때문에 **아무 칸을 고쳐도 저장이
      // 영영 막혔다** — 화면에서 그 값을 고칠 방법이 없다. 칸이 있는 설정은 그대로 두어 오류를 보여 준다(25.65)
      if (HIDDEN_SETTING_KEYS.includes(row.key as keyof Settings)) {
        const field = settingsSchema.shape[row.key as keyof typeof settingsSchema.shape];
        if (!field.safeParse(value).success) continue;
      }
      // **모양이 다르면 기본값** (25.573, 감사). 객체여야 할 칸에 JSON `null`·숫자가 들어오면 그대로 합쳐져
      // 화면(`SettingsForm`)이 TypeError 로 통째로 죽었다 — 화면에서 고칠 길이 없다. 그 사실은 `unreadableSettingNotices` 가 말한다
      merged[row.key] = 저장값_맞춤(row.key as keyof Settings, value).값;
    } catch {
      // 깨진 값은 기본값을 쓴다 — 그 사실은 `unreadableSettingNotices` 가 말한다
    }
  }
  return merged as Settings;
}

/**
 * 설정 키 하나의 저장값을 기본값 모양에 맞춘다 — 화면(`mergeStoredSettings`)·알림(`unreadableSettingNotices`)·
 * 장중(`readSetting`)이 **모두 이것을 쓴다** (docs/infra.md 25.579, 교차검증: 25.576 뒤로 화면은 칸마다, 장중은 통째로
 * 기본값이라 `quiet_hours={"enabled":false,…}`(`deliver_on_release` 없음)를 화면은 꺼짐, 장중은 켜짐으로 썼다).
 *
 * `horizon_targets` 는 **기간 단위로** 버린다 — 목표·손절은 한 짝이라 배치 `settings_range.기간별_목표` 가 잎 하나만
 * 틀려도 그 기간을 통째로 기본값으로 쓴다. 웹이 잎마다 섞으면 mid `{30, "x"}` 가 화면엔 30/−15, 배치엔 25/−15 였다
 */
function 저장값_맞춤(key: keyof Settings, 값: unknown): { 값: unknown; 맞음: boolean } {
  const 기본 = DEFAULT_SETTINGS[key];
  if (key !== "horizon_targets" || 값 === null || typeof 값 !== "object" || Array.isArray(값)) {
    return 칸별로_맞춤(기본, 값);
  }
  const 받은 = 값 as Record<string, unknown>;
  const 나온: Record<string, unknown> = {};
  let 모두 = true;
  for (const [기간, 짝기본] of Object.entries(기본 as Record<string, unknown>)) {
    const 칸 = 칸별로_맞춤(짝기본, 받은[기간]);
    나온[기간] = 칸.맞음 ? 칸.값 : 짝기본;
    모두 &&= 칸.맞음;
  }
  return { 값: 나온, 맞음: 모두 };
}

/**
 * 저장값을 기본값의 모양에 **칸마다** 맞춘다 (25.573·25.575·25.576). 어긋난 칸만 기본값으로 두고 나머지는 저장값을 쓴다.
 *
 * 25.575 는 한 칸만 어긋나도 설정 한 덩어리를 통째로 기본값으로 버렸다 — `horizon_targets.short = null` 이면 멀쩡한
 * mid 30/-10·long 60/-30 까지 기본값으로 보였는데 배치(`settings_range.기간별_목표`)는 기간마다 따로 봐 30·60 으로
 * 계산했다. 화면과 배치가 갈렸고, 그대로 저장하면 사용자 값이 덮였다. `trend_filter={"enabled":false}`(배수가 빠진
 * 옛 모양)는 화면에 "켜짐" 으로 보였다. 이제 배치와 같이 칸마다 본다.
 *
 * 기본값이 null 인 칸(수수료·세율 등, "모름" 이 기본)은 **null 또는 숫자**만 받는다 — 문자열·객체는 null 로 둔다(배치 `잎마다` 와 같다).
 */
function 칸별로_맞춤(기본: unknown, 값: unknown): { 값: unknown; 맞음: boolean } {
  if (기본 === null) {
    // **칸이 아예 없으면 "안 넣음" 이다** (25.1007, 교차검증) — 나중에 더한 칸(예: 연금 세율 25.1003)이 옛 저장값에 없다고
    // "읽지 못한 칸" 경고를 띄웠다. 배치 `잎마다` 도 없는 잎을 경고 없이 None 으로 본다
    if (값 === null || 값 === undefined) return { 값: null, 맞음: true };
    return typeof 값 === "number" && Number.isFinite(값) ? { 값, 맞음: true } : { 값: null, 맞음: false };
  }
  if (typeof 기본 === "object" && !Array.isArray(기본)) {
    if (값 === null || typeof 값 !== "object" || Array.isArray(값)) return { 값: 기본, 맞음: false };
    const 받은 = 값 as Record<string, unknown>;
    const 나온: Record<string, unknown> = {};
    let 모두 = true;
    for (const [k, v] of Object.entries(기본 as Record<string, unknown>)) {
      const 칸 = 칸별로_맞춤(v, 받은[k]);
      나온[k] = 칸.값;
      모두 &&= 칸.맞음;
    }
    return { 값: 나온, 맞음: 모두 };
  }
  return typeof 값 === typeof 기본 ? { 값, 맞음: true } : { 값: 기본, 맞음: false };
}

/**
 * **칸이 있는 설정인데 읽지 못해 기본값을 보여 주는 것** (docs/infra.md 25.573, 감사). 25.355 의 알림은 숨은 키만 봐서,
 * `horizon_targets`·`total_investable_amount` 의 깨진 JSON 이 **기본값을 저장값처럼** 보였다 — 다른 칸 하나만 고쳐 저장해도
 * 그 기본값이 저장됐다. 되살리기·이주가 값을 그대로 복사해 넣을 때 생긴다
 */
export function unreadableSettingNotices(rows: Array<{ key: string; value: string }>): string[] {
  const out: string[] = [];
  for (const row of rows) {
    const key = row.key as keyof Settings;
    if (!SETTINGS_KEYS.includes(key) || HIDDEN_SETTING_KEYS.includes(key)) continue;
    let 읽음 = false;
    try {
      읽음 = 저장값_맞춤(key, JSON.parse(row.value)).맞음;
    } catch {
      읽음 = false;
    }
    if (!읽음) {
      out.push(
        `설정 ${row.key} 의 저장값 가운데 읽지 못한 칸을 기본값으로 보여 줍니다(읽은 칸은 그대로). 그대로 저장하면 그 칸은 기본값이 저장됩니다`,
      );
    }
    // **모양은 맞는데 규칙을 벗어난 기간** (25.582, 교차검증). 배치 `기간별_목표` 는 범위 밖·0 이하 목표·0 이상 손절인 기간을
    // 기본값으로 쓴다. 화면은 사용자 값을 오류와 함께 보여 주므로(고쳐야 저장된다) 배치가 지금 무엇을 쓰는지를 따로 말한다
    if (읽음 && key === "horizon_targets") {
      const 값 = JSON.parse(row.value) as Record<string, unknown>;
      for (const 기간 of ["short", "mid", "long"] as const) {
        if (!horizonTargetSchema.safeParse(값[기간]).success) {
          out.push(`설정 horizon_targets.${기간} 가 규칙을 벗어나 배치는 지금 그 기간의 기본값을 씁니다 — 고쳐 저장하면 그 값을 씁니다`);
        }
      }
    }
  }
  return out;
}

/** 설정 화면에 입력 칸이 없는 키 (docs/infra.md 25.355). 규칙을 어긴 저장값은 읽을 때 기본값으로 둔다 */
export const HIDDEN_SETTING_KEYS: Array<keyof Settings> = ["base_currency", "backfill_years", "sentiment_target_top_n"];

/** 화면에 칸이 없는데 저장값이 규칙을 어겨 기본값으로 둔 키. 사용자에게 알린다 — 조용히 삼키지 않는다 */
export function hiddenSettingNotices(rows: Array<{ key: string; value: string }>): string[] {
  const out: string[] = [];
  for (const row of rows) {
    if (!HIDDEN_SETTING_KEYS.includes(row.key as keyof Settings)) continue;
    let ok = false;
    try {
      ok = settingsSchema.shape[row.key as keyof typeof settingsSchema.shape].safeParse(JSON.parse(row.value)).success;
    } catch {
      ok = false;
    }
    if (!ok) out.push(`화면에 없는 설정 ${row.key} 의 저장값이 규칙을 어겨 기본값으로 읽었습니다. 저장하면 기본값으로 바뀝니다`);
  }
  return out;
}

/**
 * 저장된 설정을 두고 **사용자에게 띄울 줄**.
 *
 * 검증에 실패해도 그 오류를 그대로 보여 준다. 저장된 값이 규칙을 어겼다는 것은 사용자가
 * 알아야 할 일이지 조용히 삼킬 일이 아니다 — 25.65·25.66 과 같은 이유다.
 */
export function settingsNotices(settings: Settings): string[] {
  const result = validateSettings(settings);
  return result.ok ? result.warnings : result.errors;
}

export function validateSettings(input: unknown): ValidationResult {
  const parsed = settingsSchema.safeParse(input);
  if (!parsed.success) {
    const errors = parsed.error.issues.map((issue) => {
      const path = issue.path.join(".");
      return path ? `${path}: ${issue.message}` : issue.message;
    });
    return { ok: false, errors };
  }

  const consistency = checkWeightConsistency(parsed.data);
  if (consistency.length > 0) {
    return { ok: false, errors: consistency };
  }

  const missing = missingCriticalValues(parsed.data);
  const warnings = missing.length
    ? [`아직 비어 있는 값: ${missing.join(", ")}. 해당 계산은 정확하지 않습니다`]
    : [];

  return { ok: true, value: parsed.data, warnings };
}


/**
 * **쓰이지 않는 설정 칸과 그 사유** (docs/infra.md 25.127).
 *
 * 2026-09-22 까지 세율 칸 넷 중 **셋이 아무 데도 안 쓰이고 있었다.** 사용자가 적고
 * 저장해도 바뀌는 숫자가 하나도 없었는데, 화면도 문서도 그 사실을 말하지 않았다.
 * 둘(배당 원천징수)은 배선했고, 남은 하나는 **일부러 안 쓴다** — 사유를 여기 적는다.
 *
 * `__tests__/settings.test.ts` 가 "모든 세율 칸은 쓰이거나, 사유가 있어야 한다" 를 본다.
 * 사유 없는 예외를 두지 않는 것이 이 표의 목적이다.
 */
export const UNUSED_SETTING_REASON: Record<string, string> = {
  base_currency:
    "원화 단일 풀로 고정입니다(docs/design.md A4). 스키마가 'KRW' 하나만 받으므로 읽어서 가를 것이 없습니다" +
    " — 미국 종목 금액은 그날 환율로 원화에서 나눕니다",
  // docs/infra.md 25.257 — 저장만 되고 아무도 안 읽었는데 사유가 없었다. 설정 화면에 칸도 없다
  backfill_years:
    "백필은 사람이 손으로 한 번 돌리는 작업이라 기간을 명령줄 인자로 받습니다" +
    " (backfill_us --lookback 약 5년, financials --years 5, us_financials YEARS 10)." +
    " 저장된 값은 어느 배치도 읽지 않습니다 — 기간을 바꾸려면 워크플로 입력을 쓰세요",
};


/**
 * **DB 에서 읽은 설정 한 칸을 스키마로 다시 본다** (docs/infra.md 25.172).
 *
 * 이 파일 머리말은 "설정은 웹앱만 쓴다 … 따라서 검증은 여기 한 곳에 둔다" 고 적는데,
 * 그 검증은 **쓸 때**만 돈다. 읽을 때는 아무도 안 본다 — `restore_backup`·
 * `move_user_data` 가 써 넣은 값, 옛 백업에서 되살아난 값이 그대로 쓰인다.
 * 파이썬 쪽은 25.170·25.171 에서 막았고 여기가 같은 구멍이었다.
 *
 * **기본값으로 되돌리고 말한다.** 되돌릴 곳은 `DEFAULT_SETTINGS` 하나다 —
 * 부르는 쪽이 제 기본값을 따로 적으면 두 곳이 갈라진다(`/api/cron/intraday` 가
 * `{ spike_pct: 5, volume_multiple: 3 }` 을 손으로 적고 있었다).
 */
/**
 * 뉴스 수집 크론의 켜짐 스위치 `sentiment_target_top_n` — **저장값을 설정 화면·배치와 같은 규칙으로** 읽는다 (docs/infra.md 25.629, 감사).
 * 예전에는 두 크론이 날것을 `Number()` 로 바꿔 −5·"100"·true 는 수집 끔, 0.5 는 켬이었는데 배치는 범위 밖을 기본값 200 으로
 * 되돌렸다 — 같은 저장값에 웹은 끄고 배치는 200종목을 골랐다. 범위 밖·모양이 틀리면 기본값(배치와 같다), 0 만 끔이다.
 */
export function newsTopN(stored: string | undefined | null): number {
  let raw: unknown = null;
  try {
    raw = stored == null ? null : JSON.parse(stored);
  } catch {
    raw = null; // 깨진 JSON 은 없는 것 — 기본값
  }
  return readSetting("sentiment_target_top_n", raw).value;
}

export function readSetting<K extends keyof Settings>(
  key: K,
  raw: unknown,
): { value: Settings[K]; warning: string | null } {
  if (raw === undefined || raw === null) return { value: DEFAULT_SETTINGS[key], warning: null };
  const parsed = settingsSchema.shape[key].safeParse(raw);
  if (parsed.success) return { value: parsed.data as Settings[K], warning: null };
  // **화면과 같은 방식으로 칸마다 맞춰 본다** (25.579). 맞춘 값이 규칙을 지키면 그것을 쓴다 — 화면이 보여 주는 값과 같다
  // 모양을 맞춘 뒤에도 규칙을 벗어난 **칸만** 기본값으로 바꿔 다시 본다 (25.582, 교차검증 — `{spike_pct:3, volume_multiple:0.5}` 가
  // 통째로 5/3 이 되어 사용자의 멀쩡한 3 이 버려졌다. 배치 `잎마다` 와 같은 방식). 칸을 가리키지 않는 규칙(조용시간 시작=해제 등)은
  // 고칠 칸이 없으니 전처럼 통째 기본값이다. 목표·손절은 기간 단위로 바꾼다(한 짝, 배치 `기간별_목표`)
  const 기본 = DEFAULT_SETTINGS[key];
  // 저장값이 아예 객체가 아니면(배열·숫자) 살릴 칸이 없다 — "일부 칸" 이라고 하지 않고 아래 통째 기본값으로 간다
  if (typeof 기본 === "object" && 기본 !== null && typeof raw === "object" && !Array.isArray(raw)) {
    let 후보 = structuredClone(저장값_맞춤(key, raw).값) as Record<string, unknown>;
    for (let 번 = 0; 번 < 8; 번 += 1) {
      const 맞춘 = settingsSchema.shape[key].safeParse(후보);
      if (맞춘.success) {
        return {
          value: 맞춘.data as Settings[K],
          warning: `설정 ${key} 의 일부 칸이 모양·규칙을 벗어나 그 칸만 기본값으로 냈습니다 — 설정 화면에서 고치세요`,
        };
      }
      // 두 칸을 함께 보는 규칙(refine, code "custom" — 조용시간 시작=해제)은 한 칸만 바꾸면 **없는 값을 지어낸다**
      // (`22:00=22:00` → 22:00–07:00, 25.583 교차검증). 그런 오류는 되돌리지 않고 아래 통째 기본값으로 간다
      // 목표·손절의 refine(목표 > 0, 손절 < 0)도 "custom" 이지만 경로가 기간이라 기간 단위로 되돌리면 된다 — 조용시간만 뺀다
      // (25.587, 교차검증: 25.583 이 custom 을 통째로 빼 목표·손절 한 기간이 틀리면 세 기간 모두 기본값이 됐다)
      const 길 = 맞춘.error.issues.find((i) => i.path.length > 0 && !(key === "quiet_hours" && i.code === "custom"))?.path;
      if (!길) break;
      const 자리 = key === "horizon_targets" ? 길.slice(0, 1) : 길;
      후보 = 칸_되돌림(후보, 기본 as Record<string, unknown>, 자리 as Array<string | number>);
    }
  }
  return {
    value: DEFAULT_SETTINGS[key],
    warning:
      `설정 ${key} 가 스키마를 벗어났습니다(${parsed.error.issues[0]?.message ?? "형식 오류"}).` +
      " 기본값으로 냈습니다 — 설정 화면에서 고치세요",
  };
}

/** `길` 자리의 값을 기본값의 같은 자리 값으로 바꾼 사본 (readSetting 의 칸 되돌림, 25.582) */
function 칸_되돌림(값: Record<string, unknown>, 기본: Record<string, unknown>, 길: Array<string | number>): Record<string, unknown> {
  const 사본 = structuredClone(값);
  let 여기: Record<string, unknown> = 사본;
  let 저기: Record<string, unknown> = 기본;
  for (const [i, 칸] of 길.entries()) {
    const k = String(칸);
    if (i === 길.length - 1) {
      여기[k] = structuredClone(저기?.[k]);
      break;
    }
    if (typeof 여기[k] !== "object" || 여기[k] === null) {
      여기[k] = structuredClone(저기?.[k]);
      break;
    }
    여기 = 여기[k] as Record<string, unknown>;
    저기 = (저기?.[k] ?? {}) as Record<string, unknown>;
  }
  return 사본;
}

/**
 * 설정 화면에서 저장해도 되는가 (docs/infra.md 25.351). 읽기에 실패한 화면은 **기본값**을 보여 주는데,
 * 저장은 모든 키를 쓰므로 한 칸만 고쳐도 저장된 수수료·세율·무위험수익률이 기본값(비어 있음)으로 덮인다.
 */
export function canSaveSettings(loadFailed: boolean): boolean {
  return !loadFailed;
}

/**
 * 종목 상한이 섹터 상한보다 크면 섹터 상한까지로 줄인다 — 배치 `settings_range.비중_상한_맞추기`(25.300)와 같은 규칙
 * (docs/infra.md 25.357). 웹은 이 조합을 저장하지 않지만 되살리기·이주로 들어올 수 있다. 화면이 줄이지 않으면
 * "종목 40%" 라고 적는데 배치는 30% 로 쓴다 — 같은 설정을 두 곳이 다르게 말한다.
 */
export function fitStockCap(stock: number, sector: number): { stock: number; warning: string | null } {
  if (stock > sector) {
    return {
      stock: sector,
      warning: `설정 종목 상한 ${stock}% 가 섹터 상한 ${sector}% 보다 커 종목 상한을 ${sector}% 로 씁니다 (배치와 같은 규칙)`,
    };
  }
  return { stock, warning: null };
}


/**
 * 숫자 칸의 입력을 어떻게 받을지 (docs/infra.md 25.573, 감사). `{ commit: false }` 면 부모 값을 바꾸지 않고 입력칸 글만 둔다.
 *
 * 예전에는 빈 칸·"-" 를 **0 으로 바꿔 올려**, React 가 입력칸에 "0" 을 다시 써서 "-10" 을 칠 수 없었다(손절선이 +10 이 됨).
 * 또 비운 채 저장하면 조용히 0 이 되어 약세 배수 0(권장 금액 전부 0)·최소 주문 0 이 "저장했습니다" 로 끝났다.
 * 이제 빈 칸은 nullable 칸만 null 로 올리고, 아니면 **부모 값을 그대로 둔다**(칸을 떠나면 원래 값이 다시 보인다).
 */
export function numberFieldInput(raw: string, nullable: boolean): { commit: boolean; value: number | null } {
  const t = raw.trim();
  if (t === "" || t === "-" || t === "." || t === "-.") {
    return nullable && t === "" ? { commit: true, value: null } : { commit: false, value: null };
  }
  // **매매 폼과 같은 읽기** (`readAmount`, docs/infra.md 25.1113, 웹 감사). `Number()` 는 "1,000" 을 못 읽어 값을 반영하지
  // 않고(총 투자가능금액 "10,000,000" 이 옛 값 그대로 "저장했습니다"), 반대로 "1e3"·"0x10" 은 1000·16 으로 받았다.
  // 매매 폼은 같은 문제로 이미 글자 칸 + `readAmount` 로 바꿨다(25.642·25.645)
  const parsed = readAmount(t);
  return parsed !== null && Number.isFinite(parsed) ? { commit: true, value: parsed } : { commit: false, value: null };
}


/** 설정 화면과 `GET /api/settings` 가 **같이** 띄우는 줄 (25.575, 교차검증 — API 만 읽기 실패 알림이 빠져 있었다) */
export function storedSettingNotices(rows: Array<{ key: string; value: string }>, settings: Settings): string[] {
  return [...unreadableSettingNotices(rows), ...hiddenSettingNotices(rows), ...settingsNotices(settings)];
}
