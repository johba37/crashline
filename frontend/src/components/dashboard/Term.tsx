import type { ReactNode } from 'react'
import InfoTip from '../Tooltip.tsx'
import { GLOSSARY, type TermId } from './glossary.ts'

/** A word that needs explaining, followed by its info icon. The word itself stays plain text. */
export default function Term({ t, children }: { t: TermId; children?: ReactNode }) {
  const [label, text] = GLOSSARY[t]
  return (
    <span className="inline-flex items-center gap-0.5 whitespace-nowrap">
      {children ?? label}
      <InfoTip label={label}>{text}</InfoTip>
    </span>
  )
}
