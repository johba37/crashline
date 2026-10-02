// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC4626} from "@openzeppelin/contracts/interfaces/IERC4626.sol";
import {IPerpQuoter} from "./IPerpQuoter.sol";
import {IPerpPricer} from "./IPerpPricer.sol";
import {IPerpFactory} from "./IPerpFactory.sol";

/// A time-averaged market price for a stock while its feed is blind (weekends).
/// An adapter over an on-chain pool implements it; the Desk caps what it returns.
interface IWeekendSource {
    /// Time-averaged price of `feed`'s stock in USDG, feed decimals (8). Reverts if it has none.
    function averagePrice(address feed) external view returns (uint256);
}

/// L3 of the v2 perpetual note: the market for both legs of a perpetual
/// series, and the pricer's only in-path use. The v1 Desk (IDesk, IDeskCover)
/// carried over: an ERC-4626 vault on USDG whose LPs fund a desk that sells
/// and buys back NOTE and WRITER ("cover") around the model's quote at two
/// prices, within a risk budget per stock. The LP redemption queue is
/// IDeskQueue, unchanged. What is different from v1:
///
/// Units. Trades are in tokens; prices are per unit of live notional, and a
/// token stands for `notionalPerToken / 1e27` of it (IPerpSeries), less after
/// each fixing. With notional = amount * notionalPerToken / 1e27:
///   buy, buyCover:   cost     = ceil(notional * priceBps / 1e4)  + fee
///   sell, sellCover: proceeds = floor(notional * priceBps / 1e4) - fee
///   fee: NOTE  ceil(floor(notional) * feeBps / 1e4), feeBps <= MAX_FEE_BPS
///        cover ceil(premium * feeBps / 1e4),         feeBps <= MAX_COVER_FEE_BPS
/// `capNotional` and the risk budget are in USDG of live notional too.
///
/// Price. NOTE = the quoter's price (closed form + model correction + coupon
/// reserve); cover = pairBps - NOTE, pairBps = (1e6 + couponReserve) / 100: a
/// pair redeems for that at any time. The two prices are the v1 rule: the
/// quoter is asked at the listing's vol -/+ the band, lo and hi are the lower
/// and higher NOTE quote capped at pairBps, and
///   NOTE ask  = min(hi + askBps, pairBps)    NOTE bid  = lo - bidBps, floored at 0
///   cover ask = pairBps - NOTE bid           cover bid = pairBps - NOTE ask
///
/// Earnings. The next earnings date is a listing parameter like vol: it
/// changes the price, never the payout. Once it has passed the series can't
/// be quoted until the curator names the next one (`setEarnings`).
///
/// Released cash. The Desk's NOTE and WRITER earn USDG at every fixing
/// (IPerpSeries.claimable). It counts as an asset at once; `collect` pulls it.
/// Nothing settles: a position ends when it is sold, paired and redeemed, or
/// when the series closes and everything has been released.
///
/// Weekend price. From Saturday 01:00 UTC to Monday 00:00 UTC a feed that
/// has not updated since the window began is blind, however recent its last
/// round (Friday's close): the Desk does not trade at it. If the curator set
/// a source for the feed, and the feed traded within 4 hours of the window's
/// start, trades go at a time-averaged market price (IWeekendSource), capped
/// at +/- capBps around the feed's last price and with `spreadBps` added on
/// both sides; otherwise they revert FeedStale. It is a price level only:
/// fixings never use it, and LP flows stay paused for a Desk that holds
/// positions on a blind feed (totalAssets needs the live feed). A feed that
/// does update over the weekend is used as on any other day.
///
/// Risk budget, per feed, in bps of vault assets, 0 until set:
///   atRisk = what the Desk's positions on that feed can still lose:
///            NOTE mark - its coupon reserve, WRITER mark
/// While a series can't be quoted its positions count 1 USDG per unit of
/// notional at risk and its NOTE only its coupon reserve as an asset.
///
/// FRONTENDS: as in v1, quotes do NOT apply the risk budget or look at the
/// queue. Read `risk(feed)` and `queuedShares()` before offering a trade
/// that adds to the Desk's position.
interface IPerpDesk is IERC4626 {
    /// Which leg the trader buys or sells.
    enum Side {
        BuyNote, // buy: the Desk sells NOTE
        SellNote, // sell: the Desk buys NOTE
        BuyCover, // buyCover: the Desk sells WRITER and ends up with NOTE
        SellCover // sellCover: the Desk buys WRITER
    }

