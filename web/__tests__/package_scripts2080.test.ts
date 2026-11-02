import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// 25.2080 — Next 16 은 `next lint` 를 없앴다(`npm run lint` 가 "lint" 를 프로젝트 폴더로 읽어 "no such directory" 로 실패).
// 웹에는 eslint 도 설정도 없다(코드 스타일은 prettier·tsc). 죽은 명령이 검사인 척하지 않게 — next 하위 명령은 있는 것만
const pkg = JSON.parse(readFileSync(join(__dirname, "..", "package.json"), "utf8")) as {
  scripts: Record<string, string>;
  dependencies: Record<string, string>;
};

describe("package.json 명령 (25.2080)", () => {
  it("next 하위 명령은 Next 16 에 있는 것만 쓴다 — `next lint` 없음", () => {
    expect(Number(pkg.dependencies.next.split(".")[0])).toBeGreaterThanOrEqual(16);
    for (const [name, cmd] of Object.entries(pkg.scripts)) {
      const m = cmd.match(/^next\s+(\S+)/);
      if (m) expect(["dev", "build", "start"], name).toContain(m[1]);
    }
    expect(pkg.scripts.typecheck).toBe("tsc --noEmit");
  });
});
