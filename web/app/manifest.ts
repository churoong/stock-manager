import type { MetadataRoute } from "next";

/**
 * 홈 화면에 설치해 앱처럼 쓰기 위한 선언 (docs/pwa.md, 2026-09-17 사용자 요청).
 *
 * **네이티브 앱이 아니라 PWA 다.** 앱스토어에 올리려면 애플 개발자 계정(연 99달러,
 * 카드 등록)이 필요한데 CLAUDE.md 비용 규칙이 금지한다. 설치형 웹앱은 0원이고
 * 아이콘·전체화면·오프라인 안내까지 같은 코드로 된다.
 *
 * 검색엔진 색인은 robots 로 막혀 있고, 주소를 알아도 로그인 없이는 아무것도 못 본다.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "주식 분석·매매관리",
    short_name: "주식관리",
    description: "개인용 주식 분석·매매관리. 추천 근거를 화면에서 확인할 수 있다",
    // 설치 뒤 열리는 화면. 매일 보는 것이 오늘의 추천이다
    start_url: "/recommend",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#0f172a",
    theme_color: "#0f172a",
    lang: "ko",
    dir: "ltr",
    categories: ["finance", "productivity"],
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      // 안드로이드는 아이콘 바깥을 잘라 모양을 맞춘다. 여백을 준 그림을 따로 둔다
      { src: "/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    shortcuts: [
      { name: "오늘의 추천", url: "/recommend" },
      { name: "내 포트폴리오", url: "/portfolio" },
      { name: "시스템 상태", url: "/status" },
    ],
  };
}