    struct Listing {
        bool active;
        IPerpPricer pricer; // the model for this series' product (or a formula-only pricer)
        uint16 volBpsAnnual; // implied vol used for every quote of this series
        uint40 nextEarnings; // the stock's next earnings date
        uint128 capNotional; // max WRITER the Desk may hold in this series, USDG of live notional
    }

    struct Spread {
        uint16 bidBps; // below the model's lower NOTE quote when the Desk buys NOTE / sells cover
        uint16 askBps; // above its higher one when the Desk sells NOTE / buys cover back
        uint16 volBandBps; // the two quotes are taken at the listing's vol -/+ this
    }

    struct Weekend {
        IWeekendSource source; // 0: no weekend price for this feed
        uint16 capBps; // the weekend price stays within +/- this of the feed's last price
        uint16 spreadBps; // added to bidBps and askBps while the weekend price is in use
    }

    /// Emitted on listing and on every update (new model, vol, earnings date or cap).
    event SeriesListed(
        address indexed series,
        address indexed pricer,
        bytes32 weightsHash,
        uint16 volBpsAnnual,
        uint40 nextEarnings,
        uint128 capNotional
    );
    event SeriesDelisted(address indexed series);
    event EarningsSet(address indexed series, uint40 nextEarnings);
    event SpreadSet(address indexed series, uint16 bidBps, uint16 askBps, uint16 volBandBps);
    event RiskBudgetSet(address indexed feed, uint16 budgetBps);
    event WeekendSet(address indexed feed, address source, uint16 capBps, uint16 spreadBps);
    event MinSecsToFixingSet(uint32 secs);
    /// One event for all four trades. `amount` tokens = `notional` USDG of live notional;
    /// `priceBps` is the price applied to that leg, spread included; `paid` is the cost of a
    /// buy or the proceeds of a sell, fee included; `weightsHash` is the model that priced it.
    event Traded(
        address indexed series,
        address indexed trader,
        Side indexed side,
        address to,
        uint256 amount,
        uint256 notional,
        uint16 priceBps,
        uint256 paid,
        uint16 feeBps,
        address feeReceiver,
        bytes32 weightsHash
    );
    /// USDG pulled from the series: released cash, plus pairs the Desk held and redeemed.
    event Collected(address indexed series, uint256 collateralOut);

    error NotListed(address series);
    error NotFactorySeries(address series);
    /// The series is not the pricer's product (0 knock-in, 1 melt share, 2 interval),
    /// or the vol band leaves its certified vol range (3).
    error ModelMismatch(uint8 field);
    error CapExceeded(uint256 requested, uint256 available); // in tokens
    error FeeTooHigh(uint16 feeBps);
    error Slippage(uint256 actual, uint256 limit);
    error TooCloseToFixing(uint40 fixingTime); // the band before each fixing
    error SpreadTooWide(uint16 spreadBps);
    error BudgetTooHigh(uint16 budgetBps);
    error RiskBudgetExceeded(uint256 atRisk, uint256 limit);
    error ZeroAmount();
    // plus IPerpQuoter errors and the model's OutOfRange / Uncertified, bubbled up

    function factory() external view returns (IPerpFactory);
    function quoter() external view returns (IPerpQuoter);
    function MAX_FEE_BPS() external view returns (uint16);
    function MAX_COVER_FEE_BPS() external view returns (uint16);
    function BACKSTOP_SHARE_BPS() external view returns (uint16);
    function MAX_SPREAD_BPS() external view returns (uint16);
    function MAX_WEEKEND_CAP_BPS() external view returns (uint16);
    function minSecsToFixing() external view returns (uint32);

