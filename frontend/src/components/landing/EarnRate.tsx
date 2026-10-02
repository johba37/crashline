// PLACEHOLDER, red on purpose: Earn's weekly income across the notes that are open. To make it
// live: useMarket(false).data?.series (src/market/useMarket.ts), each note's
// terms.couponBpsPerPeriod through pct() (as dashboard/SeriesDetail.tsx does), lowest to highest,
// and one figure while there is only one note. Then drop the red.
export default function EarnRate() {
  return <mark className="rounded-sm bg-abort-soft px-1 whitespace-nowrap text-abort">x% to x% a week</mark>
}
