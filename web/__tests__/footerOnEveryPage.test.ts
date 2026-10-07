import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";
import { DISCLAIMER } from "@/lib/notice";

/**
 * **"모든 화면 하단" 을 지키는 장치가 없었다** (docs/infra.md 25.177).
 *
 * CLAUDE.md 절대 규칙:
 *
 * > 모든 화면 하단과 텔레그램 리포트 끝에 "투자 판단의 책임은 본인에게 있습니다" 고지
 *
 * 텔레그램 쪽은 **보내는 쪽이 붙인다** — `sendTelegram()` 이 마지막에 한 번 붙이므로
 * 새 경로가 생겨도 저절로 지켜진다(25.115). 웹은 그렇지 않다. `<Footer />` 를
 * **화면마다 손으로** 적는다. 열세 화면이 지금은 다 적고 있지만, 그것을 확인하는
 * 장치가 없다 — 새 화면을 만들면서 한 줄 빠뜨리면 고지 없는 추천 화면이 뜬다.
 *
 * 25.115 가 웹 텔레그램에 대해 쓴 말이 그대로 여기에도 맞는다.
 *
 * > 규칙을 부르는 쪽마다 지키게 하면 언젠가 빠뜨린다 — 실제로 빠뜨렸다.
 *
 * 뿌리 layout 으로 옮기는 길도 있지만, 화면마다 `flex min-h-screen flex-col` +
 * `<main flex-1>` + `<Footer />` 로 **바닥에 붙는 푸터**를 만들고 있어 layout 으로
 * 올리면 그 구조가 깨진다(폰 아래 메뉴와 얽혀 있다, docs/pwa.md). 그래서 자리는 그대로
 * 두고 **빠뜨림을 잡는 그물**을 친다.
 */

const 앱 = join(process.cwd(), "app");

/** 화면이 아니라서 푸터가 없어도 되는 곳과 **왜인지** */
const 화면이_아닌_것: Record<string, string> = {
  "page.tsx":
    "그리는 것이 없다. `redirect(\"/recommend\")` 한 줄이라 사용자가 보는 순간이 없다",
};

function 화면들(디렉터리: string): string[] {
  const 나온것: string[] = [];
  for (const 이름 of readdirSync(디렉터리)) {
    const 길 = join(디렉터리, 이름);
    if (statSync(길).isDirectory()) {
      // `api/` 는 화면이 아니라 경로다. 돌려주는 것이 JSON 이라 푸터가 없다
      if (이름 === "api") continue;
      나온것.push(...화면들(길));
    } else if (이름 === "page.tsx" || 이름 === "error.tsx" || 이름 === "not-found.tsx") {
      나온것.push(길);
    }
  }
  return 나온것;
}

describe("모든 화면 하단에 고지", () => {
  const 목록 = 화면들(앱);

  it("화면을 실제로 찾아 냈다", () => {
    // 훑기가 조용히 비면 아래가 공짜로 통과한다
    expect(목록.length).toBeGreaterThanOrEqual(12);
    expect(목록.map((p) => relative(앱, p))).toContain("recommend/page.tsx");
  });

  it.each(목록.map((p) => relative(앱, p)))("%s 에 푸터가 있다", (이름) => {
    const 사유 = 화면이_아닌_것[이름];
    const 글 = readFileSync(join(앱, 이름), "utf-8");

    if (사유) {
      expect(사유.length, `${이름} 의 사유가 너무 짧다`).toBeGreaterThan(20);
      expect(글, `${이름} 은 "화면이 아니다" 라고 적혀 있는데 화면을 그린다`).not.toContain("<main");
      return;
    }

    expect(글, "이 화면에는 책임 고지가 없다. <Footer /> 를 넣거나 사유를 적어라").toContain("<Footer />");
    expect(글).toContain('from "@/components/Footer"');
  });

  it("사유 목록이 낡지 않았다", () => {
    const 있는것 = new Set(목록.map((p) => relative(앱, p)));
    const 없는것 = Object.keys(화면이_아닌_것).filter((이름) => !있는것.has(이름));

    expect(없는것, "없는 화면의 사유가 남아 있다").toEqual([]);
  });

  it("푸터가 실제로 고지를 그린다", () => {
    // 위 그물은 `<Footer />` 라는 글자만 본다. 푸터가 빈 껍데기가 되면 지킬 것이 없다
    const 글 = readFileSync(join(process.cwd(), "components", "Footer.tsx"), "utf-8");

    expect(글).toContain("{DISCLAIMER}");
    expect(DISCLAIMER).toBe("투자 판단의 책임은 본인에게 있습니다.");
  });
});

/**
 * **React 밖의 화면도 화면이다** (docs/infra.md 25.232).
 *
 * `public/offline.html` 은 서비스워커가 연결이 끊겼을 때 띄우는 화면이다. 위 그물은 `app/` 만 훑어서
 * 이 파일에 고지가 없는 것을 못 봤다. `<Footer />` 를 쓸 수 없으니 고지 글을 그대로 적었는지 본다.
 */
describe("public 의 HTML 화면에도 고지", () => {
  const 공개 = join(process.cwd(), "public");
  const 목록 = readdirSync(공개).filter((이름) => 이름.endsWith(".html"));

  it("HTML 화면을 실제로 찾아 냈다", () => {
    expect(목록).toContain("offline.html");
  });

  it.each(목록)("%s 에 고지가 있다", (이름) => {
    expect(readFileSync(join(공개, 이름), "utf-8")).toContain(DISCLAIMER);
  });
});

/**
 * **매도 플래그가 보이는 화면은 근거표도 펼친다** (docs/infra.md 25.262).
 * 포트폴리오 화면이 플래그 문장만 그리고 근거표(`rationale_data`)를 펼치지 않았다 — 질의는 읽어 오고 있었다.
 */
describe("매도 플래그 근거표", () => {
  const 부품 = join(process.cwd(), "components");
  const 플래그_화면 = readdirSync(부품).filter((f) => {
    const 글 = readFileSync(join(부품, f), "utf-8");
    return /FLAG_MARK|reason_code/.test(글) && /rationale_text/.test(글);
  });

  it("플래그를 그리는 화면을 찾아 냈다", () => {
    expect(플래그_화면).toEqual(expect.arrayContaining(["PortfolioView.tsx", "StockDetail.tsx"]));
  });

  it.each(플래그_화면)("%s 는 FlagCriteria 로 근거를 펼친다", (f) => {
    expect(readFileSync(join(부품, f), "utf-8")).toContain("<FlagCriteria");
  });
});
