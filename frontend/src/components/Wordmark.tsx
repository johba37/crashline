/**
 * The name next to the mark: "CrashLine", slanted like the ship, with the dot of the i in the
 * arrows' orange. The dot is painted by clipping a two-colour ground to the letter, so the name
 * stays real text.
 */
export default function Wordmark() {
  return (
    <span className="inline-block -skew-x-[11deg] type-wordmark whitespace-nowrap">
      CrashL
      <span className="bg-[linear-gradient(#ed440e_36%,currentColor_36%)] bg-clip-text [-webkit-text-fill-color:transparent]">
        i
      </span>
      ne
    </span>
  )
}
