import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * **해도 소용없는 일을 시키지 않는다** — 빈 화면의 안내 전체 (docs/infra.md 25.86).
 *
 * 25.86 은 추천 화면 하나를 고쳤다. 같은 모양이 더 있는지 화면·경로의 안내 문구를
 * 전부 훑어 찾은 것들이다.
 *
 * | 어디 | 무엇이라 했나 | 왜 틀렸나 |
 * |---|---|---|
 * | `/recommend` | "매수 신호 배치를 먼저 돌리세요" | 따라잡기 7단계가 매일 돌린다 |
 * | `/etf` | "Actions → ETF 판정을 돌리세요" | **가르는 데 필요한 값을 이미 읽고 있었다** |
 * | `/screener` | "재무 수집을 먼저 돌리세요" | 따라잡기 2단계가 매일 돌린다 |
 *
 * 셋 다 "기다리는 것이 답" 인 상황에서 사람에게 할 일을 만들어 줬다.
 * 이 파일은 **그 문구가 다시 돌아오지 않게** 못 박는다.
 */

const 뿌리 = join(process.cwd(), "..");
const 읽기 = (경로: string) => readFileSync(join(뿌리, 경로), "utf-8");

describe("ETF: 돌았는지 보고 말한다", () => {
  const 글 = 읽기("web/app/api/etf/route.ts");

  it("실행 이력을 **안내보다 먼저** 읽는다", () => {
    // 예전에는 안내를 만든 **뒤에** 읽었다. 값이 손에 있는데 쓰지 못한 순서였다
    expect(글.indexOf("const lastRun")).toBeLessThan(글.indexOf("const notes"));
  });

  it("돌았으면 돌리라고 하지 않는다", () => {
    const 갈래 = 글.split("pickedRows.length === 0 && !anyExcluded")[1].split("} else if")[0];

    expect(갈래).toContain("last");
    expect(갈래).toContain("재료가 없는 것입니다");
  });

  it("정말 안 돌았을 때는 그대로 시킨다", () => {
    // 옛 문장이 맞는 **유일한** 경우다. 지우면 진짜로 안 돈 날에 아무 말도 못 한다
    expect(글).toContain("아직 ETF 판정을 돌린 적이 없습니다");
  });

  it("읽기를 늘리지 않았다", () => {
    // 같은 질의를 그대로 쓴다. 옮기기만 했다
    expect((글.match(/job_name = 'etf'/g) ?? []).length).toBe(1);
  });
});

describe("스크리너: 시킬 수 없는 일을 시키지 않는다", () => {
  const 글 = 읽기("web/app/api/screener/route.ts");

  it("'재무 수집을 먼저 돌리세요' 가 사라졌다", () => {
    expect(글).not.toContain("재무 수집을 먼저 돌리세요");
  });

  it("무엇이 비었고 왜 비었는지 말한다 — 25.490 뒤로 비율은 점수 팩터에서 온다 (25.577)", () => {
    expect(글).not.toContain("재무가 아직 수집되지 않아");
    expect(글).toContain("점수를 내지 않는 종목(유니버스 밖 등)이거나 그날 점수가 아직 없습니다");
  });

  it("배치 상태는 /status 가 답할 일이라고 넘긴다", () => {
    expect(글).toContain("/status");
  });

  it("이웃 안내와 말투가 같다", () => {
    // 바로 위 성과 지표 안내는 처음부터 "…끝나면 채워집니다" 였다. 한 화면에서 둘이 달랐다.
    // 성과 지표 안내는 25.779 에서 lib(`metricsEmptyNote`)로 옮겼다
    expect(읽기("web/lib/screener.ts")).toContain("시세 백필이 끝나면 채워집니다");
    expect(글).toContain("metricsEmptyNote(rows, filters.metric_window)");
  });
});

describe("다시 늘어나지 않게", () => {
  /** 화면·경로가 사용자에게 **배치를 돌리라고** 시키는 자리 */
  const 시키는말 = /배치를? (먼저 )?돌리세요|수집을 먼저 돌리세요|판정을 돌리세요/g;

  /**
   * 지금 남아 있는 것은 **셋**이고 셋 다 정당하다 — `signals`·`etf` 가 **정말 한 번도
   * 안 돈** 경우에만 뜬다. 그때는 옛 문장이 맞는 유일한 경우다.
   *
   *   web/lib/whyEmpty.ts       signalRuns === 0
   *   web/app/api/etf/route.ts  batch_runs 에 etf 성공이 없음
   *   web/app/api/etf/satellite/route.ts  batch_runs 에 etf_satellite 성공이 없음 (25.581 — 예전에는 기록을 안 보고
   *                             늘 시켰는데 문장이 이 정규식에 안 걸려 세지 않았다)
   *
   * **줄어드는 것은 좋은 일이라 상한만 본다.** 늘리려면 그 안내가 정말 사람이 할 수 있고
   * 해야 하는 일인지 먼저 따져 보라 — 예약으로 도는 배치를 사람에게 시키면 안 된다.
   */
  const 상한 = 3;

  function 훑기(디렉터리: string, 확장: string[]): string[] {
    const 나온것: string[] = [];
    for (const 이름 of readdirSync(join(뿌리, 디렉터리), { withFileTypes: true })) {
      const 길 = join(디렉터리, 이름.name);
      if (이름.isDirectory()) 나온것.push(...훑기(길, 확장));
      else if (확장.some((e) => 이름.name.endsWith(e))) 나온것.push(길);
    }
    return 나온것;
  }

  const 파일들 = [...훑기("web/app", [".ts", ".tsx"]), ...훑기("web/components", [".tsx"]), ...훑기("web/lib", [".ts"])];

  it("훑어 냈다", () => {
    // 0개면 아래가 공짜로 통과한다
    expect(파일들.length).toBeGreaterThan(40);
  });

  it("**시키는 말이 늘지 않았다**", () => {
    const 걸린것: string[] = [];
    for (const 길 of 파일들) {
      const 글 = 읽기(길);
      for (const 줄 of 글.split("\n")) {
        // 주석은 뺀다 — 왜 그렇게 고쳤는지 적어 둔 설명이다
        if (/^\s*(\*|\/\/)/.test(줄)) continue;
        if (줄.match(시키는말)) 걸린것.push(`${길}: ${줄.trim().slice(0, 80)}`);
      }
    }

    expect(걸린것.length, `사람에게 배치를 돌리라고 시키는 자리:\n  ${걸린것.join("\n  ")}`).toBeLessThanOrEqual(
      상한,
    );
  });
});
