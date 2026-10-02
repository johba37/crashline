// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IDesk} from "./IDesk.sol";

/// L3, the cover side of the Desk (extends IDesk): it trades both legs of a
/// series at two prices, and its positions in one stock are limited by a risk budget.
///
/// Cover. WRITER is crash cover: it pays the coupons and collects the loss of a
/// knocked-in note. `buyCover` sells it to a holder of the stock for the
/// premium alone; the Desk puts up the rest of the pair's collateral from LP
/// funds and keeps the NOTE. `sellCover` buys it back. A pair redeems for
/// maxPayout at any time, so with maxBps = maxPayoutPerNote in bps of notional:
///   cover price = maxBps - NOTE price
///
/// Two prices. The listing's spread has a vol band and two flat parts (all 0
/// until set). The model is asked at the listing's vol minus and plus the
/// band; lo and hi are the lower and the higher of its two NOTE quotes, each
/// capped at maxBps (one quote, lo = hi, while the band is 0):
///   NOTE ask  = min(hi + askBps, maxBps)    buy      (the Desk sells NOTE)
///   NOTE bid  = lo - bidBps, floored at 0   sell     (the Desk buys NOTE)
///   cover ask = maxBps - NOTE bid           buyCover (the Desk ends up with NOTE)
///   cover bid = maxBps - NOTE ask           sellCover
/// The note is worth less at a higher vol, so the Desk buys NOTE (sells cover)
/// at the higher vol and sells NOTE (buys cover back) at the lower one, and
/// the spread is widest where the note's value depends most on vol. Taking
/// the lower and higher quote, not the one at a fixed end of the band, keeps
/// bid <= ask where that ordering flips. The flat parts are a floor for what
/// the band can't see (student and teacher error). A model whose certified vol
/// is a single value takes no band (ModelMismatch(FIELD_VOL)).
/// NOTE bid + cover bid <= maxBps <= NOTE ask + cover ask: minting a pair to
/// sell both legs, or buying both legs to redeem the pair, never pays. The
/// spread stays in the vault; the integrator fee is charged on top.
/// Marks (totalAssets, risk) use the quote at the listing's vol itself.
/// `priceBps` in quotes and trade events is the price applied, spread included:
///   buy, buyCover:   cost     = ceil(amount * priceBps / 1e4)  + fee
///   sell, sellCover: proceeds = floor(amount * priceBps / 1e4) - fee
///
/// Integrator fee. For NOTE it is a share of the notional, as in IDesk:
/// fee = ceil(noteAmount * feeBps / 1e4), feeBps <= MAX_FEE_BPS. For cover it
/// is a share of the premium, the first term of cost or proceeds above:
/// fee = ceil(premium * feeBps / 1e4), feeBps <= MAX_COVER_FEE_BPS. On both
/// legs the feeReceiver gets the fee minus the BACKSTOP_SHARE_BPS slice.
///
/// Inventory. Every trade is served from inventory first. The Desk mints a
/// pair only for the shortfall and keeps the other leg; a leg handed in is
/// paired with the other leg the Desk holds and redeemed. Two limits apply to
/// a trade that leaves the Desk holding more of a leg than before:
///  - WRITER: at most `Listing.capNotional` per series (CapExceeded), so
///    `capNotional` = 0 sells NOTE from inventory only. `Listing.soldNotional`
///    reports the WRITER the Desk holds now; it is computed when `listing` is
///    read, not stored.
///  - both legs: the risk budget of the series' feed (RiskBudgetExceeded).
///
/// Risk budget, per feed (= per stock), in bps of vault assets, 0 until set:
///   atRisk = sum over the Desk's unsettled positions on that feed of what
///            they can still lose: NOTE mark - the coupons, WRITER mark
///   limit  = vault assets * riskBudgetBps / 1e4
/// A position whose payout is already certain counts 0. While a series can't
/// be quoted its NOTE and WRITER count 1 USDG per unit at risk (the most
/// either can lose) and its NOTE counts only its coupons as an asset, so the
/// check never reverts with a quoter error and errs against new risk.
///
/// FRONTENDS: the quote functions do NOT apply the risk budget. A trade that
/// adds to the Desk's position (buyCover; sell of NOTE the Desk can't pair;
/// buy or sellCover that leave it with WRITER) reverts RiskBudgetExceeded even
/// right after a successful quote. Read `risk(feed)` before offering it: the
/// room left is `limit - atRisk`.
interface IDeskCover is IDesk {
    struct Spread {
        uint16 bidBps; // below the model's lower NOTE quote when the Desk buys NOTE / sells cover
        uint16 askBps; // above its higher one when the Desk sells NOTE / buys cover back
        uint16 volBandBps; // the two quotes are taken at the listing's vol -/+ this (annualized vol, bps)
    }

