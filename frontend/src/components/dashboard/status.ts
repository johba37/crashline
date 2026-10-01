// Contract states and refusals -> tone, icon, label and message (spec.json statusMap), in the
// dashboard's plain vocabulary (glossary.ts). Facts per docs/interfaces.md "Errors worth a human message".
import {
  CheckCircle, FlagCheckered, Gauge, HourglassMedium, Info, MoonStars, Pulse, Queue, ShieldWarning,
  Timer, TrendDown, Vault, X, XCircle, type Icon,
} from '@phosphor-icons/react'
import type { Refusal, SeriesView } from '../../market/types.ts'

export type Tone = 'go' | 'hold' | 'abort' | 'info' | 'neutral'
export type Status = { tone: Tone; icon: Icon; label: string; message?: string }

const FIELDS = ['the price against the start', 'the distance to the crash line', 'volatility', 'the crash line',
  'the starting price level', 'the weekly income', 'the time to the end', 'the time to the next check',
  'the checks left', 'the crash flag']

export const quoteLive: Status = { tone: 'go', icon: Pulse, label: 'Price live' }
export const knockedIn: Status = { tone: 'abort', icon: TrendDown, label: 'Crash line hit' }
export const autocalled: Status = { tone: 'go', icon: FlagCheckered, label: 'Ended early' }
export const settled: Status = { tone: 'neutral', icon: CheckCircle, label: 'Ended' }
export const confirmed: Status = { tone: 'go', icon: CheckCircle, label: 'Done' }

export function refusalStatus({ error, args }: Refusal): Status {
  switch (error) {
    case 'FeedStale':
      return { tone: 'hold', icon: MoonStars, label: 'Market closed', message: 'The stock market is closed. Prices come back when it opens.' }
    case 'FixingPending':
      return { tone: 'hold', icon: HourglassMedium, label: 'Waiting for the weekly price', message: 'This week’s closing price isn’t recorded yet. Prices come back once it is.' }
    case 'TooCloseToObservation':
      return { tone: 'hold', icon: Timer, label: 'Paused for the weekly check', message: 'Trading pauses shortly before each weekly check.' }
    case 'OutOfRange':
      return { tone: 'hold', icon: ShieldWarning, label: 'Outside what the model knows', message: `This is outside the range the model was tested on (${FIELDS[Number(args[0])] ?? 'one of its inputs'}). It gives no price rather than guess.` }
    case 'Uncertified':
      return Number(args[0]) === 0
        ? { tone: 'hold', icon: ShieldWarning, label: 'Near the starting price', message: 'Too close to the starting price on a check day, where the value can jump. The model gives no price rather than guess.' }
        : { tone: 'hold', icon: ShieldWarning, label: 'Near the crash line', message: 'Too close to the crash line on a check day, where the value can jump. The model gives no price rather than guess.' }
    case 'NotLive':
      return { tone: 'neutral', icon: Info, label: 'Not open', message: 'This note hasn’t started yet, or it has already ended.' }
    case 'RiskBudgetExceeded':
      return { tone: 'hold', icon: Gauge, label: 'The Desk is full for now', message: 'The Desk can’t take on more for this stock right now, so it can always pay. Selling what you hold still works.' }
    case 'QueuePending':
      return { tone: 'hold', icon: Queue, label: 'The Desk is paying out', message: 'The Desk is paying back the people who fund it first. Buying resumes once that’s done; selling still works.' }
    case 'CapExceeded':
      return { tone: 'abort', icon: XCircle, label: 'Not enough on offer', message: 'The Desk can’t sell this much of this note. Choose a smaller amount.' }
    case 'ReservedForClaims':
      return { tone: 'hold', icon: Vault, label: 'The Desk is paying out', message: 'The Desk’s free money is set aside for the people who fund it. Try a smaller amount or later.' }
    case 'Slippage':
      return { tone: 'abort', icon: XCircle, label: 'Price moved', message: 'The price moved more than 0.5% before your order went through, so nothing happened. Check the new price and try again.' }
    case 'FeeTooHigh':
      return { tone: 'abort', icon: XCircle, label: 'Fee too high', message: 'The fee is above what the contract allows.' }
    case 'Inconsistent':
      return { tone: 'abort', icon: XCircle, label: 'Input error', message: 'The price inputs don’t match. This is a bug on our side.' }
    case 'UserRejected':
      return { tone: 'neutral', icon: X, label: 'Cancelled in your wallet' }
    default:
      return { tone: 'abort', icon: XCircle, label: 'It didn’t go through', message: typeof args[0] === 'string' ? args[0] : error }
  }
}

/** A series' headline state: why it has no price, else crash line hit or live. */
export function seriesStatus(s: SeriesView): Status {
  if (!s.mid.ok) return refusalStatus(s.mid.refusal)
  return s.state.knockedIn ? knockedIn : quoteLive
}

/** Chip and notice colours per tone. Tints only behind their own role colour (spec colour rules). */
export const toneClass: Record<Tone, string> = {
  go: 'bg-go-soft text-go',
  hold: 'bg-hold-soft text-hold',
  abort: 'bg-abort-soft text-abort',
  info: 'bg-info-soft text-info',
  neutral: 'bg-surface-overlay text-ink-muted',
}
