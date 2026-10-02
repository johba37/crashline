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
import {IDeskCover} from "./interfaces/IDeskCover.sol";
import {IDeskQueue} from "./interfaces/IDeskQueue.sol";
import {INoteQuoter} from "./interfaces/INoteQuoter.sol";
import {
    ISurrogatePricer,
    FIELD_VOL,
    FIELD_KI_BARRIER,
    FIELD_AC_BARRIER,
    FIELD_COUPON,
    FIELD_OBSERVATIONS_REMAINING
} from "./interfaces/ISurrogatePricer.sol";
import {ISeriesFactory} from "./interfaces/ISeriesFactory.sol";
import {INoteSeries, SeriesTerms, SeriesState, Phase} from "./interfaces/INoteSeries.sol";
import {AutocallPayout} from "./AutocallPayout.sol";

/// L3: the market for both legs of a series and the pricer's only in-path use
/// (IDesk, IDeskCover). An ERC-4626 vault on USDG: LPs deposit USDG; the Desk
/// sells and buys back NOTE and WRITER ("cover") around the model's quote, at
/// two prices, and the spread stays in the vault. Every trade is served from
/// inventory first; for the shortfall the Desk mints a pair and keeps the
/// other leg, and a leg handed in is paired with the other leg it holds and
/// redeemed. So a cover buyer leaves the Desk with NOTE (the side a USDG vault
/// is paid to hold), and a NOTE buyer beyond the inventory leaves it with
/// WRITER, up to the listing's cap. The owner is the curator: listings (which
/// model, which vol, WRITER cap), spreads, the risk budget per feed and the
/// pre-observation band. Nothing here can touch series collateral.
///
/// Share price. totalAssets = idle USDG + the Desk's NOTE and WRITER at the
/// model's quote (mid, no fee; WRITER = maxPayout - NOTE). Exact without the
/// model where the payout is already certain: after settlement, and in the
/// final period of a note that isn't knocked in (NOTE = maxPayout).
///
/// Deviations from ERC-4626 MUSTs (the reason: share price is unknown while a
/// held series can't be quoted, e.g. weekends, a pending fixing, the autocall
/// observation-day band, the final period of a knocked-in note):
///  - totalAssets, convertToShares/Assets and the previews revert while any
///    held series can't be quoted, with the quoter's or model's error.
///  - maxDeposit / maxMint / maxWithdraw / maxRedeem return 0 in that state
///    (they never revert), so deposit/withdraw revert with ERC4626Exceeded*.
///  - maxWithdraw / maxRedeem are also capped by idle USDG: collateral locked
///    in series comes back only through sells, redeemPair or collect.
///  - maxWithdraw / maxRedeem are 0 while redemptions are queued (IDeskQueue):
///    a request can be made in every one of these states and is paid first.
/// Inflation: OpenZeppelin's virtual shares with a 6-decimal offset, so
/// shares have 12 decimals and a donation can't round a depositor to zero.
contract Desk is IDeskCover, IDeskQueue, ERC4626, Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;
    using EnumerableSet for EnumerableSet.AddressSet;

    /// A queued redemption; `shares` is the unfilled rest (0 once filled or cancelled).
    struct Request {
        address owner;
        uint256 shares;
    }

    /// Which leg the trader buys or sells.
    enum Side {
        BuyNote,
        SellNote,
        BuyCover,
        SellCover
    }

    /// One trade's parameters and quote, kept in memory (the events are wide).
    struct Trade {
        address series;
        uint256 legAmount; // NOTE or WRITER
        uint16 feeBps;
        address feeReceiver;
        address to;
        Side side;
        uint16 priceBps; // of the traded leg, spread included
        bytes32 weightsHash;
        uint256 fee;
        uint256 growth; // part of legAmount that becomes a new position of the Desk
        uint256 amount; // cost of a buy, proceeds of a sell
    }

    // The payout rule's units, under short local names.
    uint256 internal constant BPS = AutocallPayout.BPS;
    uint256 internal constant UNIT = AutocallPayout.UNIT; // base units per NOTE
    uint256 internal constant UNIT_PER_BPS = AutocallPayout.UNIT_PER_BPS;

    uint16 public constant MAX_FEE_BPS = 200;
    uint16 public constant MAX_COVER_FEE_BPS = 1_000; // of the premium (MAX_FEE_BPS is of notional)
    uint16 public constant BACKSTOP_SHARE_BPS = 5_000; // half of every fee stays with the LPs
    uint256 public constant MAX_HELD_SERIES = 64; // bounds the totalAssets loop
    uint16 public constant MAX_SPREAD_BPS = 1_000;
    uint256 public constant MIN_REQUEST_SHARES = 10e12; // 10 USDG at the first deposit's share price
    uint256 public constant QUEUE_BATCH = 8;

    ISeriesFactory public immutable factory;
    INoteQuoter public immutable quoter;
    uint32 public minSecsToObservation;

    mapping(address => Listing) internal _listings; // soldNotional is never written: listing() computes it
    mapping(address => Spread) internal _spreads;
    mapping(address feed => uint16) public riskBudgetBps;
    address[] internal _listed;
    EnumerableSet.AddressSet internal _held;

    Request[] internal _queue;
    uint256 internal _queueHead;
    uint256 public queuedShares;
    uint256 public reservedAssets;
    mapping(address owner => uint256) public claimableAssets;

    constructor(IERC20 usdg, ISeriesFactory factory_, INoteQuoter quoter_, address owner_, uint32 minSecs)
        ERC20("Surrogate Pricer Desk", "spDESK")
        ERC4626(usdg)
        Ownable(owner_)
    {
        if (factory_.collateral() != address(usdg)) revert WrongAsset();
        factory = factory_;
        quoter = quoter_;
        minSecsToObservation = minSecs;
        emit MinSecsToObservationSet(minSecs);
    }

    // --- views ------------------------------------------------------------------

    /// `soldNotional` is not stored: it is read here from the Desk's WRITER balance,
    /// the WRITER the Desk holds now, which `capNotional` limits.
    function listing(address series) external view returns (Listing memory l) {
        l = _listings[series];
        if (address(l.pricer) != address(0)) {
            // clamped to the field's uint128; a larger balance can't be reached with USDG
            // forge-lint: disable-next-line(unsafe-typecast)
            l.soldNotional = uint128(Math.min(_balance(INoteSeries(series).writer()), type(uint128).max));
        }
    }

    /// Every series ever listed, including delisted ones (sells stay open).
    function listedSeries() external view returns (address[] memory) {
        return _listed;
    }

    /// @inheritdoc IDeskCover
    function heldSeries() external view returns (address[] memory) {
        return _held.values();
    }

    function spread(address series) external view returns (Spread memory) {
        return _spreads[series];
    }

    /// @inheritdoc IDeskCover
    function risk(address feed) public view returns (uint256 atRisk, uint256 limit) {
        uint256 assets = _idle();
        uint256 n = _held.length();
        for (uint256 i = 0; i < n; i++) {
            address series = _held.at(i);
            (, uint256 value, uint256 seriesRisk) = _position(series, false);
            assets += value;
            if (seriesRisk != 0 && INoteSeries(series).terms().feed == feed) atRisk += seriesRisk;
        }
        limit = Math.mulDiv(assets, riskBudgetBps[feed], BPS);
    }

    function quoteBuy(address series, uint256 noteAmount, uint16 feeBps)
        external
        view
        returns (uint256 cost, uint16 priceBps)
    {
        return _quoteCost(series, noteAmount, feeBps, Side.BuyNote);
    }

    function quoteSell(address series, uint256 noteAmount, uint16 feeBps)
        external
        view
        returns (uint256 proceeds, uint16 priceBps)
    {
        return _quoteProceeds(series, noteAmount, feeBps, Side.SellNote);
    }

    function quoteBuyCover(address series, uint256 writerAmount, uint16 feeBps)
        external
        view
        returns (uint256 cost, uint16 priceBps)
    {
        return _quoteCost(series, writerAmount, feeBps, Side.BuyCover);
    }

    function quoteSellCover(address series, uint256 writerAmount, uint16 feeBps)
        external
        view
        returns (uint256 proceeds, uint16 priceBps)
    {
        return _quoteProceeds(series, writerAmount, feeBps, Side.SellCover);
    }

    // --- trading ------------------------------------------------------------------

    function buy(address series, uint256 noteAmount, uint256 maxCost, uint16 feeBps, address feeReceiver, address to)
        external
        nonReentrant
        returns (uint256 cost)
    {
        return _buy(_prepare(series, noteAmount, feeBps, feeReceiver, to, Side.BuyNote), maxCost);
    }

    function sell(
        address series,
        uint256 noteAmount,
        uint256 minProceeds,
        uint16 feeBps,
        address feeReceiver,
        address to
    ) external nonReentrant returns (uint256 proceeds) {
        return _sell(_prepare(series, noteAmount, feeBps, feeReceiver, to, Side.SellNote), minProceeds);
    }

    function buyCover(
        address series,
        uint256 writerAmount,
        uint256 maxCost,
        uint16 feeBps,
        address feeReceiver,
        address to
    ) external nonReentrant returns (uint256 cost) {
        return _buy(_prepare(series, writerAmount, feeBps, feeReceiver, to, Side.BuyCover), maxCost);
    }

    function sellCover(
        address series,
        uint256 writerAmount,
        uint256 minProceeds,
        uint16 feeBps,
        address feeReceiver,
        address to
    ) external nonReentrant returns (uint256 proceeds) {
        return _sell(_prepare(series, writerAmount, feeBps, feeReceiver, to, Side.SellCover), minProceeds);
    }

    /// Permissionless: after settlement, turn the Desk's NOTE and WRITER into USDG.
    function collect(address series) external nonReentrant returns (uint256 collateralOut) {
        if (!factory.isSeries(series)) revert NotFactorySeries(series);
        INoteSeries s = INoteSeries(series);
        uint256 n = _balance(s.note());
        uint256 w = _balance(s.writer());
        if (n != 0 || w != 0) collateralOut = s.redeem(n, w, address(this));
        _held.remove(series);
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

    function listSeries(address series, ISurrogatePricer pricer, uint16 volBpsAnnual, uint128 capNotional)
        external
        onlyOwner
    {
        if (!factory.isSeries(series)) revert NotFactorySeries(series);
        SeriesTerms memory t = INoteSeries(series).terms();
        _checkVolBand(pricer, volBpsAnnual, _spreads[series].volBandBps);
        _checkRange(pricer, FIELD_KI_BARRIER, t.kiBarrierBps);
        _checkRange(pricer, FIELD_AC_BARRIER, t.acBarrierBps);
        _checkRange(pricer, FIELD_COUPON, t.couponBpsPerPeriod);
        _checkRange(pricer, FIELD_OBSERVATIONS_REMAINING, t.observationCount);

        Listing storage l = _listings[series];
        if (address(l.pricer) == address(0)) _listed.push(series);
        l.active = true;
        l.pricer = pricer;
        l.volBpsAnnual = volBpsAnnual;
        l.capNotional = capNotional;
        emit SeriesListed(series, address(pricer), pricer.weightsHash(), volBpsAnnual, capNotional);
    }

    function delistSeries(address series) external onlyOwner {
        Listing storage l = _listings[series];
        if (address(l.pricer) == address(0)) revert NotListed(series);
        l.active = false;
        emit SeriesDelisted(series);
    }

    function setMinSecsToObservation(uint32 secs) external onlyOwner {
        minSecsToObservation = secs;
        emit MinSecsToObservationSet(secs);
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

    // --- ERC-4626 -----------------------------------------------------------------

    /// Reverts (with the quoter's or model's error) while a held series can't be quoted.
    function totalAssets() public view override(ERC4626, IERC4626) returns (uint256 assets) {
        assets = _idle();
        uint256 n = _held.length();
        for (uint256 i = 0; i < n; i++) {
            (, uint256 v,) = _position(_held.at(i), true);
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
        return super.deposit(assets, receiver);
    }

    function mint(uint256 shares, address receiver) public override(ERC4626, IERC4626) nonReentrant returns (uint256) {
        return super.mint(shares, receiver);
    }

    function withdraw(uint256 assets, address receiver, address owner_)
        public
        override(ERC4626, IERC4626)
        nonReentrant
        returns (uint256)
    {
        return super.withdraw(assets, receiver, owner_);
    }

    function redeem(uint256 shares, address receiver, address owner_)
        public
        override(ERC4626, IERC4626)
        nonReentrant
        returns (uint256)
    {
        return super.redeem(shares, receiver, owner_);
    }

    function _decimalsOffset() internal pure override returns (uint8) {
        return 6;
    }

    // --- internals ------------------------------------------------------------------

    function _prepare(address series, uint256 legAmount, uint16 feeBps, address feeReceiver, address to, Side side)
        internal
        view
        returns (Trade memory t)
    {
        (t.priceBps, t.weightsHash, t.fee, t.growth) = _quote(series, legAmount, feeBps, side);
        t.series = series;
        t.legAmount = legAmount;
        t.feeBps = feeBps;
        t.feeReceiver = feeReceiver;
        t.to = to;
        t.side = side;
    }

    /// The trader pays, the Desk mints pairs for what its inventory lacks and hands over the leg.
    function _buy(Trade memory t, uint256 maxCost) internal returns (uint256 cost) {
        INoteSeries s = INoteSeries(t.series);
        cost = t.amount = _buyCost(t.legAmount, t.priceBps, t.fee);
        if (cost > maxCost) revert Slippage(cost, maxCost);
        if (t.growth != 0) _queueFirst();

        IERC20(asset()).safeTransferFrom(msg.sender, address(this), cost);
        if (t.growth != 0) {
            IERC20(asset()).forceApprove(t.series, s.previewMint(t.growth));
            // forge-lint: disable-next-line(unused-return)
            s.mint(t.growth, address(this)); // collateral = the previewMint approved above
            _hold(t.series);
        }
        IERC20(_isCover(t.side) ? s.writer() : s.note()).safeTransfer(t.to, t.legAmount);
        _payFee(t.feeReceiver, t.fee);
        if (t.growth != 0) _checkRisk(t.series);
        _checkReserve();
        _emitTrade(t);
    }

    /// The trader hands the leg in, the Desk redeems the pairs it can form and pays.
    function _sell(Trade memory t, uint256 minProceeds) internal returns (uint256 proceeds) {
        INoteSeries s = INoteSeries(t.series);
        (proceeds, t.fee) = _sellProceeds(t.legAmount, t.priceBps, t.fee);
        t.amount = proceeds;
        if (proceeds < minProceeds) revert Slippage(proceeds, minProceeds);
        if (t.growth != 0) _queueFirst();

        IERC20(_isCover(t.side) ? s.writer() : s.note()).safeTransferFrom(msg.sender, address(this), t.legAmount);
        uint256 pairs = t.legAmount - t.growth;
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
        if (t.side == Side.BuyNote) {
            emit NoteBought(
                t.series, msg.sender, t.to, t.legAmount, t.priceBps, t.amount, t.feeBps, t.feeReceiver, t.weightsHash
            );
        } else if (t.side == Side.SellNote) {
            emit NoteSold(
                t.series, msg.sender, t.to, t.legAmount, t.priceBps, t.amount, t.feeBps, t.feeReceiver, t.weightsHash
            );
        } else if (t.side == Side.BuyCover) {
            emit CoverBought(
                t.series, msg.sender, t.to, t.legAmount, t.priceBps, t.amount, t.feeBps, t.feeReceiver, t.weightsHash
            );
        } else {
            emit CoverSold(
                t.series, msg.sender, t.to, t.legAmount, t.priceBps, t.amount, t.feeBps, t.feeReceiver, t.weightsHash
            );
        }
    }

    function _isBuy(Side side) internal pure returns (bool) {
        return side == Side.BuyNote || side == Side.BuyCover;
    }

    function _isCover(Side side) internal pure returns (bool) {
        return side == Side.BuyCover || side == Side.SellCover;
    }

    /// The Desk sells NOTE, outright (BuyNote) or by buying cover back (SellCover):
    /// it ends up with WRITER and prices off the NOTE ask.
    function _deskSellsNote(Side side) internal pure returns (bool) {
        return side == Side.BuyNote || side == Side.SellCover;
    }

    function _quoteCost(address series, uint256 legAmount, uint16 feeBps, Side side)
        internal
        view
        returns (uint256 cost, uint16 priceBps)
    {
        uint256 fee;
        (priceBps,, fee,) = _quote(series, legAmount, feeBps, side);
        cost = _buyCost(legAmount, priceBps, fee);
    }

    function _quoteProceeds(address series, uint256 legAmount, uint16 feeBps, Side side)
        internal
        view
        returns (uint256 proceeds, uint16 priceBps)
    {
        uint256 fee;
        (priceBps,, fee,) = _quote(series, legAmount, feeBps, side);
        (proceeds,) = _sellProceeds(legAmount, priceBps, fee);
    }

    /// Price, fee and cap checks shared by quotes and trades. Buys need an
    /// active listing; sells only a listing (delisted series stay sellable).
    function _quote(address series, uint256 legAmount, uint16 feeBps, Side side)
        internal
        view
        returns (uint16 priceBps, bytes32 weightsHash, uint256 fee, uint256 growth)
    {
        if (legAmount == 0) revert INoteSeries.ZeroAmount();
        if (feeBps > (_isCover(side) ? MAX_COVER_FEE_BPS : MAX_FEE_BPS)) revert FeeTooHigh(feeBps);
        Listing memory l = _listings[series];
        if (_isBuy(side) ? !l.active : address(l.pricer) == address(0)) revert NotListed(series);
        (priceBps, weightsHash) = _sidePrice(series, l, side);
        uint40 next = INoteSeries(series).state().nextObservation;
        if (uint256(next) < block.timestamp + minSecsToObservation) revert TooCloseToObservation(next);
        // the integrator fee is a share of the notional for NOTE, of the premium for cover
        uint256 feeBase = legAmount;
        if (_isCover(side)) {
            feeBase = Math.mulDiv(legAmount, priceBps, BPS, _isBuy(side) ? Math.Rounding.Ceil : Math.Rounding.Floor);
        }
        fee = Math.mulDiv(feeBase, feeBps, BPS, Math.Rounding.Ceil);
        growth = _growth(series, legAmount, side, l.capNotional);
    }

    /// The model's lower or higher NOTE quote (at the two ends of the vol
    /// band) moved by the flat spread, against the trader; the cover price is
    /// what is left of a pair (IDeskCover).
    function _sidePrice(address series, Listing memory l, Side side)
        internal
        view
        returns (uint16 priceBps, bytes32 weightsHash)
    {
        Spread memory sp = _spreads[series];
        uint256 lo;
        uint256 hi;
        (lo, weightsHash) = quoter.notePriceBps(INoteSeries(series), l.pricer, l.volBpsAnnual - sp.volBandBps);
        if (sp.volBandBps == 0) {
            hi = lo;
        } else {
            // forge-lint: disable-next-line(unused-return)
            (hi,) = quoter.notePriceBps(INoteSeries(series), l.pricer, l.volBpsAnnual + sp.volBandBps);
            if (hi < lo) (lo, hi) = (hi, lo);
        }
        uint256 maxBps = INoteSeries(series).maxPayoutPerNote() / UNIT_PER_BPS;
        uint256 note;
        if (_deskSellsNote(side)) {
            note = Math.min(hi + sp.askBps, maxBps);
        } else {
            lo = Math.min(lo, maxBps);
            note = lo > sp.bidBps ? lo - sp.bidBps : 0;
        }
        priceBps = SafeCast.toUint16(_isCover(side) ? maxBps - note : note);
    }

    /// The part of a trade the Desk can't serve from, or pair with, what it
    /// holds. It becomes a new position: WRITER when the Desk sells NOTE or
    /// buys cover back (limited by the listing's cap), NOTE otherwise.
    function _growth(address series, uint256 legAmount, Side side, uint256 cap) internal view returns (uint256 growth) {
        INoteSeries s = INoteSeries(series);
        bool growsWriter = _deskSellsNote(side);
        growth = legAmount - Math.min(legAmount, _balance(growsWriter ? s.note() : s.writer()));
        if (growth == 0 || !growsWriter) return growth;
        uint256 held = _balance(s.writer());
        uint256 room = cap > held ? cap - held : 0;
        if (growth > room) revert CapExceeded(legAmount, legAmount - growth + room);
    }

    /// After a trade that added to a position: the feed's positions must fit its budget.
    function _checkRisk(address series) internal view {
        (uint256 atRisk, uint256 limit) = risk(INoteSeries(series).terms().feed);
        if (atRisk > limit) revert RiskBudgetExceeded(atRisk, limit);
    }

    /// ceil(n * price) + fee
    function _buyCost(uint256 legAmount, uint16 priceBps, uint256 fee) internal pure returns (uint256) {
        return Math.mulDiv(legAmount, priceBps, BPS, Math.Rounding.Ceil) + fee;
    }

    /// floor(n * price) - fee, and the fee can't exceed the gross amount
    /// (otherwise the integrator's cut would come out of the LPs).
    function _sellProceeds(uint256 legAmount, uint16 priceBps, uint256 fee)
        internal
        pure
        returns (uint256 proceeds, uint256 feeTaken)
    {
        uint256 gross = Math.mulDiv(legAmount, priceBps, BPS);
        feeTaken = Math.min(fee, gross);
        proceeds = gross - feeTaken;
    }

    /// The listing's vol and both ends of its band must be vols the model is certified for.
    function _checkVolBand(ISurrogatePricer pricer, uint256 vol, uint256 band) internal view {
        if (band > vol || vol + band > type(uint16).max) revert ModelMismatch(FIELD_VOL);
        _checkRange(pricer, FIELD_VOL, vol - band);
        _checkRange(pricer, FIELD_VOL, vol + band);
    }

    function _checkRange(ISurrogatePricer pricer, uint8 field, uint256 value) internal view {
        (int64 lo, int64 hi) = pricer.certifiedRange(field);
        // value is a uint8/uint16 term: fits int256
        // forge-lint: disable-next-line(unsafe-typecast)
        if (int256(value) < lo || int256(value) > hi) revert ModelMismatch(field);
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
        uint256 balance = _balance(asset());
        return balance > reservedAssets ? balance - reservedAssets : 0;
    }

    /// A trade paid out: it must not have dipped into the USDG set aside for claims.
    function _checkReserve() internal view {
        if (_balance(asset()) < reservedAssets) revert ReservedForClaims();
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
            (bool ok,,) = _position(_held.at(i), false);
            if (!ok) return false;
        }
        return true;
    }

    /// The Desk's NOTE + WRITER of one series: their value at the model's mid
    /// and what they can still lose (IDeskCover, risk budget). `strict` bubbles
    /// the quoter's revert; otherwise an unquotable series returns ok = false
    /// with its least value and its most at risk.
    function _position(address series, bool strict) internal view returns (bool ok, uint256 value, uint256 atRisk) {
        INoteSeries s = INoteSeries(series);
        uint256 n = _balance(s.note());
        uint256 w = _balance(s.writer());
        if (n == 0 && w == 0) return (true, 0, 0);
        uint256 maxPayout = s.maxPayoutPerNote();
        uint256 floorValue = Math.mulDiv(n, maxPayout - UNIT, UNIT); // an unsettled NOTE pays at least its coupons
        SeriesState memory st = s.state();

        uint256 notePerUnit; // NOTE value in base units per 1e6 NOTE units
        bool certain = true;
        if (st.phase == Phase.Settled) {
            notePerUnit = st.payoutPerNote;
        } else if (st.phase == Phase.Live && !st.knockedIn && st.observationsDone == s.terms().observationCount) {
            notePerUnit = maxPayout; // final period, not knocked in: the payout is certain
        } else {
            certain = false;
            Listing memory l = _listings[series];
            uint16 priceBps = 0;
            if (strict) {
                // forge-lint: disable-next-line(unused-return)
                (priceBps,) = quoter.notePriceBps(s, l.pricer, l.volBpsAnnual);
            } else {
                try quoter.notePriceBps(s, l.pricer, l.volBpsAnnual) returns (uint16 p, bytes32) {
                    priceBps = p;
                } catch {
                    return (false, floorValue, n + w); // either leg can lose at most 1 USDG per unit
                }
            }
            notePerUnit = Math.min(uint256(priceBps) * UNIT_PER_BPS, maxPayout);
        }
        uint256 noteValue = Math.mulDiv(n, notePerUnit, UNIT);
        uint256 writerValue = Math.mulDiv(w, maxPayout - notePerUnit, UNIT);
        if (!certain) atRisk = writerValue + noteValue - Math.min(noteValue, floorValue);
        return (true, noteValue + writerValue, atRisk);
    }
}
