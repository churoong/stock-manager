import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import PageHeader from "@/components/PageHeader";
import ScreenerForm from "@/components/ScreenerForm";

export const dynamic = "force-dynamic";

export default function ScreenerPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/screener" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <PageHeader title="종목 찾기">
          조건을 바꿔 가며 목록을 봅니다. 추천 리포트(텔레그램)는 이 조건이 아니라 점수·신호에서 나옵니다 — 여기 조건은 이 화면에만 씁니다.
          {/* 예전 문구 "텔레그램 목록도 같은 조건" 은 사실이 아니었다 — 배치는 스크리너 조건을 읽지 않는다 (docs/infra.md 25.772) */}
        </PageHeader>
        <ScreenerForm />
      </main>
      <Footer />
    </div>
  );
}