    event SpreadSet(address indexed series, uint16 bidBps, uint16 askBps, uint16 volBandBps);
    event RiskBudgetSet(address indexed feed, uint16 budgetBps);
    /// `priceBps` is the cover price applied: maxBps - NOTE bid.
    event CoverBought(
        address indexed series,
        address indexed buyer,
        address to,
        uint256 writerAmount,
        uint16 priceBps,
        uint256 cost,
        uint16 feeBps,
        address feeReceiver,
        bytes32 weightsHash
    );
    /// `priceBps` is the cover price applied: maxBps - NOTE ask.
    event CoverSold(
        address indexed series,
        address indexed seller,
        address to,
        uint256 writerAmount,
        uint16 priceBps,
        uint256 proceeds,
        uint16 feeBps,
        address feeReceiver,
        bytes32 weightsHash
    );

    error SpreadTooWide(uint16 spreadBps);
    error BudgetTooHigh(uint16 budgetBps);
    error RiskBudgetExceeded(uint256 atRisk, uint256 limit);
    /// Holding one more series would exceed MAX_HELD_SERIES.
    error HeldSeriesLimit();

    function MAX_SPREAD_BPS() external view returns (uint16);
    /// Most series the Desk may hold at once; bounds the totalAssets loop.
    function MAX_HELD_SERIES() external view returns (uint256);
    /// Cap of the integrator fee on cover trades, in bps of the premium.
    function MAX_COVER_FEE_BPS() external view returns (uint16);

    /// Series whose NOTE or WRITER the Desk holds (valued in totalAssets).
    function heldSeries() external view returns (address[] memory);
    function spread(address series) external view returns (Spread memory);
    function riskBudgetBps(address feed) external view returns (uint16);

    /// What the Desk's positions on `feed` can lose, and the budget's limit, in USDG base units.
    /// Quotes don't check it: a frontend must, before offering a trade that adds to a position.
    function risk(address feed) external view returns (uint256 atRisk, uint256 limit);

    function quoteBuyCover(address series, uint256 writerAmount, uint16 feeBps)
        external
        view
        returns (uint256 cost, uint16 priceBps);
    function quoteSellCover(address series, uint256 writerAmount, uint16 feeBps)
        external
        view
        returns (uint256 proceeds, uint16 priceBps);

    /// Buyer pays `cost` USDG (approve the Desk) and receives `writerAmount` WRITER at `to`.
    /// Needs an active listing.
    function buyCover(
        address series,
        uint256 writerAmount,
        uint256 maxCost,
        uint16 feeBps,
        address feeReceiver,
        address to
    ) external returns (uint256 cost);

    /// Seller hands in `writerAmount` WRITER (approve the Desk) and receives `proceeds` USDG
    /// at `to`. Open for delisted series; WRITER the Desk can't pair with its NOTE counts
    /// against `capNotional`.
    function sellCover(
        address series,
        uint256 writerAmount,
        uint256 minProceeds,
        uint16 feeBps,
        address feeReceiver,
        address to
    ) external returns (uint256 proceeds);

    // --- curator (owner) -----------------------------------------------------
    /// Both flat spreads <= MAX_SPREAD_BPS; the series must have a listing, and its vol
    /// -/+ `volBandBps` must lie inside the pricer's certified vol range (ModelMismatch(FIELD_VOL)).
    /// `listSeries` re-checks the band when it changes the model or the vol.
    function setSpread(address series, uint16 bidBps, uint16 askBps, uint16 volBandBps) external;
    /// `budgetBps` <= 10_000. While it is 0 no trade may add to the Desk's positions on `feed`.
    function setRiskBudget(address feed, uint16 budgetBps) external;
}
