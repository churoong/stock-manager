import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeAll, describe, expect, it } from "vitest";

/**
 * **화면과 경로에 직접 박힌 SQL 을 실제 스키마에 대 본다** (docs/infra.md 25.61).
 *
 * `recommendSql.test.ts`·`screenerSql.test.ts` 같은 것들이 `web/lib` 의 질의를 진짜
 * SQLite 에 돌려 보지만, **`web/app` 의 경로 파일에 그대로 적힌 SQL 은 아무도 보지 않았다.**
 * 경로 모듈은 `next/server` 를 들고 있어 테스트에서 그냥 import 할 수 없기 때문이다.
 *
 * 그래서 import 하지 않고 **글자만 꺼내** 본다. 파일에서 SQL 처럼 생긴 문자열을 모아
 * `migrations/` 를 그대로 적용한 메모리 DB 에 `prepare()` 한다. 값을 넣지 않아도
 * **없는 표·없는 열**은 그 자리에서 드러난다.
 *
 * **왜 이것이 필요한가.** 마이그레이션에서 열 이름 하나를 바꾸면 파이썬 쪽은 여러 테스트가
 * 진짜 스키마에 돌려 보므로 즉시 깨진다. 웹 경로는 **화면을 열어야** 깨진다 — 배포한 뒤,
 * 그것도 그 화면을 누른 사람만 본다. 지금은 Actions 도 DB 도 볼 수 없으니
 * (`docs/infra.md` 25.27) 더더욱 이런 정적 대조가 유일한 그물이다.
 */

const 뿌리 = join(process.cwd(), "..");
const MIGRATIONS = join(뿌리, "migrations");

