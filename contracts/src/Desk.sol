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
import {IDesk} from "./interfaces/IDesk.sol";
import {INoteQuoter} from "./interfaces/INoteQuoter.sol";
import {ISurrogatePricer} from "./interfaces/ISurrogatePricer.sol";
import {ISeriesFactory} from "./interfaces/ISeriesFactory.sol";
import {INoteSeries, SeriesTerms, SeriesState, Phase} from "./interfaces/INoteSeries.sol";

/// L3: the market for NOTE and the pricer's only in-path use (IDesk). An
/// ERC-4626 vault on USDG: LPs deposit USDG; on a buy the Desk sells NOTE from
/// inventory or mints NOTE+WRITER pairs and keeps the WRITER; on a sell it
/// buys NOTE back at the model's quote and unwinds pairs against the WRITER it
/// holds. The owner is the curator: listings (which model, which vol, cap)
/// and the pre-observation band. Nothing here can touch series collateral.
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
/// Inflation: OpenZeppelin's virtual shares with a 6-decimal offset, so
/// shares have 12 decimals and a donation can't round a depositor to zero.
contract Desk is IDesk, ERC4626, Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;
    using EnumerableSet for EnumerableSet.AddressSet;

    uint256 internal constant BPS = 10_000;
    uint256 internal constant UNIT = 1e6; // base units per NOTE
    uint256 internal constant UNIT_PER_BPS = UNIT / BPS;

    uint16 public constant MAX_FEE_BPS = 200;
    uint16 public constant BACKSTOP_SHARE_BPS = 2_000; // 20% of every fee stays with the LPs
    uint256 public constant MAX_HELD_SERIES = 64; // bounds the totalAssets loop

    ISeriesFactory public immutable factory;
    INoteQuoter public immutable quoter;
    uint32 public minSecsToObservation;

    mapping(address => Listing) internal _listings;
    address[] internal _listed;
    EnumerableSet.AddressSet internal _held;

    event MinSecsToObservationSet(uint32 secs);

    error HeldSeriesLimit();
    error WrongAsset();

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

    function listing(address series) external view returns (Listing memory) {
        return _listings[series];
    }

    /// Every series ever listed, including delisted ones (sells stay open).
    function listedSeries() external view returns (address[] memory) {
        return _listed;
    }

    /// Series whose NOTE or WRITER the Desk holds (valued in totalAssets).
    function heldSeries() external view returns (address[] memory) {
        return _held.values();
    }

    function quoteBuy(address series, uint256 noteAmount, uint16 feeBps)
        external
        view
        returns (uint256 cost, uint16 priceBps)
    {
        uint256 fee;
        (priceBps,, fee) = _quote(series, noteAmount, feeBps, true);
        _checkCap(series, noteAmount);
        cost = Math.mulDiv(noteAmount, priceBps, BPS, Math.Rounding.Ceil) + fee;
    }

    function quoteSell(address series, uint256 noteAmount, uint16 feeBps)
        external
        view
        returns (uint256 proceeds, uint16 priceBps)
    {
        uint256 fee;
        (priceBps,, fee) = _quote(series, noteAmount, feeBps, false);
        (proceeds,) = _sellProceeds(noteAmount, priceBps, fee);
    }

    // --- trading ------------------------------------------------------------------

    function buy(address series, uint256 noteAmount, uint256 maxCost, uint16 feeBps, address feeReceiver, address to)
        external
        nonReentrant
        returns (uint256 cost)
    {
        Trade memory t = _trade(series, noteAmount, feeBps, feeReceiver, to, true);
        _checkCap(series, noteAmount);
        cost = t.amount = Math.mulDiv(noteAmount, t.priceBps, BPS, Math.Rounding.Ceil) + t.fee;
        if (cost > maxCost) revert Slippage(cost, maxCost);
        _listings[series].soldNotional += uint128(noteAmount);

        IERC20(asset()).safeTransferFrom(msg.sender, address(this), cost);
        IERC20 note = IERC20(INoteSeries(series).note());
        uint256 inventory = note.balanceOf(address(this));
        if (inventory < noteAmount) {
            uint256 toMint = noteAmount - inventory;
            IERC20(asset()).forceApprove(series, INoteSeries(series).previewMint(toMint));
            INoteSeries(series).mint(toMint, address(this));
            _hold(series);
        }
        note.safeTransfer(to, noteAmount);
        _payFee(feeReceiver, t.fee);
        _emitTrade(t, true);
    }

    function sell(
        address series,
        uint256 noteAmount,
        uint256 minProceeds,
        uint16 feeBps,
        address feeReceiver,
        address to
    ) external nonReentrant returns (uint256 proceeds) {
        Trade memory t = _trade(series, noteAmount, feeBps, feeReceiver, to, false);
        (proceeds, t.fee) = _sellProceeds(noteAmount, t.priceBps, t.fee);
        t.amount = proceeds;
        if (proceeds < minProceeds) revert Slippage(proceeds, minProceeds);
        Listing storage l = _listings[series];
        l.soldNotional -= uint128(Math.min(noteAmount, l.soldNotional));

        IERC20(INoteSeries(series).note()).safeTransferFrom(msg.sender, address(this), noteAmount);
        uint256 pairs = Math.min(noteAmount, IERC20(INoteSeries(series).writer()).balanceOf(address(this)));
        if (pairs != 0) INoteSeries(series).redeemPair(pairs, address(this));
        if (pairs < noteAmount) _hold(series); // NOTE inventory from a hedger's pair
        IERC20(asset()).safeTransfer(to, proceeds);
        _payFee(feeReceiver, t.fee);
        _emitTrade(t, false);
    }

    /// Permissionless: after settlement, turn the Desk's NOTE and WRITER into USDG.
    function collect(address series) external nonReentrant returns (uint256 collateralOut) {
        if (!factory.isSeries(series)) revert NotFactorySeries(series);
        uint256 n = IERC20(INoteSeries(series).note()).balanceOf(address(this));
        uint256 w = IERC20(INoteSeries(series).writer()).balanceOf(address(this));
        if (n != 0 || w != 0) collateralOut = INoteSeries(series).redeem(n, w, address(this));
        _held.remove(series);
        emit Collected(series, collateralOut);
    }

    // --- curator ------------------------------------------------------------------

    function listSeries(address series, ISurrogatePricer pricer, uint16 volBpsAnnual, uint128 capNotional)
        external
        onlyOwner
    {
        if (!factory.isSeries(series)) revert NotFactorySeries(series);
        SeriesTerms memory t = INoteSeries(series).terms();
        _checkRange(pricer, 2, volBpsAnnual);
        _checkRange(pricer, 3, t.kiBarrierBps);
        _checkRange(pricer, 4, t.acBarrierBps);
        _checkRange(pricer, 5, t.couponBpsPerPeriod);
        _checkRange(pricer, 8, t.observationCount);

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

    // --- ERC-4626 -----------------------------------------------------------------

    /// Reverts (with the quoter's or model's error) while a held series can't be quoted.
    function totalAssets() public view override(ERC4626, IERC4626) returns (uint256 assets) {
        assets = IERC20(asset()).balanceOf(address(this));
        uint256 n = _held.length();
        for (uint256 i = 0; i < n; i++) {
            (, uint256 v) = _positionValue(_held.at(i), true);
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
        if (!_allQuotable()) return 0;
        return Math.min(_convertToAssets(balanceOf(owner_), Math.Rounding.Floor), _idle());
    }

    function maxRedeem(address owner_) public view override(ERC4626, IERC4626) returns (uint256) {
        if (!_allQuotable()) return 0;
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

    /// One trade's parameters and quote, kept in memory (the events are wide).
    struct Trade {
        address series;
        uint256 noteAmount;
        uint16 feeBps;
        address feeReceiver;
        address to;
        uint16 priceBps;
        bytes32 weightsHash;
        uint256 fee;
        uint256 amount; // cost of a buy, proceeds of a sell
    }

    function _trade(address series, uint256 noteAmount, uint16 feeBps, address feeReceiver, address to, bool isBuy)
        internal
        view
        returns (Trade memory t)
    {
        (t.priceBps, t.weightsHash, t.fee) = _quote(series, noteAmount, feeBps, isBuy);
        t.series = series;
        t.noteAmount = noteAmount;
        t.feeBps = feeBps;
        t.feeReceiver = feeReceiver;
        t.to = to;
    }

    function _emitTrade(Trade memory t, bool isBuy) internal {
        if (isBuy) {
            emit NoteBought(
                t.series, msg.sender, t.to, t.noteAmount, t.priceBps, t.amount, t.feeBps, t.feeReceiver, t.weightsHash
            );
        } else {
            emit NoteSold(
                t.series, msg.sender, t.to, t.noteAmount, t.priceBps, t.amount, t.feeBps, t.feeReceiver, t.weightsHash
            );
        }
    }

    /// Price and fee checks shared by quotes and trades. Buys need an active
    /// listing; sells only a listing (delisted series stay sellable).
    function _quote(address series, uint256 noteAmount, uint16 feeBps, bool isBuy)
        internal
        view
        returns (uint16 priceBps, bytes32 weightsHash, uint256 fee)
    {
        if (noteAmount == 0) revert INoteSeries.ZeroAmount();
        if (feeBps > MAX_FEE_BPS) revert FeeTooHigh(feeBps);
        Listing memory l = _listings[series];
        if (isBuy ? !l.active : address(l.pricer) == address(0)) revert NotListed(series);
        (priceBps, weightsHash) = quoter.notePriceBps(INoteSeries(series), l.pricer, l.volBpsAnnual);
        uint40 next = INoteSeries(series).state().nextObservation;
        if (uint256(next) < block.timestamp + minSecsToObservation) revert TooCloseToObservation(next);
        fee = Math.mulDiv(noteAmount, feeBps, BPS, Math.Rounding.Ceil);
    }

    /// floor(n * price) - fee, and the fee can't exceed the gross amount
    /// (otherwise the integrator's cut would come out of the LPs).
    function _sellProceeds(uint256 noteAmount, uint16 priceBps, uint256 fee)
        internal
        pure
        returns (uint256 proceeds, uint256 feeTaken)
    {
        uint256 gross = Math.mulDiv(noteAmount, priceBps, BPS);
        feeTaken = Math.min(fee, gross);
        proceeds = gross - feeTaken;
    }

    function _checkCap(address series, uint256 noteAmount) internal view {
        Listing memory l = _listings[series];
        uint256 available = l.capNotional > l.soldNotional ? l.capNotional - l.soldNotional : 0;
        if (noteAmount > available) revert CapExceeded(noteAmount, available);
    }

    function _checkRange(ISurrogatePricer pricer, uint8 field, uint256 value) internal view {
        (int64 lo, int64 hi) = pricer.certifiedRange(field);
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

    function _idle() internal view returns (uint256) {
        return IERC20(asset()).balanceOf(address(this));
    }

    function _allQuotable() internal view returns (bool) {
        uint256 n = _held.length();
        for (uint256 i = 0; i < n; i++) {
            (bool ok,) = _positionValue(_held.at(i), false);
            if (!ok) return false;
        }
        return true;
    }

    /// Value of the Desk's NOTE + WRITER of one series. `strict` bubbles the
    /// quoter's revert; otherwise an unquotable series returns ok = false.
    function _positionValue(address series, bool strict) internal view returns (bool ok, uint256 value) {
        INoteSeries s = INoteSeries(series);
        uint256 n = IERC20(s.note()).balanceOf(address(this));
        uint256 w = IERC20(s.writer()).balanceOf(address(this));
        if (n == 0 && w == 0) return (true, 0);
        uint256 maxPayout = s.maxPayoutPerNote();
        SeriesState memory st = s.state();

        uint256 notePerUnit; // NOTE value in base units per 1e6 NOTE units
        if (st.phase == Phase.Settled) {
            notePerUnit = st.payoutPerNote;
        } else if (st.phase == Phase.Live && !st.knockedIn && st.observationsDone == s.terms().observationCount) {
            notePerUnit = maxPayout; // final period, not knocked in: the payout is certain
        } else {
            Listing memory l = _listings[series];
            uint16 priceBps;
            if (strict) {
                (priceBps,) = quoter.notePriceBps(s, l.pricer, l.volBpsAnnual);
            } else {
                try quoter.notePriceBps(s, l.pricer, l.volBpsAnnual) returns (uint16 p, bytes32) {
                    priceBps = p;
                } catch {
                    return (false, 0);
                }
            }
            notePerUnit = Math.min(uint256(priceBps) * UNIT_PER_BPS, maxPayout);
        }
        value = Math.mulDiv(n, notePerUnit, UNIT) + Math.mulDiv(w, maxPayout - notePerUnit, UNIT);
        return (true, value);
    }
}
