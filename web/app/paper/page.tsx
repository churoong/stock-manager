import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import PageHeader from "@/components/PageHeader";
import PaperView from "@/components/PaperView";

export const dynamic = "force-dynamic";

/**
 * 모의 투자 계좌 (docs/paper.md, 2026-10-15 사용자 지시). 입금만 사용자가 하고 매매는 배치의 규칙서가 한다.
 */
export default function PaperPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/paper" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <PageHeader title="모의 투자" doc="docs/paper.md">
          넣은 예수금 안에서 시스템이 아침 리포트의 규칙대로 사고팝니다. 실제 돈·실제 주문은 없습니다. A 는 모든 매도 플래그를,
          B 는 손절·목표·기간만 따릅니다 — 같은 돈으로 황색 플래그를 따르는 것이 나은지 봅니다. 복기용이며 규칙을 켜고 끄는
          근거로는 쓰지 않습니다.
        </PageHeader>
        <PaperView />
      </main>
      <Footer />
    </div>
  );
}
