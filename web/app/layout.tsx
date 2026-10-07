import type { Metadata, Viewport } from "next";
import DbBanner from "@/components/DbBanner";
import ServiceWorker from "@/components/ServiceWorker";
import "./globals.css";

export const metadata: Metadata = {
  title: "주식 분석·매매관리",
  description: "개인용 주식 분석 도구",
  robots: { index: false, follow: false },
  // 홈 화면에 설치해 앱처럼 쓴다 (docs/pwa.md). manifest 는 app/manifest.ts 가 만든다
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    title: "주식관리",
    // black-translucent 는 본문을 상태표시줄 **아래까지** 밀어 넣는다. 그러면 화면
    // 아래 메뉴가 브라우저 툴바 뒤로 들어가 보이지 않는다(2026-09-17 사용자 보고).
    // default 는 안전 영역을 브라우저가 알아서 비워 준다
    statusBarStyle: "default",
  },
  icons: {
    icon: [
      { url: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: "/apple-touch-icon.png",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // 폰에서 보는 화면이다. 확대를 막지 않는다.
  maximumScale: 5,
  themeColor: "#0f172a",
};

// viewport-fit=cover 를 쓰지 않는 이유 (2026-09-17)
//
//   cover 는 화면 끝까지(노치 아래까지) 그리게 한다. 보기에는 좋지만 **레이아웃 영역이
//   브라우저 툴바 뒤까지 넓어진다.** 그러면 `position: fixed; bottom: 0` 인 아래 메뉴가
//   사파리 하단 툴바 뒤로 들어가 보이지 않는다. 실제로 그렇게 보고됐다.
//   빼면 브라우저가 안전 영역을 알아서 비워 주고, 메뉴는 툴바 위에 뜬다.
//   배경이 노치 아래까지 이어지지 않는 대신 메뉴가 보인다. 보이는 쪽을 골랐다.

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko">
      <body className="min-h-screen">
        {/* DB 가 막히면 화면마다 빈칸만 남는다. 이유를 맨 위에서 한 번 말한다 (docs/infra.md 24절) */}
        <DbBanner />
        {children}
        <ServiceWorker />
      </body>
    </html>
  );
}
