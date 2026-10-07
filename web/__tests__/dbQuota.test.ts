import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { explain, quotaReason } from "@/lib/db";

/**
 * 한도 오류를 알아보는가 — 웹 쪽 (`web/lib/db.quotaReason`).
 *
 * 문구 표는 `tests/fixtures/db_quota_messages.json` 에 있고 **배치도 같은 파일을 읽는다**
 * (`tests/test_db_quota_messages.py`). 25.42 에서 판정표에 했던 것과 같은 방식이다.
 *
 * **왜 중요한가.** 이 함수가 내는 답이 `probeTurso()` 의 `blocked` 가 되고, 그 값이
 * **어느 DB 를 읽을지**를 가른다(`db_backend_decision.json`). 한도를 "다른 장애" 로 읽으면
 * 웹은 Turso 로 갔다가 막힌 DB 를 읽는다. 배치 쪽은 한도를 못 알아보면 매일 실패 알림이
 * 쏟아지고 진짜 고장이 그 속에 묻힌다.
 *
 * **분류**를 맞춘다. 돌려주는 한국어 문장은 두 언어가 조금 다르다(괄호·줄바꿈).
 */

interface 행 {
  쪽: "d1_daily" | "turso_monthly" | null;
  문구: string;
  왜: string;
}

const 표: 행[] = JSON.parse(
  readFileSync(join(process.cwd(), "..", "tests", "fixtures", "db_quota_messages.json"), "utf-8"),
).표;

/** 분류 → 돌려주는 문장에 반드시 들어 있어야 할 말. 두 언어가 같이 지킨다 */
const 표시: Record<string, string> = {
  d1_daily: "D1 하루 쓰기 한도",
  turso_monthly: "Turso 월 한도",
};

describe("한도 문구 표 (batch/core/db.quota_reason 과 같은 파일을 읽는다)", () => {
  it("표를 읽어 냈다", () => {
    expect(표.length).toBeGreaterThanOrEqual(10);
    expect(new Set(표.map((r) => r.쪽))).toEqual(new Set(["d1_daily", "turso_monthly", null]));
  });

  it.each(표.map((r) => [`${r.쪽}: ${r.문구.slice(0, 45) || "(빈 문구)"}`, r] as const))(
    "%s",
    (_이름, r) => {
      const 답 = quotaReason(r.문구);
      if (r.쪽 === null) {
        expect(답, `${r.왜} — 한도로 읽으면 안 된다`).toBeNull();
      } else {
        expect(답, `${r.왜} — 한도를 못 알아봤다`).not.toBeNull();
        expect(답).toContain(표시[r.쪽]);
      }
    },
  );

  it("대소문자를 가리지 않는다", () => {
    expect(quotaReason("EXCEEDED D1'S FREE TIER DAILY ROW WRITE LIMIT")).not.toBeNull();
    expect(quotaReason("OPERATION WAS BLOCKED: SQL READ OPERATIONS ARE FORBIDDEN")).not.toBeNull();
  });
});

describe("화면에 띄우는 문장", () => {
  it.each(표.filter((r) => r.쪽 !== null))("$쪽 은 원문도 함께 남긴다", (r) => {
    // 다음에 **다른 이유로** 막혔을 때 구별해야 한다 (docs/infra.md 23절)
    expect(explain(r.문구)).toContain(r.문구);
  });

  it.each(표.filter((r) => r.쪽 === null))("$쪽 도 원문을 잃지 않는다", (r) => {
    expect(explain(r.문구)).toContain(r.문구);
  });
});

describe("D1 하루 읽기 한도 (25.863)", () => {
  it("읽기 한도 초과를 쓰기 한도로 안내하지 않는다 — 2026-10-01 실제 응답", async () => {
    const { quotaReason } = await import("@/lib/db");
    const 사유 = quotaReason(
      `D1 HTTP 400: {"errors":[{"code":7500,"message":"Your account has exceeded D1's free tier daily row read limit. Upgrade to a paid plan or wait until tomorrow (midnight UTC) to continue."}]}`,
    ) ?? "";
    expect(사유).toContain("읽기 한도");
    expect(사유).not.toContain("쓰기");
    expect(quotaReason("exceeded D1's free tier daily row write limit")).toContain("쓰기 한도");
  });
});
