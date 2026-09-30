# Plan: frontend, landing page, submission text, videos (Max)

> Owner: Max · Created: 2026-09-30 · Updated 2026-09-30 after the contracts and `model/k2` landed on `main`.
> johba owns the contracts, backend and model.
> Repo: `johba37/surrogate-pricer`. Work branch: **`max/frontend`**, based on `main`.
> Hard dates (SGT):
> - **Registration closes Oct 3, 01:01.**
> - **Hedge submission Oct 1, 23:59.**
> - **Final Oct 4, 23:59**, with the internal target at 18:00.

This plan lives on a public branch: no secrets, no private keys. Decide before merging `max/frontend`
whether this file stays.

## State of `main` (2026-09-30, 03:23 SGT)

- **Contracts done:** SeriesFactory, NoteSeries, SeriesToken, AutocallPayout, NoteQuoter, Desk (ERC-4626),
  FixingsRecorder, MockUSDG, MockChainlinkFeed. They pass `forge test` (including invariants) and the
  end-to-end run on a Nitro dev node ([contracts-review.md](../docs/contracts-review.md)).
- **Model:** the default is **`model/k2`**, certified over the **whole life of the note** (max error 18.7 bps,
  [k2-round2.md](../docs/k2-round2.md)). It's distilled from a **jump-diffusion teacher calibrated to TSLA**
  ([teacher-v2.md](../docs/teacher-v2.md)). `model/k1-r1` (first week only) stays as a second certified model.
- **Not deployed yet.** `Deploy.s.sol` simulates cleanly against Robinhood testnet, but nothing was broadcast
  because there was no funded `DEPLOYER_KEY`. With `--broadcast` it writes **`deployments/46630.json`**.
  The Stylus pricer is deployed separately (`cargo stylus deploy`).
- **Testnet setup:** the real USDG (`0x7E95…802F`, 6 decimals) plus a **MockChainlinkFeed "RHTSLA / USD
  (staged demo feed)"**. The quoter treats a feed older than **26 hours** as stale, so **someone must push
  the mock feed at least daily**, or every quote reverts with `FeedStale`.

## Ground rules

- **Use the real product terms in all copy**, not the illustrative ones from the user stories:
  - 26 **weekly** observations, maturity fixing one week after the last one
  - knock-in **60%**, autocall **100%**
  - coupon **0.25% per week** (≈ 13% a year, simple)
  - total vol 55%
- **Where the model answers (k2):** the whole life of the note (1–26 observations remaining, any time in
  the week), with spot 50–120% of the initial fixing. **Except:**
  - two **observation-day bands** (next observation within 1 day): spot 95–105% (autocall, `Uncertified`
    region 0), and, if not yet knocked in, spot 50–70% (knock-in, region 1)
  - the final period after the last observation
  - shortly before each observation (`TooCloseToObservation`)
  - a stale feed

  A refusal is a feature to show, not a bug to hide.
- **Fees:** the integrator fee is capped at **2%** (`MAX_FEE_BPS` = 200), and **20% of every fee funds the
  backstop** (`BACKSTOP_SHARE_BPS` = 2000). The fee on a sell is capped at its gross proceeds.
- Screens, calls, events and error messages are in [docs/interfaces.md](../docs/interfaces.md). Build to that.
  ABIs are in `abi/`, and they must be merged for error decoding (the Desk bubbles up quoter and pricer errors).
- **Implementation notes** (from interfaces.md):
  - `desk.listedSeries()` also returns delisted series, so filter by `desk.listing(s)`.
  - `desk.heldSeries()` exists (not in `IDesk`) for the LP view.
  - `FixingPending` starts as soon as `obsTime < block.timestamp`.
  - LP `maxDeposit`/`maxWithdraw` are 0 on weekends, while a fixing is pending, and in the final week of a
    knocked-in note.
- No claims beyond what's built. Lending/collateral is roadmap. The v2 perpetual note
  ([v2-perpetual-note.md](../docs/v2-perpetual-note.md)) is roadmap too.

## Phase 0: setup and alignment (Wed Sep 30 morning)

