/**
 * KIS모의투자 도전자 — 둘째 모의 계좌가 베낄 가상 계좌 (docs/paper.md 15.10, docs/infra.md 25.1515).
 *
 * 첫째 모의 계좌는 챔피언(H)을 베끼고, 둘째는 여기서 고른 계좌를 베낀다. 배치(`batch/jobs/broker_mirror.challenger_account`)가
 * 설정 `kis_challenger` = {"account": "C", "at": …} 를 읽는다. 키(`KIS_VTS2_*`)가 없으면 골라도 배치는 꺼진 채 지나간다.
 * 바꾸면 다음 배치에서 그 전 도전자가 산 종목을 모의 계좌가 정리 주문으로 판다(가상 돈).
 */

import { z } from "zod";

/** 둘째 모의 계좌의 증권사 칸 — `broker_mirror.CHALLENGER` 와 같은 이름(테스트가 대조) */
export const CHALLENGER_KEYS = { KR: "kis_vts2", US: "kis_vts2_us" } as const;
/** 챔피언 — `broker_mirror.MIRROR_ACCOUNT`. 도전자로 고를 수 없다 */
export const CHAMPION = "H";
export const CHALLENGER_SETTING_KEY = "kis_challenger";
/** 따로 재는 실험 계좌(단타·스윙) — 도전자로 고를 수 없다. 정의처 `batch/services/paper.SEPARATE_IDS`(시험이 대조, 25.1879) */
export const SEPARATE_IDS: readonly string[] = ["S"];

export const CHALLENGER_READ = `SELECT value FROM settings WHERE key = '${CHALLENGER_SETTING_KEY}'`;
export const CHALLENGER_UPSERT = `INSERT INTO settings (key, value, updated_at) VALUES ('${CHALLENGER_SETTING_KEY}', ?, ?)
ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`;
export const ACCOUNTS_SQL = "SELECT id, name FROM paper_accounts ORDER BY id";

/** 고르기 — 계좌 id 또는 null(끄기). 챔피언은 받지 않는다(배치도 꺼짐으로 둔다) */
export const challengerSchema = z.object({
  account: z
    .string()
    .trim()
    .regex(/^[A-Z][A-Z0-9]{0,3}$/, "계좌 id 꼴이 아닙니다")
    .refine((v) => v !== CHAMPION, `${CHAMPION} 는 챔피언이라 도전자로 고를 수 없습니다`)
    .refine((v) => !SEPARATE_IDS.includes(v), "따로 재는 실험 계좌(단타·스윙)는 도전자로 고를 수 없습니다")
    .nullable(),
});

/** 설정 값 → 계좌. 배치와 같은 읽기: {"account": "C"} 의 account, 아니면 null */
export function challengerOf(raw: unknown): string | null {
  if (raw == null) return null;
  try {
    const v = typeof raw === "string" ? JSON.parse(raw) : raw;
    return v && typeof v === "object" && typeof (v as { account?: unknown }).account === "string" && (v as { account: string }).account
      ? (v as { account: string }).account
      : null;
  } catch {
    return null;
  }
}
