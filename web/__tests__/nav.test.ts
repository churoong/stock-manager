/**
 * 메뉴 배치 테스트 (docs/pwa.md 3.1·3.2).
 *
 * 2026-09-17 "메뉴가 너무 위에있어" → 폰에서는 메뉴를 **아래**에 둔다.
 * 2026-09-18 "메뉴 위치나 버튼을 찾기 어렵다" → 아래 막대를 **아이콘 + 글자** 로 하고 나머지 화면은
 * 더보기 한 곳에 모은다. 같은 날 "장기 적립은 메뉴에 노출" 로 여섯 칸(추천·적립·찾기·내 계좌·알림·더보기)이 됐다.
 *
 * 화면을 띄우지 않고 구성(lib/menu.ts)과 파일로 고정한다. 여기서 확인할 것은
 * "어느 자리에 두기로 했는가" 와 "모든 화면에 갈 길이 있는가" 라는 결정이다.
 */

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { BACK_TO, DESKTOP_TABS, MORE_ITEMS, PHONE_TABS, TITLES, activePhoneTab } from "@/lib/menu";

const read = (...parts: string[]) => readFileSync(join(process.cwd(), ...parts), "utf-8");
const nav = read("components", "Nav.tsx");
const css = read("app", "globals.css");
const APP = join(process.cwd(), "app");
/** app/ 아래의 화면 경로 (로그인·API·동적 경로 제외) */
const PAGES = readdirSync(APP, { withFileTypes: true })
  .filter((d) => d.isDirectory() && !["api", "login", "stocks"].includes(d.name))
  .filter((d) => existsSync(join(APP, d.name, "page.tsx")))
  .map((d) => `/${d.name}`);

