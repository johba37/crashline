# Roadmap: yield on USDG that sits still

Status: roadmap, 2026-09-30. Not for the Oct 4 submission. Nothing here is built, and no
venue on Robinhood Chain has been checked: the venue facts below (instant ERC-4626
vaults, venues with scheduled redemptions) are as reported by johba, not probed.

## Why

The vault earns a premium for taking crash risk. It earns no interest: two piles of USDG
sit still at 0%.

| Pile | Where | Why it sits still |
|---|---|---|
| Idle USDG | the Desk | waiting for a trade or a withdrawal; a series locks collateral only until it settles |
| Collateral | each series' escrow | locked until `redeemPair` or settlement |

An LP can hold a T-bill token instead, so the premium has to beat that rate on its own.

## Which venue fits which pile

What decides it is how fast the pile can be asked for its money.

| Pile | Must pay | Instant ERC-4626 venue | Scheduled venue |
|---|---|---|---|
| Desk USDG that trades may need | in the same transaction | yes: the Desk withdraws inside the trade | no |
| Desk USDG beyond what trades can use | days are fine | yes | yes |
| Series collateral | at any time, to anyone, with no admin | only as the collateral token itself | no |

1. **Desk idle USDG in an instant venue.** The largest pile and the smallest change. The
   Desk keeps little raw USDG and pulls what a trade needs when it needs it. Its value is
   exact on-chain (`convertToAssets`), so the share price stays exact.
2. **A scheduled venue for the part that can wait.** The risk budget already caps how
   much of the vault trades can lock, so the rest is idle by construction. The redemption
   queue (`IDeskQueue`) is what makes a delay acceptable: LP withdrawals no longer need
   USDG at once, so a queued request can trigger a scheduled redemption and be paid when
   it lands.
3. **Series collateral, last.** The core has no admin and pays at any time, so a scheduled
   venue can't hold it. An instant venue can only if the series' collateral is the
   venue's share token instead of USDG: a second factory, notes denominated in shares.
   That puts the venue's risk under every NOTE and WRITER holder, not only under the LPs.

## What it does not change

- **No retraining.** If only the Desk's idle USDG earns, the escrow still earns 0 and the
  teacher's discount rate of 0 (`rDiscount`, teacher v3) stays right. If the collateral
  becomes a share token that grows at about the drift rate, a note priced in shares is
  also discounted at 0.
- **The core**, for steps 1 and 2.

## Shape of the change

The Desk is 20,749 bytes of 24,576, so this is a separate treasury contract, not more
Desk code. The Desk asks it for two things: USDG now (`pull`), and the value of what it
holds (for `totalAssets`). The curator sets a cap per venue.

## Open

1. Which venues: address, redemption schedule, share decimals, who can pause or upgrade.
2. "Instant" is not a promise: a lending vault at full utilization pays nothing. The
   Desk has to read the venue's withdrawable amount and treat only that as idle.
3. Valuing a scheduled venue: its position and its redemptions in flight need a price the
   Desk can trust, or LPs can deposit and withdraw against a stale mark.
4. Venue risk is new risk for LPs (a loss, a pause). Caps per venue; how a loss is shown
   in the share price.
5. Step 3: NOTE par becomes 1 share, not 1 USDG. Units for the frontend, and the quoter's
   accrued coupon, are not designed.
