// Every word the dashboard needs to teach, in plain words. The same vocabulary as the landing
// page. Facts from contracts/src/interfaces (INoteSeries payout rules, IDeskCover) and
// docs/interfaces.md.
export const GLOSSARY = {
  note: ['note', 'An agreement on one coin or stock that runs for a set number of weeks, up to about six months: a check every week, then one more week. It’s split into two sides, a weekly income (Earn) and crash insurance (Protect), and the most it can ever pay is locked in the day it’s made.'],
  cover: ['cover', 'Crash insurance on a coin or stock. You pay once, upfront. If its price is below the crash line at a weekly check and ends below the starting price, cover pays you the fall.'],
  NOTE: ['NOTE', 'The income side of a note. You get 0.25% of your amount for every week, paid when the note ends. In a big crash you get back less.'],
  premium: ['premium', 'What cover costs. You pay it once, when you buy it.'],
  crashLine: ['crash line', 'The price that counts as a crash: 60% of the note’s starting price. If the price is below it at a weekly check, the insurance switches on for good.'],
  weeklyCheck: ['weekly check', 'Once a week the price is recorded on the blockchain. Only these prices count, not the moves in between.'],
  startingPrice: ['starting price', 'The price on the note’s first day. Payouts are measured from it, not from today’s price.'],
  endsEarly: ['ends early', 'If the price is at or above the starting price at a weekly check, the note is over that day. Protect gets back part of what it paid and is no longer covered. Earn gets its amount back, plus the income so far.'],
  endDate: ['end date', 'One week after the last weekly check. Whatever hasn’t ended early is paid out then.'],
  amount: ['amount', 'How much the insurance is for, in USDG. Every payout is a share of it: if the cover pays for a 50% fall, that’s half of this amount. The weekly income is 0.25% of it.'],
  usdg: ['USDG', 'A digital dollar made by Paxos: 1 USDG is worth 1 US dollar. All prices and payouts are in USDG.'],
  fullyBacked: ['fully backed', 'The most a note can ever pay is locked in the contract the day it’s made, so every payout is already there.'],
  model: ['model', 'A small AI model that runs on the blockchain and computes the price. Anyone can check what it saw and which version priced a trade.'],
  refuses: ['gives no price', 'Close to the crash line or the starting price on a check day, the value can jump. The model then gives no price instead of guessing.'],
  fairPrice: ['fair price', 'The model’s own price. The Desk sells a little above it and buys a little below it.'],
  spread: ['spread', 'The small gap between the Desk’s selling and buying price. It goes to the people who fund the Desk.'],
  desk: ['Desk', 'The shop that sells and buys both sides at the model’s price. People who fund it earn the spread.'],
  fee: ['fee', 'What this website charges, as a share of the price. The contract caps it, and half of it stays with the Desk as a safety buffer.'],
  coverAvailable: ['cover available', 'How much more the Desk can take on for this coin or stock right now. It stops selling before it could ever fail to pay.'],
  fingerprint: ['fingerprint', 'A unique code for the model’s exact version. If anyone changed the model, the code would change.'],
  slippage: ['slippage limit', 'If the price moves by more than 0.5% before your order goes through, the order is cancelled and you pay nothing.'],
} as const

export type TermId = keyof typeof GLOSSARY