    function listing(address series) external view returns (Listing memory);
    function listedSeries() external view returns (address[] memory); // delisted ones included
    function heldSeries() external view returns (address[] memory);
    function spread(address series) external view returns (Spread memory);
    function weekend(address feed) external view returns (Weekend memory);
    function riskBudgetBps(address feed) external view returns (uint16);

    /// The spot the Desk prices `series` at now, and whether it is the weekend price.
    /// Reverts like a quote would (FeedStale, BadFeedAnswer).
    function spotOf(address series) external view returns (uint256 spot, bool weekendPrice);

    /// The NOTE mid per unit of live notional at the listing's vol and the Desk's spot (the
    /// weekend price included): what the risk budget marks positions at. Reverts where a quote would.
    function midPriceBps(address series) external view returns (uint16);

    /// The same at the live feed only: what `totalAssets` marks positions at. Reverts
    /// `FeedStale` while the feed is blind, the weekend window included.
    function navPriceBps(address series) external view returns (uint16);

    /// What the Desk's positions on `feed` can lose, and the budget's limit, in USDG base units.
    function risk(address feed) external view returns (uint256 atRisk, uint256 limit);

    /// What a trade of `amount` tokens on `side` pays now: the cost of a buy or the proceeds
    /// of a sell, fee included, and the price applied to that leg. Reverts where the trade
    /// would for its price, fee or cap; it does not apply the risk budget or the queue.
    function quote(address series, Side side, uint256 amount, uint16 feeBps)
        external
        view
        returns (uint256 paid, uint16 priceBps);

    /// Buyer pays `cost` USDG (approve the Desk) and receives `amount` NOTE at `to`. Needs an active listing.
    function buy(address series, uint256 amount, uint256 maxCost, uint16 feeBps, address feeReceiver, address to)
        external
        returns (uint256 cost);

    /// Seller hands in `amount` NOTE (approve the Desk) and receives `proceeds` USDG at `to`.
    function sell(address series, uint256 amount, uint256 minProceeds, uint16 feeBps, address feeReceiver, address to)
        external
        returns (uint256 proceeds);

    /// Buyer pays the premium (approve the Desk) and receives `amount` WRITER at `to`; the Desk keeps the NOTE.
    function buyCover(address series, uint256 amount, uint256 maxCost, uint16 feeBps, address feeReceiver, address to)
        external
        returns (uint256 cost);

    /// Seller hands in `amount` WRITER (approve the Desk) and receives `proceeds` USDG at `to`.
    function sellCover(
        address series,
        uint256 amount,
        uint256 minProceeds,
        uint16 feeBps,
        address feeReceiver,
        address to
    ) external returns (uint256 proceeds);

    /// Permissionless: pull the USDG released to the Desk's NOTE and WRITER, and redeem any pairs it holds.
    function collect(address series) external returns (uint256 collateralOut);

    // --- curator (owner) -----------------------------------------------------
    /// Lists a series or updates its listing. The series comes from `factory`, its knock-in,
    /// melt share and interval are the pricer's product, and the vol (with the spread's
    /// band) lies inside the pricer's certified vol range.
    function listSeries(
        address series,
        IPerpPricer pricer,
        uint16 volBpsAnnual,
        uint40 nextEarnings,
        uint128 capNotional
    ) external;
    function delistSeries(address series) external; // stops new buys; sells still allowed
    function setEarnings(address series, uint40 nextEarnings) external;
    function setSpread(address series, uint16 bidBps, uint16 askBps, uint16 volBandBps) external;
    function setRiskBudget(address feed, uint16 budgetBps) external;
    function setWeekend(address feed, IWeekendSource source, uint16 capBps, uint16 spreadBps) external;
    function setMinSecsToFixing(uint32 secs) external;
}
