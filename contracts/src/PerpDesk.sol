// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC4626} from "@openzeppelin/contracts/interfaces/IERC4626.sol";
import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {EnumerableSet} from "@openzeppelin/contracts/utils/structs/EnumerableSet.sol";
import {SafeCast} from "@openzeppelin/contracts/utils/math/SafeCast.sol";
import {IPerpDesk, IWeekendSource} from "./interfaces/IPerpDesk.sol";
import {IDeskQueue} from "./interfaces/IDeskQueue.sol";
import {IPerpQuoter, PerpQuote} from "./interfaces/IPerpQuoter.sol";
import {IPerpPricer, PerpProduct} from "./interfaces/IPerpPricer.sol";
import {IPerpFactory} from "./interfaces/IPerpFactory.sol";
import {IPerpSeries, PerpTerms, PerpState, PerpPhase} from "./interfaces/IPerpSeries.sol";
import {IAggregatorV3} from "./interfaces/IAggregatorV3.sol";

/// L3 of the v2 perpetual note: the market for both legs of a perpetual
/// series (IPerpDesk) and the LP redemption queue (IDeskQueue). The v1 Desk
/// carried over to a note that never settles: an ERC-4626 vault on USDG; LPs
/// deposit USDG; the Desk sells and buys back NOTE and WRITER ("cover")
/// around the quoter's price at two prices, and the spread stays in the
/// vault. Every trade is served from inventory first; for the shortfall the
/// Desk mints a pair and keeps the other leg, and a leg handed in is paired
/// with the other leg it holds and redeemed. The owner is the curator:
/// listings (which model, which vol, which earnings date, WRITER cap),
/// spreads, the risk budget and the weekend price per feed, and the band
/// before each fixing. Nothing here can touch series collateral.
///
/// Share price. totalAssets = idle USDG + the USDG the series have released
/// to the Desk's tokens and not yet collected + the Desk's NOTE and WRITER at
/// the quoter's price (mid, at the listing's vol, from the feed; WRITER =
/// pair value - NOTE). A closed series holds nothing but released USDG.
///
/// Deviations from ERC-4626 MUSTs, as in v1 (the share price is unknown while
/// a held series can't be quoted: weekends, a pending fixing, an earnings
/// date that has passed, a state the model refuses):
///  - totalAssets, convertToShares/Assets and the previews revert in that
///    state, with the quoter's or model's error.
///  - maxDeposit / maxMint / maxWithdraw / maxRedeem return 0 in that state.
///  - maxWithdraw / maxRedeem are also capped by idle USDG, and are 0 while
///    redemptions are queued (IDeskQueue).
/// Inflation: OpenZeppelin's virtual shares with a 6-decimal offset.
contract PerpDesk is IPerpDesk, IDeskQueue, ERC4626, Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;
    using EnumerableSet for EnumerableSet.AddressSet;

    uint256 internal constant BPS = 10_000;
    uint256 internal constant UNIT = 1e6; // base units per USDG of notional
    uint256 internal constant UNIT_PER_BPS = UNIT / BPS;
    uint256 internal constant RAY = 1e27; // notional per token

    uint16 public constant MAX_FEE_BPS = 200;
    uint16 public constant MAX_COVER_FEE_BPS = 1_000; // of the premium (MAX_FEE_BPS is of notional)
    uint16 public constant BACKSTOP_SHARE_BPS = 5_000; // half of every fee stays with the LPs
    uint256 public constant MAX_HELD_SERIES = 64; // bounds the totalAssets loop
    uint16 public constant MAX_SPREAD_BPS = 1_000;
    uint16 public constant MAX_WEEKEND_CAP_BPS = 1_000;
    uint256 public constant MIN_REQUEST_SHARES = 10e12; // 10 USDG at the first deposit's share price
    uint256 public constant QUEUE_BATCH = 8;

    /// The weekend price is allowed from Saturday 01:00 UTC to Monday 00:00
    /// UTC: inside the feeds' blind window (Friday 20:00 to Sunday 20:00 New
    /// York time) whether New York is on daylight time or not.
    uint256 internal constant WEEKEND_START_OFFSET = 1 hours;
    /// A feed counts as alive going into the weekend if its last round is at most this old
    /// when the window begins: it traded in Friday's last hours.
    uint256 internal constant WEEKEND_ALIVE_SECS = 4 hours;

    IPerpFactory public immutable factory;
    IPerpQuoter public immutable quoter;
    uint32 public minSecsToFixing;

    mapping(address => Listing) internal _listings;
    mapping(address => Spread) internal _spreads;
    mapping(address feed => Weekend) internal _weekends;
    mapping(address feed => uint16) public riskBudgetBps;
    address[] internal _listed;
    EnumerableSet.AddressSet internal _held;

    /// A queued redemption; `shares` is the unfilled rest (0 once filled or cancelled).
    struct Request {
        address owner;
        uint256 shares;
    }

    Request[] internal _queue;
    uint256 internal _queueHead;
    uint256 public queuedShares;
    uint256 public reservedAssets;
    mapping(address owner => uint256) public claimableAssets;

    error HeldSeriesLimit();
    error WrongAsset();

    constructor(IERC20 usdg, IPerpFactory factory_, IPerpQuoter quoter_, address owner_, uint32 minSecs)
        ERC20("Surrogate Pricer Perpetual Desk", "spPDESK")
        ERC4626(usdg)
        Ownable(owner_)
    {
        if (factory_.collateral() != address(usdg)) revert WrongAsset();
        factory = factory_;
        quoter = quoter_;
        minSecsToFixing = minSecs;
        emit MinSecsToFixingSet(minSecs);
    }

    // --- views ------------------------------------------------------------------

    function listing(address series) external view returns (Listing memory) {
        return _listings[series];
    }

    /// Every series ever listed, including delisted ones (sells stay open).
    function listedSeries() external view returns (address[] memory) {
        return _listed;
    }

    /// Series whose NOTE or WRITER the Desk holds, or held until the last `collect`.
    function heldSeries() external view returns (address[] memory) {
        return _held.values();
    }

    function spread(address series) external view returns (Spread memory) {
        return _spreads[series];
    }

    function weekend(address feed) external view returns (Weekend memory) {
        return _weekends[feed];
    }

    function spotOf(address series) external view returns (uint256 spot, bool weekendPrice) {
        return _spot(IPerpSeries(series).terms().feed);
    }

    /// @inheritdoc IPerpDesk
    function midPriceBps(address series) external view returns (uint16) {
        return _mid(series, true);
    }

    /// @inheritdoc IPerpDesk
    function navPriceBps(address series) external view returns (uint16) {
        return _mid(series, false);
    }

    /// @inheritdoc IPerpDesk
    function risk(address feed) public view returns (uint256 atRisk, uint256 limit) {
        uint256 assets = _idle();
        uint256 n = _held.length();
        for (uint256 i = 0; i < n; i++) {
            address series = _held.at(i);
            (, uint256 value, uint256 seriesRisk) = _position(series, Mark.Risk);
            assets += value;
            if (seriesRisk != 0 && IPerpSeries(series).terms().feed == feed) atRisk += seriesRisk;
        }
        limit = Math.mulDiv(assets, riskBudgetBps[feed], BPS);
    }

    /// @inheritdoc IPerpDesk
    function quote(address series, Side side, uint256 amount, uint16 feeBps)
        external
        view
        returns (uint256 paid, uint16 priceBps)
    {
        Trade memory t = _trade(series, amount, feeBps, address(0), address(0), side);
        if (side == Side.BuyNote || side == Side.BuyCover) paid = _buyCost(t);
        else (paid,) = _sellProceeds(t);
        return (paid, t.priceBps);
    }

    // --- trading ------------------------------------------------------------------

    function buy(address series, uint256 amount, uint256 maxCost, uint16 feeBps, address feeReceiver, address to)
        external
        nonReentrant
        returns (uint256 cost)
    {
        return _buy(_trade(series, amount, feeBps, feeReceiver, to, Side.BuyNote), maxCost);
    }

    function sell(address series, uint256 amount, uint256 minProceeds, uint16 feeBps, address feeReceiver, address to)
        external
        nonReentrant
        returns (uint256 proceeds)
    {
        return _sell(_trade(series, amount, feeBps, feeReceiver, to, Side.SellNote), minProceeds);
    }

    function buyCover(address series, uint256 amount, uint256 maxCost, uint16 feeBps, address feeReceiver, address to)
        external
        nonReentrant
        returns (uint256 cost)
    {
        return _buy(_trade(series, amount, feeBps, feeReceiver, to, Side.BuyCover), maxCost);
    }

    function sellCover(
        address series,
        uint256 amount,
        uint256 minProceeds,
        uint16 feeBps,
        address feeReceiver,
        address to
    ) external nonReentrant returns (uint256 proceeds) {
        return _sell(_trade(series, amount, feeBps, feeReceiver, to, Side.SellCover), minProceeds);
    }

    /// Permissionless: redeem the pairs the Desk holds, pull the USDG released to its
    /// tokens, and forget the series once nothing of it is left.
    function collect(address series) external nonReentrant returns (uint256 collateralOut) {
        if (!factory.isSeries(series)) revert NotFactorySeries(series);
        IPerpSeries s = IPerpSeries(series);
        uint256 n = _balance(s.note());
        uint256 w = _balance(s.writer());
        uint256 pairs = Math.min(n, w);
        if (pairs != 0) collateralOut = s.redeemPair(pairs, address(this));
        collateralOut += s.claim(address(this));
        if (n == w || s.state().phase == PerpPhase.Closed) _held.remove(series);
        emit Collected(series, collateralOut);
    }

    // --- redemption queue (IDeskQueue) ------------------------------------------------

    function queue() external view returns (uint256 head, uint256 length) {
        return (_queueHead, _queue.length);
    }

    function redeemRequest(uint256 id) external view returns (address owner_, uint256 shares) {
        Request memory r = _queue[id];
        return (r.owner, r.shares);
    }

    function requestRedeem(uint256 shares) external nonReentrant returns (uint256 id) {
        if (shares < MIN_REQUEST_SHARES) revert RequestTooSmall(shares, MIN_REQUEST_SHARES);
        _transfer(msg.sender, address(this), shares);
        id = _queue.length;
        _queue.push(Request({owner: msg.sender, shares: shares}));
        queuedShares += shares;
        emit RedeemRequested(id, msg.sender, shares);
    }

    function cancelRedeem(uint256 id) external nonReentrant returns (uint256 shares) {
        if (id >= _queue.length || _queue[id].owner != msg.sender) revert NotRequestOwner(id);
        shares = _queue[id].shares;
        _queue[id].shares = 0;
        queuedShares -= shares;
        _transfer(address(this), msg.sender, shares);
        emit RedeemCancelled(id, msg.sender, shares);
    }

    function processQueue(uint256 maxRequests)
        external
        nonReentrant
        returns (uint256 sharesFilled, uint256 assetsSetAside)
    {
        return _processQueue(maxRequests);
    }

    function claim(address to) external nonReentrant returns (uint256 assets) {
        assets = claimableAssets[msg.sender];
        if (assets == 0) revert NothingToClaim();
        claimableAssets[msg.sender] = 0;
        reservedAssets -= assets;
        IERC20(asset()).safeTransfer(to, assets);
        emit RedeemClaimed(msg.sender, to, assets);
    }

    // --- curator ------------------------------------------------------------------

    function listSeries(
        address series,
        IPerpPricer pricer,
        uint16 volBpsAnnual,
        uint40 nextEarnings,
        uint128 capNotional
    ) external onlyOwner {
        if (!factory.isSeries(series)) revert NotFactorySeries(series);
        PerpTerms memory t = IPerpSeries(series).terms();
        PerpProduct memory p = pricer.product();
        if (p.kiBarrierBps != t.kiBarrierBps) revert ModelMismatch(0);
        if (p.meltShare != t.meltShare) revert ModelMismatch(1);
        if (p.fixingInterval != t.fixingInterval) revert ModelMismatch(2);
        _checkVolBand(pricer, volBpsAnnual, _spreads[series].volBandBps);

        Listing storage l = _listings[series];
        if (address(l.pricer) == address(0)) _listed.push(series);
        l.active = true;
        l.pricer = pricer;
        l.volBpsAnnual = volBpsAnnual;
        l.nextEarnings = nextEarnings;
        l.capNotional = capNotional;
        emit SeriesListed(series, address(pricer), pricer.weightsHash(), volBpsAnnual, nextEarnings, capNotional);
    }

    function delistSeries(address series) external onlyOwner {
        Listing storage l = _listings[series];
        if (address(l.pricer) == address(0)) revert NotListed(series);
        l.active = false;
        emit SeriesDelisted(series);
    }

    function setEarnings(address series, uint40 nextEarnings) external onlyOwner {
        Listing storage l = _listings[series];
        if (address(l.pricer) == address(0)) revert NotListed(series);
        l.nextEarnings = nextEarnings;
        emit EarningsSet(series, nextEarnings);
    }

    function setSpread(address series, uint16 bidBps, uint16 askBps, uint16 volBandBps) external onlyOwner {
        Listing memory l = _listings[series];
        if (address(l.pricer) == address(0)) revert NotListed(series);
        if (bidBps > MAX_SPREAD_BPS) revert SpreadTooWide(bidBps);
        if (askBps > MAX_SPREAD_BPS) revert SpreadTooWide(askBps);
        _checkVolBand(l.pricer, l.volBpsAnnual, volBandBps);
        _spreads[series] = Spread({bidBps: bidBps, askBps: askBps, volBandBps: volBandBps});
        emit SpreadSet(series, bidBps, askBps, volBandBps);
    }

    function setRiskBudget(address feed, uint16 budgetBps) external onlyOwner {
        if (budgetBps > BPS) revert BudgetTooHigh(budgetBps);
        riskBudgetBps[feed] = budgetBps;
        emit RiskBudgetSet(feed, budgetBps);
    }

    function setWeekend(address feed, IWeekendSource source, uint16 capBps, uint16 spreadBps) external onlyOwner {
        if (capBps > MAX_WEEKEND_CAP_BPS) revert SpreadTooWide(capBps);
        if (spreadBps > MAX_SPREAD_BPS) revert SpreadTooWide(spreadBps);
        _weekends[feed] = Weekend({source: source, capBps: capBps, spreadBps: spreadBps});
        emit WeekendSet(feed, address(source), capBps, spreadBps);
    }

    function setMinSecsToFixing(uint32 secs) external onlyOwner {
        minSecsToFixing = secs;
        emit MinSecsToFixingSet(secs);
    }

    // --- ERC-4626 -----------------------------------------------------------------

    /// Reverts (with the quoter's or model's error) while a held series can't be quoted from its feed.
    function totalAssets() public view override(ERC4626, IERC4626) returns (uint256 assets) {
        assets = _idle();
        uint256 n = _held.length();
        for (uint256 i = 0; i < n; i++) {
            (, uint256 v,) = _position(_held.at(i), Mark.Strict);
            assets += v;
        }
    }

    function maxDeposit(address) public view override(ERC4626, IERC4626) returns (uint256) {
        return _allQuotable() ? type(uint256).max : 0;
    }

    function maxMint(address) public view override(ERC4626, IERC4626) returns (uint256) {
        return _allQuotable() ? type(uint256).max : 0;
    }

    function maxWithdraw(address owner_) public view override(ERC4626, IERC4626) returns (uint256) {
        if (queuedShares != 0 || !_allQuotable()) return 0;
        return Math.min(_convertToAssets(balanceOf(owner_), Math.Rounding.Floor), _idle());
    }

    function maxRedeem(address owner_) public view override(ERC4626, IERC4626) returns (uint256) {
        if (queuedShares != 0 || !_allQuotable()) return 0;
        return Math.min(balanceOf(owner_), _convertToShares(_idle(), Math.Rounding.Floor));
    }

    function deposit(uint256 assets, address receiver)
        public
        override(ERC4626, IERC4626)
        nonReentrant
        returns (uint256)
    {
        _advanceHeld();
        return super.deposit(assets, receiver);
    }

    function mint(uint256 shares, address receiver) public override(ERC4626, IERC4626) nonReentrant returns (uint256) {
        _advanceHeld();
        return super.mint(shares, receiver);
    }

    function withdraw(uint256 assets, address receiver, address owner_)
        public
        override(ERC4626, IERC4626)
        nonReentrant
        returns (uint256)
    {
        _advanceHeld();
        return super.withdraw(assets, receiver, owner_);
    }

    function redeem(uint256 shares, address receiver, address owner_)
        public
        override(ERC4626, IERC4626)
        nonReentrant
        returns (uint256)
    {
        _advanceHeld();
        return super.redeem(shares, receiver, owner_);
    }

    function _decimalsOffset() internal pure override returns (uint8) {
        return 6;
    }

    // --- internals ------------------------------------------------------------------

    /// Which price a position is marked at.
    enum Mark {
        Strict, // the live feed's spot; the quoter's revert bubbles (totalAssets)
        Probe, // the same, reporting failure instead of reverting (maxDeposit, maxWithdraw)
        Risk // the Desk's spot, the weekend price included; failure counts the most at risk
    }

    /// One trade's parameters and quote, kept in memory (the events are wide).
    struct Trade {
        address series;
        uint256 amount; // NOTE or WRITER tokens
        uint16 feeBps;
        address feeReceiver;
        address to;
        Side side;
        uint256 notionalPerToken;
        uint16 priceBps; // of the traded leg per unit of notional, spread included
        bytes32 weightsHash;
        uint256 fee;
        uint256 growth; // part of `amount` that becomes a new position of the Desk
        uint256 paid; // cost of a buy, proceeds of a sell
    }

    /// Price, fee and cap checks shared by quotes and trades. Buys need an
    /// active listing; sells only a listing (delisted series stay sellable).
    function _trade(address series, uint256 amount, uint16 feeBps, address feeReceiver, address to, Side side)
        internal
        view
        returns (Trade memory t)
    {
        if (amount == 0) revert ZeroAmount();
        bool isCover = side == Side.BuyCover || side == Side.SellCover;
        bool isBuy = side == Side.BuyNote || side == Side.BuyCover;
        if (feeBps > (isCover ? MAX_COVER_FEE_BPS : MAX_FEE_BPS)) revert FeeTooHigh(feeBps);
        Listing memory l = _listings[series];
        if (isBuy ? !l.active : address(l.pricer) == address(0)) revert NotListed(series);

        t.series = series;
        t.amount = amount;
        t.feeBps = feeBps;
        t.feeReceiver = feeReceiver;
        t.to = to;
        t.side = side;
        (t.priceBps, t.weightsHash) = _sidePrice(series, l, side);
        PerpState memory st = IPerpSeries(series).state(); // Live with no fixing pending: the quote succeeded
        if (uint256(st.nextFixing) < block.timestamp + minSecsToFixing) revert TooCloseToFixing(st.nextFixing);
        t.notionalPerToken = st.notionalPerToken;

        // the integrator fee is a share of the notional for NOTE, of the premium for cover
        uint256 feeBase = isCover
            ? _legValue(t, isBuy ? Math.Rounding.Ceil : Math.Rounding.Floor)
            : Math.mulDiv(amount, st.notionalPerToken, RAY);
        t.fee = Math.mulDiv(feeBase, feeBps, BPS, Math.Rounding.Ceil);
        t.growth = _growth(series, amount, side, l.capNotional, st.notionalPerToken);
    }

    /// The trader pays, the Desk mints pairs for what its inventory lacks and hands over the leg.
    function _buy(Trade memory t, uint256 maxCost) internal returns (uint256 cost) {
        IPerpSeries s = IPerpSeries(t.series);
        cost = t.paid = _buyCost(t);
        if (cost > maxCost) revert Slippage(cost, maxCost);
        if (t.growth != 0) _queueFirst();

        IERC20(asset()).safeTransferFrom(msg.sender, address(this), cost);
        if (t.growth != 0) {
            IERC20(asset()).forceApprove(t.series, s.previewMint(t.growth));
            // forge-lint: disable-next-line(unused-return)
            s.mint(t.growth, address(this)); // collateral = the previewMint approved above
            _hold(t.series);
        }
        IERC20(t.side == Side.BuyNote ? s.note() : s.writer()).safeTransfer(t.to, t.amount);
        _payFee(t.feeReceiver, t.fee);
        if (t.growth != 0) _checkRisk(t.series);
        _checkReserve();
        _emitTrade(t);
    }

    /// The trader hands the leg in, the Desk redeems the pairs it can form and pays.
    function _sell(Trade memory t, uint256 minProceeds) internal returns (uint256 proceeds) {
        IPerpSeries s = IPerpSeries(t.series);
        (proceeds, t.fee) = _sellProceeds(t);
        t.paid = proceeds;
        if (proceeds < minProceeds) revert Slippage(proceeds, minProceeds);
        if (t.growth != 0) _queueFirst();

        IERC20(t.side == Side.SellNote ? s.note() : s.writer()).safeTransferFrom(msg.sender, address(this), t.amount);
        uint256 pairs = t.amount - t.growth;
        // forge-lint: disable-next-line(unused-return)
        if (pairs != 0) s.redeemPair(pairs, address(this)); // USDG lands in idle
        if (t.growth != 0) _hold(t.series);
        IERC20(asset()).safeTransfer(t.to, proceeds);
        _payFee(t.feeReceiver, t.fee);
        if (t.growth != 0) _checkRisk(t.series);
        _checkReserve();
        _emitTrade(t);
    }

    function _emitTrade(Trade memory t) internal {
        emit Traded(
            t.series,
            msg.sender,
            t.side,
            t.to,
            t.amount,
            Math.mulDiv(t.amount, t.notionalPerToken, RAY),
            t.priceBps,
            t.paid,
            t.feeBps,
            t.feeReceiver,
            t.weightsHash
        );
    }

    /// amount * notionalPerToken * priceBps: what the traded leg is worth at the price applied.
    function _legValue(Trade memory t, Math.Rounding rounding) internal pure returns (uint256) {
        return Math.mulDiv(t.amount, t.notionalPerToken * t.priceBps, RAY * BPS, rounding);
    }

    /// ceil(value) + fee
    function _buyCost(Trade memory t) internal pure returns (uint256) {
        return _legValue(t, Math.Rounding.Ceil) + t.fee;
    }

    /// floor(value) - fee, and the fee can't exceed the gross amount
    /// (otherwise the integrator's cut would come out of the LPs).
    function _sellProceeds(Trade memory t) internal pure returns (uint256 proceeds, uint256 feeTaken) {
        uint256 gross = _legValue(t, Math.Rounding.Floor);
        feeTaken = Math.min(t.fee, gross);
        proceeds = gross - feeTaken;
    }

    // The three price helpers below revert on purpose, also when totalAssets loops over the
    // held series: the share price is unknown while one of them can't be marked.
    // forge-lint: disable-start(require-revert-in-loop)

    /// The feed's last price, and whether it is live: no older than the quoter's
    /// staleness limit, and, inside the weekend window, updated since the window
    /// began (a round from before it is Friday's close, however recent).
    function _feed(address feed)
        internal
        view
        returns (uint256 last, uint256 updatedAt, uint256 weekendStart, bool live)
    {
        int256 answer;
        // forge-lint: disable-next-line(unused-return)
        (, answer,, updatedAt,) = IAggregatorV3(feed).latestRoundData();
        if (answer <= 0) revert IPerpQuoter.BadFeedAnswer();
        // forge-lint: disable-next-line(unsafe-typecast)
        last = uint256(answer); // > 0, checked above
        weekendStart = _weekendStart(block.timestamp);
        live = updatedAt + quoter.MAX_FEED_STALENESS() >= block.timestamp
            && (weekendStart == 0 || updatedAt >= weekendStart);
    }

    /// The spot the Desk prices a feed's series at: the feed while it is live;
    /// over the weekend, for a feed that traded up to the weekend, the source's
    /// time-averaged price capped around the feed's last one.
    function _spot(address feed) internal view returns (uint256 spot, bool weekendPrice) {
        (uint256 last, uint256 updatedAt, uint256 start, bool live) = _feed(feed);
        if (live) return (last, false);
        Weekend memory w = _weekends[feed];
        if (address(w.source) == address(0) || start == 0 || updatedAt + WEEKEND_ALIVE_SECS < start) {
            // forge-lint: disable-next-line(unsafe-typecast)
            revert IPerpQuoter.FeedStale(uint40(updatedAt));
        }
        uint256 band = Math.mulDiv(last, w.capBps, BPS);
        spot = Math.max(Math.min(w.source.averagePrice(feed), last + band), last - band);
        if (spot == 0) revert IPerpQuoter.BadFeedAnswer();
        return (spot, true);
    }

    /// The NOTE mid at the listing's vol: at the Desk's spot (the weekend price
    /// included), or, for the share price, at the live feed only.
    function _mid(address series, bool weekendOk) internal view returns (uint16) {
        Listing memory l = _listings[series];
        address feed = IPerpSeries(series).terms().feed;
        uint256 spot;
        if (weekendOk) {
            (spot,) = _spot(feed);
        } else {
            uint256 updatedAt;
            bool live;
            (spot, updatedAt,, live) = _feed(feed);
            // forge-lint: disable-next-line(unsafe-typecast)
            if (!live) revert IPerpQuoter.FeedStale(uint40(updatedAt));
        }
        return quoter.quoteAtSpot(IPerpSeries(series), l.pricer, l.volBpsAnnual, l.nextEarnings, spot).priceBps;
    }

    // forge-lint: disable-end(require-revert-in-loop)

    /// Processes every processable fixing of the held series, so that a fixing which
    /// is past its fallback deadline is settled before the share price is used: once
    /// stored, a late recording can no longer change it. Every caller is nonReentrant,
    /// and a series' advance() calls only its recorder.
    function _advanceHeld() internal {
        uint256 n = _held.length();
        for (uint256 i = 0; i < n; i++) {
            // forge-lint: disable-next-line(unused-return, reentrancy-no-eth)
            IPerpSeries(_held.at(i)).advance();
        }
    }

    /// Start of the weekend window `t` lies in (Saturday 01:00 UTC), or 0 outside it.
    function _weekendStart(uint256 t) internal pure returns (uint256) {
        uint256 day = t / 1 days;
        uint256 weekday = (day + 4) % 7; // 1970-01-01 was a Thursday: 0 = Sunday .. 6 = Saturday
        // day * 1 days is midnight of that day: the truncation is the point
        // forge-lint: disable-start(divide-before-multiply)
        if (weekday == 6) {
            uint256 start = day * 1 days + WEEKEND_START_OFFSET;
            return t >= start ? start : 0;
        }
        if (weekday == 0) return (day - 1) * 1 days + WEEKEND_START_OFFSET;
        // forge-lint: disable-end(divide-before-multiply)
        return 0;
    }

    /// The quoter's lower or higher NOTE quote (at the two ends of the vol
    /// band) moved by the flat spread, against the trader; the cover price is
    /// what is left of a pair (IPerpDesk).
    function _sidePrice(address series, Listing memory l, Side side)
        internal
        view
        returns (uint16 priceBps, bytes32 weightsHash)
    {
        PerpTerms memory t = IPerpSeries(series).terms();
        Spread memory sp = _spreads[series];
        (uint256 spot, bool weekendPrice) = _spot(t.feed);
        uint256 flat = weekendPrice ? _weekends[t.feed].spreadBps : 0;
        uint256 lo;
        uint256 hi;
        (lo, weightsHash) = _noteBps(series, l, l.volBpsAnnual - sp.volBandBps, spot);
        if (sp.volBandBps == 0) {
            hi = lo;
        } else {
            (hi,) = _noteBps(series, l, l.volBpsAnnual + sp.volBandBps, spot);
            if (hi < lo) (lo, hi) = (hi, lo);
        }
        uint256 pairBps = (UNIT + t.couponReserve) / UNIT_PER_BPS;
        uint256 note;
        if (side == Side.BuyNote || side == Side.SellCover) {
            note = Math.min(hi + sp.askBps + flat, pairBps);
        } else {
            lo = Math.min(lo, pairBps);
            flat += sp.bidBps;
            note = lo > flat ? lo - flat : 0;
        }
        priceBps = SafeCast.toUint16(side == Side.BuyNote || side == Side.SellNote ? note : pairBps - note);
    }

    function _noteBps(address series, Listing memory l, uint16 vol, uint256 spot)
        internal
        view
        returns (uint256 priceBps, bytes32 weightsHash)
    {
        PerpQuote memory q = quoter.quoteAtSpot(IPerpSeries(series), l.pricer, vol, l.nextEarnings, spot);
        return (q.priceBps, q.weightsHash);
    }

    /// The part of a trade the Desk can't serve from, or pair with, what it
    /// holds. It becomes a new position: WRITER when the Desk sells NOTE or
    /// buys cover back (limited by the listing's cap, in notional), NOTE otherwise.
    function _growth(address series, uint256 amount, Side side, uint256 capNotional, uint256 notionalPerToken)
        internal
        view
        returns (uint256 growth)
    {
        IPerpSeries s = IPerpSeries(series);
        bool growsWriter = side == Side.BuyNote || side == Side.SellCover;
        growth = amount - Math.min(amount, _balance(growsWriter ? s.note() : s.writer()));
        if (growth == 0 || !growsWriter) return growth;
        uint256 held = _balance(s.writer());
        uint256 capTokens = notionalPerToken == 0 ? 0 : Math.mulDiv(capNotional, RAY, notionalPerToken);
        uint256 room = capTokens > held ? capTokens - held : 0;
        if (growth > room) revert CapExceeded(amount, amount - growth + room);
    }

    /// After a trade that added to a position: the feed's positions must fit its budget.
    function _checkRisk(address series) internal view {
        (uint256 atRisk, uint256 limit) = risk(IPerpSeries(series).terms().feed);
        if (atRisk > limit) revert RiskBudgetExceeded(atRisk, limit);
    }

    /// The listing's vol and both ends of its band must be vols the model is certified for.
    function _checkVolBand(IPerpPricer pricer, uint256 vol, uint256 band) internal view {
        if (band > vol || vol + band > type(uint16).max) revert ModelMismatch(3);
        (int64 lo, int64 hi) = pricer.certifiedRange(1);
        // vol and band are uint16 values: the sums fit int256
        // forge-lint: disable-next-line(unsafe-typecast)
        if (int256(vol - band) < lo || int256(vol + band) > hi) revert ModelMismatch(3);
    }

    /// The integrator keeps the fee minus the backstop slice (rounded up, in
    /// the LPs' favor); with no receiver the whole fee stays in the vault.
    function _payFee(address feeReceiver, uint256 fee) internal {
        if (fee == 0 || feeReceiver == address(0)) return;
        uint256 slice = Math.mulDiv(fee, BACKSTOP_SHARE_BPS, BPS, Math.Rounding.Ceil);
        if (fee > slice) IERC20(asset()).safeTransfer(feeReceiver, fee - slice);
    }

    function _hold(address series) internal {
        if (_held.add(series) && _held.length() > MAX_HELD_SERIES) revert HeldSeriesLimit();
    }

    /// USDG the vault can use: its balance minus what is set aside for filled redemptions.
    function _idle() internal view returns (uint256) {
        uint256 balance = IERC20(asset()).balanceOf(address(this));
        return balance > reservedAssets ? balance - reservedAssets : 0;
    }

    /// A trade paid out: it must not have dipped into the USDG set aside for claims.
    function _checkReserve() internal view {
        if (IERC20(asset()).balanceOf(address(this)) < reservedAssets) revert ReservedForClaims();
    }

    /// Before a trade adds to a position: queued redemptions are paid first, or the trade fails.
    function _queueFirst() internal {
        if (queuedShares == 0) return;
        // forge-lint: disable-next-line(unused-return)
        _processQueue(QUEUE_BATCH);
        if (queuedShares != 0) revert QueuePending(queuedShares);
    }

    /// Fills requests from the head at one share price (the vault's, now) out of
    /// idle USDG; the last one partially. Every visited request counts towards
    /// `maxRequests`, cancelled ones too, so the loop is bounded by the caller.
    function _processQueue(uint256 maxRequests) internal returns (uint256 sharesFilled, uint256 assetsSetAside) {
        uint256 head = _queueHead;
        uint256 len = _queue.length;
        if (queuedShares == 0) {
            _queueHead = len; // only cancelled requests are left
            return (0, 0);
        }
        _advanceHeld();
        // OpenZeppelin's convertToAssets, with totalAssets read once for the whole batch
        uint256 nav = totalAssets() + 1;
        uint256 supply = totalSupply() + 10 ** _decimalsOffset();
        uint256 idle = _idle();
        for (; head < len && maxRequests != 0; maxRequests--) {
            Request storage r = _queue[head];
            uint256 shares = r.shares;
            if (shares == 0) {
                head++;
                continue;
            }
            uint256 assets = Math.mulDiv(shares, nav, supply);
            if (assets > idle) {
                shares = Math.mulDiv(shares, idle, assets);
                assets = Math.mulDiv(shares, nav, supply);
            }
            if (shares == 0) break; // no idle USDG left
            r.shares -= shares;
            idle -= assets;
            claimableAssets[r.owner] += assets;
            sharesFilled += shares;
            assetsSetAside += assets;
            emit RedeemFilled(head, r.owner, shares, assets);
            if (r.shares != 0) break; // partly filled: the USDG ran out
            head++;
        }
        _queueHead = head;
        if (sharesFilled != 0) {
            queuedShares -= sharesFilled;
            reservedAssets += assetsSetAside;
            _burn(address(this), sharesFilled);
        }
    }

    function _balance(address token) internal view returns (uint256) {
        return IERC20(token).balanceOf(address(this));
    }

    function _allQuotable() internal view returns (bool) {
        uint256 n = _held.length();
        for (uint256 i = 0; i < n; i++) {
            (bool ok,,) = _position(_held.at(i), Mark.Probe);
            if (!ok) return false;
        }
        return true;
    }

    /// The Desk's NOTE + WRITER of one series and the USDG released to them:
    /// their value and what they can still lose (IPerpDesk, risk budget). An
    /// unquotable series returns ok = false with its least value and its most
    /// at risk, unless `mark` is Strict, which bubbles the quoter's revert.
    function _position(address series, Mark mark) internal view returns (bool ok, uint256 value, uint256 atRisk) {
        IPerpSeries s = IPerpSeries(series);
        uint256 cash = s.claimable(address(this));
        uint256 n = _balance(s.note());
        uint256 w = _balance(s.writer());
        if (n == 0 && w == 0) return (true, cash, 0);
        PerpState memory st = s.state();
        if (st.phase == PerpPhase.Closed) return (true, cash, 0); // everything was released
        uint256 reserve = s.terms().couponReserve;
        n = Math.mulDiv(n, st.notionalPerToken, RAY); // from here on: USDG of live notional
        w = Math.mulDiv(w, st.notionalPerToken, RAY);
        uint256 floorValue = Math.mulDiv(n, reserve, UNIT); // a NOTE is paid at least its coupon reserve

        uint256 noteBps = 0;
        if (mark == Mark.Strict) {
            noteBps = _mid(series, false);
        } else if (mark == Mark.Probe) {
            try this.navPriceBps(series) returns (uint16 p) {
                noteBps = p;
            } catch {
                return (false, cash + floorValue, n + w);
            }
        } else {
            try this.midPriceBps(series) returns (uint16 p) {
                noteBps = p;
            } catch {
                return (false, cash + floorValue, n + w); // either leg can lose at most 1 USDG per unit
            }
        }
        uint256 pairBps = (UNIT + reserve) / UNIT_PER_BPS;
        noteBps = Math.min(noteBps, pairBps);
        uint256 noteValue = Math.mulDiv(n, noteBps, BPS);
        uint256 writerValue = Math.mulDiv(w, pairBps - noteBps, BPS);
        atRisk = writerValue + noteValue - Math.min(noteValue, floorValue);
        return (true, cash + noteValue + writerValue, atRisk);
    }
}