/** 따옴표 세 종류 안의 글자. SQL 로 시작하는 것만 고른다 */
const 문자열 = /`([^`]*)`|"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)'/g;
const SQL시작 = /^(SELECT|INSERT|UPDATE|DELETE|WITH)\b/i;

/**
 * 보간이 든 질의. `${}` 안이 무엇인지는 실행해 봐야 알아서 이 대조로는 못 잰다.
 * **목록을 손으로 적어 둔다** — 새로 생기면 테스트가 알려 주고, 그때 다시 판단한다.
 */
const 보간_허용 = new Set([
  "web/app/api/watchlist/[id]/route.ts", // UPDATE watchlist SET ${sets.join(", ")} — 아래에서 따로 본다
]);

interface 질의 {
  파일: string;
  sql: string;
}

function 훑기(디렉터리: string, 모음: string[] = []): string[] {
  for (const 이름 of readdirSync(디렉터리)) {
    const 길 = join(디렉터리, 이름);
    if (statSync(길).isDirectory()) 훑기(길, 모음);
    else if (/\.tsx?$/.test(길)) 모음.push(길);
  }
  return 모음;
}

/**
 * `"SELECT …" + " WHERE …"` 처럼 이어 붙인 질의를 한 덩어리로 만든다.
 *
 * 이것이 없으면 **앞 조각만** 검사하고 `WHERE` 절은 통째로 빠진다 — 틀린 열 이름이
 * 숨기 가장 좋은 자리가 바로 거기다. (2026-09-21: 처음에 빠뜨렸다가 알아챘다)
 */
function 이어붙이기(글: string): string {
  return 글.replace(/(["'])\s*\+\s*(["'])/g, "");
}

function 모으기(밑: string): 질의[] {
  const 나온것: 질의[] = [];
  for (const 길 of 훑기(join(뿌리, 밑))) {
    const 글 = 이어붙이기(readFileSync(길, "utf-8"));
    for (const m of 글.matchAll(문자열)) {
      const s = (m[1] ?? m[2] ?? m[3] ?? "").trim();
      if (SQL시작.test(s)) 나온것.push({ 파일: 길.slice(뿌리.length + 1), sql: s });
    }
  }
  return 나온것;
}

let db: DatabaseSync;
const 경로질의 = 모으기("web/app");
const 라이브러리질의 = 모으기("web/lib");

beforeAll(() => {
  db = new DatabaseSync(":memory:");
  for (const 파일 of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(MIGRATIONS, 파일), "utf-8"));
  }
});

/** 없는 표·없는 열이면 그 말을, 그냥 문법이 안 맞으면(조각일 수 있다) null 을 돌려준다 */
function 스키마문제(sql: string): string | null {
  try {
    db.prepare(sql); // 컴파일만 한다. 값을 안 넣었으니 실행되지 않는다
    return null;
  } catch (error) {
    const 말 = error instanceof Error ? error.message : String(error);
    return /no such (table|column|function)/i.test(말) ? 말 : null;
  }
}

describe("경로와 화면에 박힌 SQL", () => {
  it("꺼내 냈다", () => {
    // 긁기가 조용히 0개를 내면 아래가 0번 돌고 전부 통과한다
    expect(경로질의.length).toBeGreaterThanOrEqual(30);
    expect(new Set(경로질의.map((q) => q.파일)).size).toBeGreaterThanOrEqual(12);
  });

  it("마이그레이션이 적용됐다", () => {
    const 표 = db
      .prepare("SELECT name FROM sqlite_master WHERE type = 'table'")
      .all()
      .map((r) => String((r as { name: string }).name));

    expect(표).toContain("stocks");
    expect(표).toContain("alerts");
    expect(표).toContain("cron_heartbeats");
  });

  it.each(
    경로질의
      .filter((q) => !q.sql.includes("${"))
      .map((q, i) => [`${q.파일} #${i}`, q] as const),
  )("%s 가 실제 스키마에 맞는다", (_이름, q) => {
    expect(스키마문제(q.sql), `${q.파일}\n${q.sql}`).toBeNull();
  });

  it("이어 붙인 질의를 한 덩어리로 본다", () => {
    expect(이어붙이기('execute("SELECT a FROM t" + " WHERE b = ?")')).toBe('execute("SELECT a FROM t WHERE b = ?")');
    // 조각만 보면 WHERE 절의 열 이름을 영영 검사하지 않는다
    const where가있는것 = 경로질의.filter((q) => /\bWHERE\b/i.test(q.sql));
    expect(where가있는것.length).toBeGreaterThanOrEqual(20);
    expect(경로질의.some((q) => /FROM batch_runs WHERE job_name/i.test(q.sql))).toBe(true);
  });

  it("보간이 든 질의는 손으로 적어 둔 것뿐이다", () => {
    const 보간 = [...new Set(경로질의.filter((q) => q.sql.includes("${")).map((q) => q.파일))];

    expect(보간.sort()).toEqual([...보간_허용].sort());
  });

  it("watchlist 부분 수정은 어느 열을 넣어도 스키마에 맞는다", () => {
    // 위 목록에서 뺀 유일한 질의다. 그것이 고르는 열을 **경로 파일에서 꺼내** 대 본다 —
    // 여기에 손으로 베껴 적으면 경로가 열을 바꿔도 테스트는 옛 목록을 지키며 통과한다
    const 글 = readFileSync(join(뿌리, [...보간_허용][0]), "utf-8");
    const 열 = [...글.matchAll(/sets\.push\("([^"]+)"\)/g)].map((m) => m[1]);

    expect(열.length, "sets.push 를 못 찾았다 — 경로가 바뀌었으면 이 테스트도 고쳐야 한다").toBeGreaterThanOrEqual(2);
    for (const 한칸 of 열) {
      expect(스키마문제(`UPDATE watchlist SET ${한칸} WHERE id = ?`), 한칸).toBeNull();
    }
    expect(스키마문제(`UPDATE watchlist SET ${열.join(", ")} WHERE id = ?`)).toBeNull();
  });
});

describe("web/lib 의 SQL 도 같은 눈으로", () => {
  it("꺼내 냈다", () => {
    expect(라이브러리질의.length).toBeGreaterThanOrEqual(20);
  });

  it.each(
    라이브러리질의
      .filter((q) => !q.sql.includes("${"))
      .map((q, i) => [`${q.파일} #${i}`, q] as const),
  )("%s 가 실제 스키마에 맞는다", (_이름, q) => {
    expect(스키마문제(q.sql), `${q.파일}\n${q.sql}`).toBeNull();
  });
});

