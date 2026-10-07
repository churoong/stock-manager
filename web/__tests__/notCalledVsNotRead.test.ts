import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeAll, describe, expect, it } from "vitest";

/**
 * **"안 불렸다" 와 "못 읽었다" 를 가른다**, 그리고 **사용자가 적은 것을 지우지 않는다**
 * (docs/infra.md 25.74).
 *
 * 둘 다 `web/app/api` 의 작은 자리인데, 둘 다 **사람이 화면을 보고 내리는 판단**을 망친다.
 *
 * 1. 알림 화면은 호출 기록이 없으면 `호출 기록 없음` 이라고 적는다. 그것은 "외부 크론이
 *    안 부른다" 는 진단으로 읽히고, `docs/todo-user.md` 2번이 사용자에게 시키는 확인이
 *    바로 그것이다. 그런데 경로가 질의 실패를 `.catch(() => 빈 목록)` 으로 삼켜서,
 *    **못 읽은 것도 같은 글자**로 보였다.
 * 2. 관심 종목 추가는 `{stock_id, target_buy_price}` 만 보낸다 — 화면에 메모 칸이 없다.
 *    그런데 경로가 `ON CONFLICT DO UPDATE SET memo = excluded.memo` 였다.
 *    이 경로로 메모를 **넣을 길이 없으니 지우기만 하는** 셈이었다.
 */

const 뿌리 = join(process.cwd(), "..");
const 읽기 = (경로: string) => readFileSync(join(뿌리, 경로), "utf-8");

describe("호출 기록: 못 읽음 ≠ 없음", () => {
  it("경로가 실패를 삼키지 않고 알려 준다", () => {
    const 글 = 읽기("web/app/api/alerts/route.ts");

    expect(글).toContain("heartbeats_error");
    expect(글, "실패를 조용히 빈 목록으로 바꾼다").not.toMatch(
      /\.catch\(\s*\(\s*\)\s*=>\s*\(\{\s*columns:\s*\[\]/,
    );
  });

  it("화면이 둘을 다른 글자로 적는다", () => {
    const 글 = 읽기("web/components/AlertCenter.tsx");

    expect(글).toContain("호출 기록을 읽지 못했습니다");
    expect(글).toContain("호출 기록 없음");
  });

  it("화면이 실패를 전달받는다", () => {
    const 글 = 읽기("web/components/AlertCenter.tsx");

    expect(글).toContain("setBeatsError(aj.heartbeats_error ?? null)");
  });
});

describe("관심 종목: 보내지 않은 것을 지우지 않는다", () => {
  let db: DatabaseSync;

  beforeAll(() => {
    db = new DatabaseSync(":memory:");
    for (const f of readdirSync(join(뿌리, "migrations")).filter((x) => x.endsWith(".sql")).sort()) {
      db.exec(readFileSync(join(뿌리, "migrations", f), "utf-8"));
    }
    db.exec(
      "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)" +
        " VALUES (1, '005930', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')",
    );
  });

  /** 경로가 쓰는 문장을 소스에서 그대로 꺼내 진짜 스키마에 돌린다 */
  function 추가문장(): string {
    const 글 = 읽기("web/app/api/watchlist/route.ts");
    const m = 글.match(/`(INSERT INTO watchlist[\s\S]*?)`/);
    if (!m) throw new Error("watchlist INSERT 를 못 찾았다");
    return m[1];
  }

  /** `보냄` = 요청에 target_buy_price 키가 있었나 (docs/infra.md 25.251 — 없으면 저장해 둔 값을 지킨다) */
  function 추가(memo: string | null, price: number | null, 보냄 = true): void {
    // 다섯째 인자: 단추만 누른 등록이면 알림을 켠다(25.817 — 목표가·메모를 보내면 켜지 않는다)
    db.prepare(추가문장()).run(1, "2026-09-21T00:00:00Z", memo, price, !보냄 && memo === null ? 1 : 0, 보냄 ? 1 : 0);
  }

  function 지금(): { memo: string | null; target_buy_price: number | null } {
    return db.prepare("SELECT memo, target_buy_price FROM watchlist WHERE stock_id = 1").get() as never;
  }

  it("처음 추가하면 그대로 들어간다", () => {
    db.exec("DELETE FROM watchlist");
    추가("장기 후보", 60000);

    expect(지금()).toMatchObject({ memo: "장기 후보", target_buy_price: 60000 });
  });

  it("**메모 없이 다시 추가해도 메모가 살아 있다**", () => {
    db.exec("DELETE FROM watchlist");
    추가("장기 후보", 60000);

    추가(null, 58000); // 화면의 "관심 추가" 가 보내는 모양

    expect(지금().memo, "보내지 않은 메모가 지워졌다").toBe("장기 후보");
  });

  it("목표 매수가는 보낸 대로 덮는다", () => {
    // 키를 보냈으면(null 포함) 그대로 — 비우면 비운 것이 뜻이다
    db.exec("DELETE FROM watchlist");
    추가("메모", 60000);

    추가(null, null);

    expect(지금().target_buy_price).toBeNull();
  });

  it("목표 매수가를 보내지 않으면 지킨다 (25.251 — 관심 버튼은 stock_id 만 보낸다)", () => {
    db.exec("DELETE FROM watchlist");
    추가("메모", 60000);

    추가(null, null, false);

    expect(지금().target_buy_price).toBe(60000);
  });

  it("메모를 새로 보내면 바뀐다", () => {
    db.exec("DELETE FROM watchlist");
    추가("옛 메모", 60000);

    추가("새 메모", 60000);

    expect(지금().memo).toBe("새 메모");
  });
});
