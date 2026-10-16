import DeskView from "@/components/DeskView";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import PageHeader from "@/components/PageHeader";

export const dynamic = "force-dynamic";

/**
 * 첫 화면 = 운용 상황실 (docs/desk.md, 2026-10-16 사용자 지시 "이 시스템의 목적에 맞게 웹앱 페이지도 전면 개편").
 * 이 시스템의 목적은 자동 매매(로보어드바이저)이고 가상모의투자·KIS모의투자가 중심이다(CLAUDE.md "시스템의 중심").
 * 예전 첫 화면 오늘의 추천(`/recommend`)은 재료로 메뉴 뒤쪽에 남는다 — 주소는 그대로라 알림·리포트 링크가 산다.
 */
export default function Home() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <PageHeader title="운용" doc="docs/desk.md">
          가상모의투자 계좌들과 KIS모의투자가 오늘 무엇을 했고, 어느 전략이 앞서며, 어디를 확인해야 하는지 봅니다. 숫자는 일일 배치가 낸
          그대로입니다.
        </PageHeader>
        <DeskView />
      </main>
      <Footer />
    </div>
  );
}
