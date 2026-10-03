// What Earn pays a week, on average over the notes. Today every note pays the same 0.25% (25 bps):
// the model is certified for that coupon only (tools/domains/k3.json) and the curator lists notes
// with it (contracts/script/curator.sh). Once notes pay different rates, work it out from the open
// notes: useMarket(false).data?.series (src/market/useMarket.ts), each note's terms.couponBpsPerPeriod.
// Bold, so it stands out in the sentence (user, 2026-10-03). The "Ø" is the average sign.
export default function EarnRate() {
  return (
    <strong className="font-semibold whitespace-nowrap">
      <span aria-hidden="true">Ø</span>
      <span className="sr-only">on average</span> 0.25% per week
    </strong>
  )
}
