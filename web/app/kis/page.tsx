import Footer from "@/components/Footer";
import KisChallenger from "@/components/KisChallenger";
import KisPanel from "@/components/KisPanel";
import KisQuality from "@/components/KisQuality";
import Nav from "@/components/Nav";
import PageHeader from "@/components/PageHeader";

export const dynamic = "force-dynamic";

/**
 * KIS모의투자 (docs/desk.md 4장, docs/paper.md 15장, 2026-10-16 웹 개편 3단계). 가상모의투자 지주사의 주문을 한국투자증권 **모의** 계좌에 그대로 낸 결과와,
 * 그것이 가상 체결 판정과 얼마나 맞는지(실행 품질). 실전 계좌 주문은 없다.
 */
export default function KisPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/kis" />
      <main className="mx-auto w-full max-w-5xl flex-1 space-y-5 px-4 py-4">
        <PageHeader title="KIS모의투자" doc="docs/paper.md">
          가상모의투자 지주사의 주문을 한국투자증권 모의 계좌에 그대로 내 실제 체결을 봅니다. 실전 계좌 주문은 없습니다. 아래 실행 품질은 가상 성적을 실전으로 옮길 때 깎을
          몫입니다.
        </PageHeader>
        <KisQuality />
        <KisPanel />
        <KisChallenger />
      </main>
      <Footer />
    </div>
  );
}