describe("폰 메뉴 자리", () => {
  it("아래에 고정하고, 넓은 화면에서는 숨긴다", () => {
    expect(nav).toContain("fixed inset-x-0 bottom-0");
    expect(nav).toMatch(/fixed inset-x-0 bottom-0[^"]*sm:hidden/);
  });

  it("노치와 홈 인디케이터를 피한다", () => {
    // 설치형 앱은 상태표시줄 아래까지 그린다. 띄우지 않으면 시계에 가린다
    expect(nav).toContain("env(safe-area-inset-top)");
    expect(nav).toContain("env(safe-area-inset-bottom)");
  });

  it("본문이 아래 막대에 가리지 않게 막대 높이만큼 여백을 준다", () => {
    expect(css).toContain("--phone-nav: 3.5rem");
    expect(nav).toContain("h-14"); // 3.5rem — 두 값이 어긋나면 마지막 줄이 가린다
    expect(css).toContain("padding-bottom: calc(var(--phone-nav) + 0.5rem + env(safe-area-inset-bottom))");
    expect(css).toContain("max-width: 639px");
  });

  it("넓은 화면 메뉴는 그대로 위에 있다", () => {
    expect(nav).toMatch(/hidden border-b[^"]*sm:block/);
  });
});

describe("폰 아래 막대 (2026-09-18 사용자 결정)", () => {
  it("추천·적립·찾기·내 계좌·알림·더보기 여섯 칸, 아이콘과 글자가 함께 있다", () => {
    expect(PHONE_TABS.map((t) => t.label)).toEqual(["추천", "적립", "찾기", "내 계좌", "알림", "더보기"]);
    expect(PHONE_TABS.every((t) => t.icon)).toBe(true);
    expect(nav).toContain("<Icon name={tab.icon}");
  });

  it("아래 막대에 없는 화면은 더보기에서 간다 — 모든 화면에 갈 길이 있다", () => {
    const reachable = new Set([...PHONE_TABS, ...MORE_ITEMS].map((i) => i.href));
    for (const page of PAGES) expect(reachable, page).toContain(page);
  });

  it("메뉴가 가리키는 곳은 모두 있는 화면이다", () => {
    for (const item of [...PHONE_TABS, ...MORE_ITEMS, ...DESKTOP_TABS]) {
      expect(existsSync(join(APP, item.href.slice(1), "page.tsx")), item.href).toBe(true);
    }
  });

  it("더보기 안의 화면에 있으면 더보기 칸이 켜진다. 종목 상세는 어느 칸도 켜지 않는다", () => {
    expect(activePhoneTab("/recommend")).toBe("/recommend");
    expect(activePhoneTab("/etf")).toBe("/etf"); // 적립은 아래 막대에 있다
    expect(activePhoneTab("/reports")).toBe("/more");
    expect(activePhoneTab("/status")).toBe("/more");
    expect(activePhoneTab("/stocks")).toBeNull();
  });

  it("로그아웃은 더보기에 있다 (매 화면 제목줄에서 뺐다)", () => {
    expect(read("app", "more", "page.tsx")).toContain('action="/api/auth/logout"');
  });
});

describe("폰 제목줄", () => {
  it("아래 막대에 없는 화면에는 뒤로 단추가 있다 — 설치형 앱에는 브라우저 뒤로가 없다", () => {
    const tabs = new Set(PHONE_TABS.map((t) => t.href));
    for (const page of [...PAGES.filter((p) => !tabs.has(p)), "/stocks"]) {
      expect(BACK_TO[page], page).toBeTruthy();
    }
    for (const tab of tabs) expect(BACK_TO[tab], tab).toBeUndefined();
  });

  it("모든 화면에 제목줄 이름이 있다", () => {
    for (const page of [...PAGES, "/stocks"]) expect(TITLES[page], page).toBeTruthy();
  });

  it("본문 제목은 폰에서 숨긴다 — 제목줄과 두 번 나오면 화면만 밀린다", () => {
    expect(read("components", "PageHeader.tsx")).toMatch(/<h1 className="[^"]*hidden[^"]*sm:block/);
    // 화면마다 제목을 PageHeader 로 쓴다. 맨 <h1> 을 다시 넣으면 폰에서 두 번 나온다
    for (const page of PAGES) {
      const source = read("app", page.slice(1), "page.tsx");
      expect(source, page).not.toContain("<h1");
    }
  });
});

describe("아래 메뉴와 겹치지 않는다", () => {
  it("화면 아래에 붙는 단추 막대는 메뉴 높이만큼 올라가 있다", () => {
    // 설정의 [저장] 이 메뉴에 가리면 누를 수 없다
    const source = read("components", "SettingsForm.tsx");
    expect(source).toContain("bottom-[calc(var(--phone-nav)+env(safe-area-inset-bottom))]");
    expect(source).toContain("sm:bottom-0");
  });
});

describe("종목 찾기 (2단계)", () => {
  const screener = read("components", "ScreenerForm.tsx");

  it("[조회] 는 폰에서 떠 있지 않고 조건 칸 끝에 붙는다 — 떠 있으면 입력칸을 가렸다", () => {
    expect(screener).not.toContain("bottom-[calc(var(--phone-nav)");
    expect(screener).toContain("sm:sticky sm:bottom-0"); // 넓은 화면은 전처럼 아래에 붙는다
  });

  it("세부 조건은 폰에서 접어 두고, 넓은 화면에서만 처음부터 펼친다", () => {
    expect(screener).toContain("useState(false)");
    expect(screener).toContain('window.matchMedia("(min-width: 640px)").matches) setPanelOpen(true)');
  });

  it("결과는 폰에서 카드, 넓은 화면에서 표이고 둘이 같은 열 정의를 쓴다", () => {
    expect(screener).toMatch(/<ul className="[^"]*sm:hidden/);
    expect(screener).toMatch(/className="hidden overflow-x-auto sm:block"/);
    expect(screener.match(/columns\.map\(/g)?.length).toBeGreaterThanOrEqual(3); // 카드·표 머리·표 칸
  });
});

describe("종목 상세 (3단계)", () => {
  const detail = read("components", "StockDetail.tsx");
  const criteria = read("components", "CriteriaTable.tsx");

  it("폰 구역 바로가기가 가리키는 카드가 모두 있다", () => {
    const block = detail.slice(detail.indexOf("const JUMPS"), detail.indexOf("];", detail.indexOf("const JUMPS")));
    const jumps = [...block.matchAll(/\["(\w+)", "[^"]+"\],/g)].map((m) => m[1]);
    expect(jumps).toEqual(["price", "signals", "metrics", "financials", "news", "events"]);
    for (const id of jumps) expect(detail, id).toContain(`id="${id}"`);
    expect(detail).toMatch(/aria-label="구역 바로가기"[\s\S]{0,80}sticky[^"]*sm:hidden/);
  });

  it("데이터가 없는 구역은 한 줄로 줄인다", () => {
    expect(detail).toContain("if (loaded.state === \"ok\" && loaded.data.empty)");
  });

  it("옆으로 늘어나는 표는 폰에서 최근 것만 보인다 (재무 3년·분기 4개)", () => {
    expect(detail).toContain("function recentOnly(");
    expect(detail).toMatch(/FinancialsBlock[\s\S]*?const keep = 3;/);
    expect(detail).toMatch(/QuarterlyBlock[\s\S]*?const keep = 4;/);
  });

  it("근거표는 폰에서 기준마다 두 줄 목록, 넓은 화면에서 표 — 같은 행을 그린다", () => {
    expect(criteria).toMatch(/<ul className="[^"]*sm:hidden/);
    expect(criteria).toMatch(/<div className="hidden overflow-x-auto sm:block">/);
    expect(criteria.match(/rows\.map\(/g)?.length).toBe(2);
  });

  it("화면 글에 docs 파일 경로를 적지 않는다 (폰에서 열 수 없다)", () => {
    // 코드 주석은 괜찮다. 근거표의 docPath 도 괜찮다 — "문턱을 어느 문서가 정의하나" 가 근거표 설계의
    // 한 부분이다(CriteriaTable). 그 밖의 화면 글에 docs/ 경로가 없어야 한다
    const visible = detail
      .split("\n")
      .filter((line) => !/^\s*(\*|\/\/|\/\*|\{\/\*)/.test(line))
      .filter((line) => !line.includes("docPath="))
      .join("\n");
    expect(visible).not.toMatch(/docs\/[a-z_]+\.md/);
  });
});

describe("포트폴리오·알림 (4단계)", () => {
  const portfolio = read("components", "PortfolioView.tsx");
  const alerts = read("components", "AlertCenter.tsx");

  it("[＋ 매매 입력] 이 맨 위에 있고 기록 탭의 입력 칸으로 간다", () => {
    expect(portfolio).toContain("매매 입력");
    expect(portfolio).toContain('setTab("trades")');
    expect(portfolio).toContain('id={fixedStock ? undefined : "trade-form"}'); // 25.894: 빠른 입력 폼에는 붙이지 않는다
    expect(portfolio).toContain('getElementById("trade-form")');
  });

  it("하위 탭은 폰에서 한 줄 다섯 칸(짧은 이름) — 옆으로 넘치지 않는다", () => {
    expect(portfolio).toMatch(/role="tablist" aria-label="포트폴리오" className="[^"]*grid grid-cols-5/);
    expect(portfolio).not.toMatch(/mb-3 flex gap-2 overflow-x-auto/);
  });

  it("복기 집계는 폰에서 카드, 넓은 화면에서 표", () => {
    expect(portfolio).toMatch(/<ul className="[^"]*sm:hidden"[\s\S]{0,200}stats\.map/);
  });

  it("알림 탭은 폰에서 두 칸, 고르기·단추는 손가락 높이", () => {
    expect(alerts).toMatch(/role="tablist" aria-label="알림" className="[^"]*grid grid-cols-2/);
    expect(alerts).toMatch(/const SELECT = "h-11/);
  });
});

describe("나머지 화면 (5단계)", () => {
  it("여러 칸 표는 폰에서 카드, 넓은 화면에서 표 (백테스트 전략·스트레스, 상태 감시)", () => {
    const backtest = read("components", "BacktestView.tsx");
    expect(backtest.match(/<ul className="[^"]*sm:hidden/g)?.length).toBe(2);
    expect(backtest).toContain('<table className="hidden w-full sm:table">');
    const status = read("components", "StatusView.tsx");
    expect(status).toMatch(/<ul className="[^"]*sm:hidden"[\s\S]{0,120}data\.watches\.map/);
  });

  it("탭·새로고침·실행 단추는 폰에서 손가락 크기", () => {
    for (const [file, needle] of [
      ["RecommendList.tsx", "min-h-11 flex-1"],
      ["EtfList.tsx", "min-h-11 flex-1"],
      ["BacktestView.tsx", 'const INPUT = "h-11'],
    ] as const) {
      expect(read("components", file), file).toContain(needle);
    }
  });

  it("화면 글에 docs 파일 경로를 적지 않는다 (근거표의 docPath 는 근거표 설계라 예외)", () => {
    for (const file of ["RecommendList.tsx", "EtfList.tsx", "SatelliteList.tsx", "AccumulationStocks.tsx", "BacktestView.tsx", "StatusView.tsx", "PortfolioView.tsx"]) {
      const visible = read("components", file)
        .split("\n")
        .filter((line) => !/^\s*(\*|\/\/|\/\*|\{\/\*)/.test(line))
        .filter((line) => !line.includes("docPath="))
        .join("\n");
      expect(visible, file).not.toMatch(/docs\/[a-z_]+\.md/);
    }
  });
});

describe("배당 폼은 종목이 바뀌면 원천징수를 다시 채운다 (docs/infra.md 25.405)", () => {
  it("종목 통화·세율을 지켜보는 효과가 있고, 사용자가 고친 값은 건드리지 않는다", () => {
    const src = read("components", "PortfolioView.tsx");
    const 효과 = src.slice(src.indexOf("**종목을 바꾸면 세율도 바뀐다**"));
    expect(효과).toContain("}, [stock?.currency, 세율]);");
    expect(효과.slice(0, 효과.indexOf("}, [stock?.currency, 세율]);"))).toContain("if (손댔나.current) return;");
  });
});