- [ ] **0.1 Both of you are registered on HackQuest.** It closes Oct 3, 01:01 SGT and needs the ≤300-char idea pitch and an Arbitrum One wallet.
  → verify: both registrations show on the HackQuest dashboard.
- [x] **0.2 Branch `max/frontend`** created from `main` (this file is its first commit). PR #2 (judge-facing docs)
  is still open. Once it merges, `git rebase origin/main`.
- [ ] **0.3 Unblock the testnet deploy (the critical path for everything live):**
  1. Create a **fresh deployer wallet** used for nothing else, and fund it from the Robinhood testnet faucet (ETH).
     Keep the key **out of the repo**, in an env var or keystore.
  2. johba (or you, with the key): `cargo stylus deploy` for the pricer, then
     `forge script Deploy.s.sol --broadcast`, which writes `deployments/46630.json`. Commit that file.
  3. Seed the demo state: staged history on the mock feed, a series with a past strike (so mid-life states
     exist), an LP deposit, and a listing.
  → verify: `deployments/46630.json` is on `main`; `desk.listedSeries()` returns a live series on the explorer.
- [ ] **0.4 15-minute sync with johba.** Agree on and write down (as a GitHub issue):
  1. Who runs the deploy (0.3) and **when**.
  2. The **mock-feed keeper**: who pushes the staged feed at least daily until Oct 12 (a cron job or script).
  3. The **frontend fee** (e.g. 10 bps, to show the honest price tag) and the fee receiver address.
  4. **Hosting:** the Vercel account and domain.
  → verify: the issue exists with an owner and a date per item.

## Phase 1: frontend skeleton (Wed Sep 30)

- [ ] **1.1 Scaffold `frontend/`:**
  - React + TypeScript, wagmi + viem, a wallet connector, Tailwind. Vite, or Next.js if you prefer.
  - Robinhood testnet chain config: ID `46630`, RPC `https://rpc.testnet.chain.robinhood.com`, explorer `https://explorer.testnet.chain.robinhood.com`.
  - Import `abi/*.json` and merge the ABIs for error decoding.
- [ ] **1.2 Three data modes**, switched by env var, so you're never blocked:
  - `fixtures`: hard-coded series, quotes and **every error case**, shaped exactly like the interfaces.
  - `devnode`: `KEEP_NODE=1 contracts/script/e2e-devnode.sh` leaves a Nitro dev node on port 8647 with
    everything deployed and a full lifecycle already run. That's the most realistic local target, but it needs
    docker, Foundry and cargo-stylus. If you don't have them, skip this mode.
  - `testnet`: reads `deployments/46630.json`.
