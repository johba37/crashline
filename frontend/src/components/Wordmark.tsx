/** The name next to the mark: "CrashLine", slanted like the ship. `large` in the nav bars. */
export default function Wordmark({ large }: { large?: boolean }) {
  return <span className={`inline-block -skew-x-[11deg] ${large ? 'type-wordmark-lg' : 'type-wordmark'} whitespace-nowrap`}>CrashLine</span>
}
