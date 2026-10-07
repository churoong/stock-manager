/**
 * 장기 적립 ETF 의 계좌별 보기 (docs/etf.md 11.1, docs/infra.md 25.966).
 *
 * 계좌별 가능 여부·우선순위·까닭은 배치(`batch/services/etf_accounts.py`)가 판정 행의 `rationale_data.accounts` 에
 * 적었다. 여기서는 읽어서 계좌마다 순위대로 모으기만 한다 — 순위를 다시 내지 않는다(CLAUDE.md "웹앱은 계산하지 않는다").
 */

import { BUCKET_ORDER, type EtfRow } from "@/lib/etf";

export type Account = "pension" | "irp" | "taxable";

export const ACCOUNTS: Array<{ key: Account; label: string }> = [
  { key: "pension", label: "연금저축" },
  { key: "irp", label: "IRP" },
  { key: "taxable", label: "일반계좌" },
];

export interface AccountCell {
  eligible: boolean;
  priority: number | null;
  reason: string | null;
  note: string | null;
}

/** 배치가 적은 계좌 칸. 없거나(이 기능 전 판정) 깨졌으면 null — 지어내지 않는다 */
export function parseAccounts(raw: string | null): Record<Account, AccountCell> | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw)?.accounts;
    if (!parsed || typeof parsed !== "object") return null;
    for (const { key } of ACCOUNTS) {
      if (!parsed[key] || typeof parsed[key].eligible !== "boolean") return null;
    }
    return parsed as Record<Account, AccountCell>;
  } catch {
    return null;
  }
}

export interface AccountTier {
  priority: number;
  country: string;
  bucket: string;
  reason: string | null;
  note: string | null;
  /** 분류(국내는 기초지수)마다 그 분류 1위 — 같은 지수의 여러 상품을 다 늘어놓지 않는다 */
  rows: EtfRow[];
}

export interface AccountView {
  tiers: AccountTier[];
  /** 이 계좌에서 살 수 없는 통과 ETF 수 (예: 연금저축에서 미국 상장) */
  ineligible: number;
  /** 계좌 칸이 없는 판정 행 수 — 이 기능 전에 낸 판정이다. 다음 월 판정부터 채워진다 */
  missing: number;
}

const COUNTRY_ORDER = ["KR", "US"];

/** 한 계좌의 순위별 묶음. 순위 → 시장(국내 먼저) → 묶음 순. 순위가 없는 칸(위성 등)은 넣지 않는다 */
export function groupByAccount(rows: EtfRow[], account: Account): AccountView {
  let ineligible = 0;
  let missing = 0;
  const tiers = new Map<string, AccountTier & { byCategory: Map<string, EtfRow> }>();
  for (const row of rows) {
    const cells = parseAccounts(row.rationale_data);
    if (!cells) {
      missing += 1;
      continue;
    }
    const cell = cells[account];
    if (!cell.eligible) {
      ineligible += 1;
      continue;
    }
    if (cell.priority === null || !row.bucket) continue;
    const key = `${cell.priority}|${row.country}|${row.bucket}`;
    const tier = tiers.get(key) ?? {
      priority: cell.priority, country: row.country, bucket: row.bucket,
      reason: cell.reason, note: cell.note, rows: [], byCategory: new Map<string, EtfRow>(),
    };
    const category = row.category ?? "(분류 없음)";
    const best = tier.byCategory.get(category);
    if (!best || (row.rank_in_category ?? Infinity) < (best.rank_in_category ?? Infinity)) {
      tier.byCategory.set(category, row);
    }
    tiers.set(key, tier);
  }
  const bucketIndex = (b: string) => {
    const i = (BUCKET_ORDER as readonly string[]).indexOf(b);
    return i < 0 ? BUCKET_ORDER.length : i;
  };
  const out = [...tiers.values()]
    .map(({ byCategory, ...tier }) => ({
      ...tier,
      rows: [...byCategory.values()].sort(
        (a, b) => (b.total_assets ?? 0) - (a.total_assets ?? 0) || a.symbol.localeCompare(b.symbol),
      ),
    }))
    .sort(
      (a, b) =>
        a.priority - b.priority ||
        COUNTRY_ORDER.indexOf(a.country) - COUNTRY_ORDER.indexOf(b.country) ||
        bucketIndex(a.bucket) - bucketIndex(b.bucket),
    );
  return { tiers: out, ineligible, missing };
}
