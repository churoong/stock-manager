import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { decideBackend } from "@/lib/db";

/**
 * 어느 DB 를 쓸지 정하는 표 — 웹 쪽 (`web/lib/db.decideBackend`).
 *
 * 표 자체는 `tests/fixtures/db_backend_decision.json` 에 있다. **배치도 같은 파일을
 * 읽는다**(`tests/test_db_backend_decision.py`). 2026-09-21 까지 두 테스트가 같은 표를
 * **손으로 베껴** 갖고 있었고, 한쪽을 고치면 다른 쪽은 옛 표를 지키며 조용히 통과했다.
 *
 * **어긋나면 무슨 일이 나나.** 배치는 쓰고 웹은 읽는다. 둘이 다른 DB 를 고르면 배치가
 * 넣은 데이터를 웹이 엉뚱한 곳에서 찾는다. 화면은 "데이터가 없다" 고 말하고, 오류도
 * 안 나고, 아무도 이유를 모른다. DB 를 옮기는 중일 때 가장 위험한 고장이다.
 */

interface 판정 {
  returned: boolean | null;
  turso_ok: boolean;
  quota_blocked: boolean;
  backend: "turso" | "d1";
  왜: string;
}

const 표: 판정[] = JSON.parse(
  readFileSync(join(process.cwd(), "..", "tests", "fixtures", "db_backend_decision.json"), "utf-8"),
).표;

describe("판정표 (batch/core/client.decide 와 같은 파일을 읽는다)", () => {
  it("표를 읽어 냈다", () => {
    // 읽기가 조용히 빈 목록을 내면 아래가 0번 돌고 전부 통과한다
    expect(표).toHaveLength(12);
    expect(new Set(표.map((r) => `${r.returned}/${r.turso_ok}/${r.quota_blocked}`)).size).toBe(12);
  });

  it.each(표.map((r) => [`복귀=${r.returned} 살아있음=${r.turso_ok} 한도=${r.quota_blocked}`, r] as const))(
    "%s",
    (_이름, r) => {
      expect(decideBackend(r.turso_ok, r.quota_blocked, r.returned), r.왜).toBe(r.backend);
    },
  );

  it("모르는 경우에만 Turso 상태를 본다", () => {
    // resolveBackend() 는 복귀 표시를 읽은 경우 Turso 를 찔러 보지 않고 ok=false 를
    // 넘긴다. 그때 Turso 상태가 답을 바꾸면 틀린 DB 를 고른다
    for (const returned of [true, false]) {
      const 답들 = new Set(
        [true, false].flatMap((ok) => [true, false].map((blocked) => decideBackend(ok, blocked, returned))),
      );
      expect(답들.size, `복귀=${returned} 인데 Turso 상태에 따라 답이 갈린다`).toBe(1);
    }
  });
});