/**
 * **계산 버전을 전체에서 고르지 않는다** (2026-09-22, docs/infra.md 25.111).
 *
 * `MAX(calc_version)` 을 표 전체에서 잡으면, 한쪽 시장만 새 버전으로 다시 돌았을 때
 * 다른 쪽은 제 기준일의 옛 버전 행만 있는데 조건은 새 버전을 요구해 **한 줄도 안 나온다.**
 * 화면은 "아직 판정이 없다" 처럼 보이고 아무도 이유를 모른다.
 *
 * 세 곳(`accumulation`·`etf`·`etfSatellite`)이 전부 그랬다. 주석은 "그중 가장 새 계산 버전"
 * 이라고 **시장별**을 약속하고 SQL 만 안 지켰다 — 25.88 의 모양이다.
 */
describe("계산 버전은 좁혀서 고른다", () => {
  const 파일들 = readdirSync(join(뿌리, "web", "lib"))
    .filter((f) => f.endsWith(".ts"))
    .map((f) => ({ 이름: f, 본문: readFileSync(join(뿌리, "web", "lib", f), "utf-8") }));

  it("읽어 냈다", () => {
    expect(파일들.length).toBeGreaterThan(20);
    expect(파일들.some((f) => f.본문.includes("calc_version"))).toBe(true);
  });

  it("MAX(calc_version) 에는 반드시 WHERE 가 붙는다", () => {
    // `(SELECT MAX(...calc_version) FROM 표)` 처럼 조건 없이 닫히는 것을 찾는다
    const 맨것 = /\(\s*SELECT\s+MAX\(\s*\w*\.?calc_version\s*\)\s+FROM\s+\w+\s*\)/gi;
    const 걸린것: string[] = [];
    for (const { 이름, 본문 } of 파일들) {
      for (const m of 본문.matchAll(맨것)) 걸린것.push(`${이름}: ${m[0]}`);
    }
    expect(걸린것, "계산 버전을 표 전체에서 골랐다. 그 나라·그 기준일 안에서 고른다").toEqual([]);
  });
});

/**
 * **"가장 최근 기준일" 은 나라별로 잡는다** (2026-09-22, docs/infra.md 25.112).
 *
 * 나라마다 배치가 따로 돌아 기준일이 다르다. 표 전체에서 `MAX` 로 잡으면 늦은 쪽이
 * 통째로 빠지거나(INNER) 값이 전부 NULL 이 된다(LEFT). 25.61(유니버스)·25.111(계산 버전)에
 * 이어 세 번째라 그물을 건다.
 */
describe("최신 기준일은 나라별로 잡는다", () => {
  /** 조건 없이 잡아도 되는 표와 **사유**. 사유 없는 예외는 두지 않는다 */
  const 예외: Record<string, string> = {
    sell_flags:
      "매도 플래그는 한 번에 모든 나라를 판정한다 (batch/jobs/sell_flags.run 은 market=None)." +
      " 나라별 날짜가 애초에 생기지 않는다",
  };

  const 파일들 = readdirSync(join(뿌리, "web", "lib"))
    .filter((f) => f.endsWith(".ts"))
    .map((f) => ({ 이름: f, 본문: readFileSync(join(뿌리, "web", "lib", f), "utf-8") }));

  it("사유 없는 예외는 없다", () => {
    expect(Object.values(예외).every((v) => v.length > 20)).toBe(true);
  });

  it("조건 없이 닫히는 MAX(as_of_date) 는 예외 목록에 있는 것뿐이다", () => {
    const 맨것 = /\(\s*SELECT\s+MAX\(\s*\w*\.?as_of_date\s*\)(?:\s+AS\s+\w+)?\s+FROM\s+(\w+)\s*\)/gi;
    const 걸린것: string[] = [];
    let 본것 = 0;
    for (const { 이름, 본문 } of 파일들) {
      for (const m of 본문.matchAll(맨것)) {
        본것 += 1;
        if (!(m[1] in 예외)) 걸린것.push(`${이름}: ${m[0]}`);
      }
    }
    expect(본것, "이런 모양을 하나도 못 찾았다 — 표기가 바뀌었나").toBeGreaterThan(0);
    expect(걸린것, "기준일을 표 전체에서 잡았다. 나라별로 잡거나 예외에 사유를 적는다").toEqual([]);
  });
});