- [ ] **1.3 Deploy the skeleton to Vercel now** (landing + "app coming"). The link must exist before the hedge submission.
- → verify: `npm run build` passes; the Vercel URL loads on desktop and mobile; the wallet connects to 46630.
- [ ] **1.4 Add a `frontend` build job to `.github/workflows/ci.yml`** (the workflow arrives with PR #2).
  → verify: CI green on the branch.

## Phase 2: landing page and copy (Wed Sep 30 → Thu Oct 1)

One site: the **landing page at `/`, the app at `/app`**. Judges arrive cold, and the landing page still
works when quotes are paused (weekends, observation-day bands, a stale feed).

Sections and their sources (every number must come from these):
1. **Hero:** "The Big Short, fixed" plus a one-line explanation. CTAs: *Open the app* · *Watch the demo* (`docs/pitch.md`, from PR #2).
2. **The three fixes** (mispriced → public price; couldn't pay → full collateral; bank marks → public marks).
3. **How it works in 3 steps:** mint a pair (NOTE + WRITER), the Desk quotes the NOTE, settlement is exact arithmetic on fixings. A simple diagram.
4. **Who it's for:** yield seeker (NOTE) and protection buyer (WRITER), with one short example each, using the **real terms**.
5. **Verify every quote:** what the model saw, `weightsHash`, the certified range, the teacher calibrated to TSLA, and **max error 18.7 bps over the note's life** ([k2-round2.md](../docs/k2-round2.md)).
6. **Why it matters:** €100.1B in German listed certificates, stock tokens EU-only, hidden markups of 1–3%, the lessons from Ribbon and Cega (`docs/market.md`, from PR #2).
7. **Honest risks and fees:** the model can be wrong, and here's how that's bounded; refusals; the fee cap of 2% with 20% to the backstop; testnet with a staged feed; not financial advice (`docs/risk.md`, from PR #2).
8. **Roadmap:** mainnet milestones, the collateral oracle, the v2 perpetual note.
9. **Footer:** repo, docs, both videos, the team.

- [ ] **2.1** Write the copy in `frontend/content/`, with each number's source noted in a comment.
- [ ] **2.2** Build the sections. Keep it readable on mobile.
- [ ] **2.3** Update `docs/user-stories.md` (PR #2) to the real terms, so the site, the stories and the videos agree.
- → verify: every number traces to a sourced doc; no claim beyond what's built; it reads well on mobile; johba has done a quick read.

## Phase 3: app screens (Thu Oct 1 → Sat Oct 3)

Build P0 first, and don't start P1 until P0 works in `testnet` mode (or `devnode` until then).

| Priority | Screen / flow | Calls ([interfaces.md](../docs/interfaces.md)) |
|---|---|---|
| **P0** | **Market list**, no wallet needed | `desk.listedSeries` filtered by `listing`, `series.terms/state`, `quoter.notePriceBps`. Show the *reason* when a quote reverts |
| **P0** | **Note detail**: terms, lifecycle timeline (26 observations + maturity), fixing chart, quote breakdown (fair value · fee · total), **what the model saw** (`quoter.inputs`), **model card** (which pricer, its `weightsHash`, `certifiedRange`, and the bands in plain words) | This screen **is** the transparency pitch |
| **P0** | **Buy NOTE** | `quoteBuy` → `USDG.approve` → `desk.buy` (slippage bound, explorer link) |
| **P0** | **Human error messages** | The table in interfaces.md, including `Uncertified` region 0 (autocall band) vs region 1 (knock-in band) |
| **P0** | **"Try it" panel** | Add the network, faucets (ETH: faucet.testnet.chain.robinhood.com; USDG: faucet.paxos.com) |
| P1 | Sell NOTE (early exit, mid-life) | `quoteSell` → `NOTE.approve` → `desk.sell` |
| P1 | Portfolio + redeem after settlement | NOTE/WRITER balances, current value, `previewRedeem`, `redeem` (auto-advance) |
| P1 | Protect (hedger) | `previewMint` → approve → `series.mint` → sell NOTE → keep WRITER; `redeemPair` |
| P2 | LP deposit/withdraw | ERC-4626 + `heldSeries()`; explain why `maxDeposit` is 0 instead of letting the transaction revert |
| P2 | Keeper button | `feed.getRoundData` binary search → `recorder.recordFixing` → `series.advance` |
| P2 | Trade history | `NoteBought`/`NoteSold` via `getLogs` (carries `weightsHash`) |

→ verify each screen: it works in `fixtures` and in `testnet` mode, every error path shows its message, and
a judge **without a wallet** can see the list, the detail and the model card.

## Phase 4: submission text (draft Thu Oct 1, final Sun Oct 4)

Source of truth: `docs/submission.md` (PR #2). Paste from there into HackQuest.
- [ ] Project name, one-liner, long description, tracks/tags (DeFi, RWA, Infra), tech stack (Solidity, Rust/Stylus, Python, React).
- [ ] Sponsor checkboxes: **Robinhood Chain, Paxos/USDG, OpenZeppelin**.
- [ ] Frontend link, core addresses from `deployments/46630.json` (`network: address — label`), the factory, the tokens (NOTE/WRITER per series via the factory), the "built during" text (259 chars).
- [ ] **Progress log:** a changelog with dates and PR/commit links. Past winners all had this.
- [ ] Update the pitch line to the current model (k2, whole life, 18.7 bps max error), within 300 chars.
- [ ] Fill the README's deployed-contracts, demo and evidence rows. No TBD left.
- [ ] **Hedge: submit a valid version by Thu Oct 1, 23:59 SGT**, even if the app is partial (it can be edited until Oct 4).
- → verify: limited fields are ≤ 300 chars; addresses are clickable on the explorer; the links open in a private window.

## Phase 5: videos (Fri Oct 2 → Sat Oct 3)

**Pitch video (≤ 2:00): why it matters.** Slides + voiceover (or face cam), with captions.

| Time | Content |
|---|---|
| 0:00–0:20 | The Big Short hook (`docs/pitch.md`) |
| 0:20–0:45 | The problem: notes are priced by the issuer's private model; path-dependent notes can't be priced on-chain |
| 0:45–1:15 | The solution: pricer in Stylus (a teacher calibrated to TSLA, max error 18.7 bps), NOTE/WRITER, full collateral, public marks |
| 1:15–1:35 | Market: €100.1B in German certificates, stock tokens EU-only, why now |
| 1:35–1:50 | The honest line + the roadmap (25/25/50 milestones, v2 perpetual note) |
| 1:50–2:00 | Close line, team, links |

**Demo video (≤ 2:00): proof it works.** A screen recording of the **live testnet app**, no slides. Record
on a series with a past strike, so mid-life states are real, and **not** in an observation-day band.

1. Market list → a note: terms, the fixing chart, the lifecycle.
2. **What the model saw + the model card** (`weightsHash`, certified range, the bands in plain words).
3. **Buy NOTE**: the quote breakdown with the visible fee, the tx on the explorer.
4. **Sell NOTE mid-life** at the model's quote (the e2e run already does this at 16 observations remaining).
5. **Show a refusal** (`Uncertified` near a barrier on observation day, or `TooCloseToObservation`): "the model refuses rather than guesses".
6. Optional: the hedger mints a pair, sells the NOTE, keeps the WRITER.

- [ ] Write both scripts first (Fri), rehearse on the seeded testnet state, then record (Sat).
- [ ] Tools: QuickTime/OBS to record, CapCut/iMovie/Descript to edit, captions on, 1080p.
- [ ] Upload both to YouTube as *unlisted*; link them in HackQuest, the README and the landing page.
- → verify: both ≤ 2:00, clear audio, the numbers match the docs, the links work logged out.

## Phase 6: polish and submit (Sun Oct 4)

- [ ] Final pass: landing page, app (P0 green in testnet mode), README, `docs/submission.md`.
- [ ] CI green on `main`; merge `max/frontend` by PR (johba reviews).
- [ ] **Final submit by 18:00 SGT** (6h buffer). After that, only link fixes.

## Phase 7: keep it alive during judging (Oct 5 → Oct 12)

Judges may open the app any day until the winners are announced (Oct 12, 14:00 SGT).
- **The mock feed must be pushed at least every 26 hours**, or every quote reverts with `FeedStale`. Run it as
  a cron or keeper, and test it before Oct 4.
- With `model/k2`, a listed series stays quotable for its whole life (except the bands), so a new series
  every week isn't required. Listing one more series shortly before Oct 4 still helps, because it gives
  fresh early-life states.
- The landing page and the demo video carry the story when a quote is refused. The app says *why* and links to the demo video.

## Dependencies on johba

| You need | When | Fallback if late |
|---|---|---|
| **Testnet broadcast** + `deployments/46630.json` (blocked only on a funded deployer key) | **Wed** | Develop in `fixtures`/`devnode`; the hedge submission uses the addresses as soon as they exist |
| Demo seed on testnet (a series with a past strike, LP deposit, listing) | Thu | Record the demo on the dev node and say so in the video |
| Mock-feed keeper (≤ 26h) through Oct 12 | Sun Oct 4 | A manual push each morning, then the landing page + demo video carry it |
| Fee receiver address | Thu | `feeBps = 0` |

## Cut list (in this order if behind)

P2 screens → hedger flow → portfolio/redeem → landing sections 6–8 shortened.
**Never cut:** the market list, the note detail with the model card, buy, human error messages, both videos, the hedge submission.
