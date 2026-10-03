// GitHub links for the landing page and the nav. Only files that exist on main.
// After PR #2 merges: add docs/risk.md, docs/pitch.md, docs/user-stories.md and docs/market.md.
const REPO = 'https://github.com/johba37/crashline'
const doc = (file: string) => `${REPO}/blob/main/docs/${file}`

export const GITHUB = {
  repo: REPO,
  readme: `${REPO}#readme`,
  docs: `${REPO}/tree/main/docs`,
  architecture: doc('architecture.md'),
  interfaces: doc('interfaces.md'),
  accuracy: doc('k3-vol-input.md'),
  simulation: doc('teacher-v2.md'),
  contractsReview: doc('contracts-review.md'),
  perpetualNote: doc('v2-perpetual-note.md'),
}

// Arbitrum's own introduction to Stylus (PriceEngine.tsx).
export const STYLUS_DOCS = 'https://docs.arbitrum.io/stylus/gentle-introduction'
