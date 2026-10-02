// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {INoteSeries, SeriesTerms, SeriesState, Phase} from "./interfaces/INoteSeries.sol";
import {IFixingsRecorder} from "./interfaces/IFixingsRecorder.sol";
import {ISeriesToken} from "./interfaces/ISeriesToken.sol";
import {AutocallPayout} from "./AutocallPayout.sol";

/// L1 core: one series = one USDG escrow + NOTE + WRITER. EIP-1167 clone,
/// initialized by SeriesFactory in the creating transaction. No model, no
/// admin, no pause, no upgrade.
///
/// Solvency: mint locks ceil(n * max / 1e6), every payment rounds down, and
/// NOTE + WRITER payouts of a pair add up to max, so the escrow always covers
/// what the outstanding tokens can claim (tested as an invariant).
/// Settlement is a state flip: `settle` moves no tokens, holders pull with
/// `redeem(…, to)` to any address (USDG can freeze a single account).
///
/// Fixings: each fixing is read from the feed's FixingsRecorder. A fixing
/// that is still unrecorded `recorder.MAX_ROLL() + FALLBACK_GRACE` after its
/// observation time reuses the previous fixing (the strike fixing for i = 1).
/// A fixing recorded before the series processes it always wins over the
/// fallback. The strike fixing has no fallback: an unstruck series never
/// holds collateral.
contract NoteSeries is INoteSeries, ReentrancyGuard {
    using SafeERC20 for IERC20;
    using AutocallPayout for AutocallPayout.Progress;

    uint40 public constant FALLBACK_GRACE = 1 days;

    /// The factory that deployed this implementation; clones see it through
    /// delegatecall, so only that factory can initialize them.
    address public immutable factory;

    bytes32 public id;
    SeriesTerms internal _terms;
    address public note;
    address public writer;
    IFixingsRecorder public recorder;
    address public collateral;
    uint128 public maxPayoutPerNote;
    uint40 internal _fallbackDelay; // recorder.MAX_ROLL() + FALLBACK_GRACE
    AutocallPayout.Progress internal _progress;

    constructor() {
        factory = msg.sender;
        id = bytes32(uint256(1)); // the implementation itself is never initialized
    }

    /// Called once by the factory right after cloning. Terms are validated there.
    function initialize(
        bytes32 id_,
        SeriesTerms calldata terms_,
        address note_,
        address writer_,
        address recorder_,
        address collateral_
    ) external {
        if (msg.sender != factory) revert OnlyFactory();
        if (id != bytes32(0)) revert AlreadyInitialized();
        id = id_;
        _terms = terms_;
        note = note_;
        writer = writer_;
        recorder = IFixingsRecorder(recorder_);
        collateral = collateral_;
        maxPayoutPerNote = AutocallPayout.maxPayoutPerNote(terms_.couponBpsPerPeriod, terms_.observationCount);
        _fallbackDelay = IFixingsRecorder(recorder_).MAX_ROLL() + FALLBACK_GRACE;
    }

    // --- views ------------------------------------------------------------------

    function terms() external view returns (SeriesTerms memory) {
        return _terms;
    }

    function state() external view returns (SeriesState memory) {
        return _toState(_replay());
    }

    function pendingObservation() external view returns (bool pending, uint40 obsTime) {
        AutocallPayout.Progress memory p = _replay();
        if (p.phase == Phase.Settled) return (false, 0);
        obsTime = _nextTime(p);
        // _replay processed everything that is recorded or past its fallback
        // deadline, so a past observation time here means its fixing is missing.
        pending = obsTime < block.timestamp;
    }

    function previewMint(uint256 amount) public view returns (uint256) {
        return Math.mulDiv(amount, maxPayoutPerNote, AutocallPayout.UNIT, Math.Rounding.Ceil);
    }

    function previewRedeemPair(uint256 amount) public view returns (uint256) {
        return Math.mulDiv(amount, maxPayoutPerNote, AutocallPayout.UNIT);
    }

    /// Reverts NotSettled while the payout isn't fixed (including recorded but
    /// unprocessed fixings, like every view here).
    function previewRedeem(uint256 noteAmount, uint256 writerAmount) external view returns (uint256) {
        AutocallPayout.Progress memory p = _replay();
        if (p.phase != Phase.Settled) revert NotSettled();
        return _redeemValue(p.payoutPerNote, noteAmount, writerAmount);
    }

    // --- state changes ----------------------------------------------------------

    function advance() external nonReentrant returns (SeriesState memory) {
        return _toState(_advance());
    }

    function mint(uint256 amount, address to) external nonReentrant returns (uint256 collateralIn) {
        if (amount == 0) revert ZeroAmount();
        Phase phase = _advance().phase;
        if (phase == Phase.Pending) revert NotStruck();
        if (phase == Phase.Settled) revert AlreadySettled();
        collateralIn = previewMint(amount);
        IERC20(collateral).safeTransferFrom(msg.sender, address(this), collateralIn);
        ISeriesToken(note).mint(to, amount);
        ISeriesToken(writer).mint(to, amount);
        emit Minted(msg.sender, to, amount, collateralIn);
    }

    function redeemPair(uint256 amount, address to) external nonReentrant returns (uint256 collateralOut) {
        if (amount == 0) revert ZeroAmount();
        if (_advance().phase == Phase.Pending) revert NotStruck();
        ISeriesToken(note).burn(msg.sender, amount);
        ISeriesToken(writer).burn(msg.sender, amount);
        collateralOut = previewRedeemPair(amount);
        IERC20(collateral).safeTransfer(to, collateralOut);
        emit PairRedeemed(msg.sender, to, amount, collateralOut);
    }

    function redeem(uint256 noteAmount, uint256 writerAmount, address to)
        external
        nonReentrant
        returns (uint256 collateralOut)
    {
        if (noteAmount == 0 && writerAmount == 0) revert ZeroAmount();
        AutocallPayout.Progress memory p = _advance();
        if (p.phase != Phase.Settled) revert NotSettled();
        if (noteAmount != 0) ISeriesToken(note).burn(msg.sender, noteAmount);
        if (writerAmount != 0) ISeriesToken(writer).burn(msg.sender, writerAmount);
        collateralOut = _redeemValue(p.payoutPerNote, noteAmount, writerAmount);
        if (collateralOut != 0) IERC20(collateral).safeTransfer(to, collateralOut);
        emit Redeemed(msg.sender, to, noteAmount, writerAmount, collateralOut);
    }

    // --- internals --------------------------------------------------------------

    /// NOTE pays floor(n * payout / 1e6), WRITER floor(w * (max - payout) / 1e6).
    function _redeemValue(uint128 payoutPerNote, uint256 noteAmount, uint256 writerAmount)
        internal
        view
        returns (uint256)
    {
        return Math.mulDiv(noteAmount, payoutPerNote, AutocallPayout.UNIT)
            + Math.mulDiv(writerAmount, maxPayoutPerNote - payoutPerNote, AutocallPayout.UNIT);
    }

    /// Time of the next fixing: strike while Pending, then observation i, then maturity.
    function _nextTime(AutocallPayout.Progress memory p) internal view returns (uint40) {
        if (p.phase == Phase.Pending) return _terms.strikeTime;
        return uint40(uint256(_terms.strikeTime) + uint256(p.nextIndex()) * _terms.observationInterval);
    }

    /// The next fixing that can be processed now, if any.
    function _nextFixing(AutocallPayout.Progress memory p)
        internal
        view
        returns (bool ok, uint40 obsTime, uint96 fixing, bool fallbackUsed)
    {
        if (p.phase == Phase.Settled) return (false, 0, 0, false);
        obsTime = _nextTime(p);
        IFixingsRecorder.Fixing memory f = recorder.fixingOf(obsTime);
        if (f.timestamp != 0) return (true, obsTime, f.price, false);
        if (p.phase == Phase.Live && block.timestamp >= uint256(obsTime) + _fallbackDelay) {
            return (true, obsTime, p.lastFixing, true);
        }
        return (false, obsTime, 0, false);
    }

    /// Applies one fixing in memory. Returns the observation index (0 = strike).
    function _apply(AutocallPayout.Progress memory p, uint96 fixing) internal view returns (uint8 index) {
        if (p.phase == Phase.Pending) {
            p.strike(fixing);
            return 0;
        }
        index = p.nextIndex();
        SeriesTerms storage t = _terms;
        p.observe(t.kiBarrierBps, t.acBarrierBps, t.couponBpsPerPeriod, t.observationCount, fixing);
    }

    /// state() without writing: every processable fixing applied in memory.
    function _replay() internal view returns (AutocallPayout.Progress memory p) {
        p = _progress;
        while (true) {
            (bool ok,, uint96 fixing,) = _nextFixing(p);
            if (!ok) return p;
            _apply(p, fixing);
        }
    }

    /// Processes every processable fixing in order, emits, and stores the result.
    function _advance() internal returns (AutocallPayout.Progress memory p) {
        p = _progress;
        bool changed = false;
        while (true) {
            (bool ok, uint40 obsTime, uint96 fixing, bool fallbackUsed) = _nextFixing(p);
            if (!ok) break;
            changed = true;
            uint8 index = _apply(p, fixing);
            if (index == 0) {
                emit Struck(fixing);
                continue;
            }
            emit ObservationProcessed(index, obsTime, fixing, p.knockedIn, p.autocalled, fallbackUsed);
            if (p.phase == Phase.Settled) {
                emit Settled(
                    p.settledAt, p.autocalled, p.knockedIn, p.payoutPerNote, maxPayoutPerNote - p.payoutPerNote
                );
            }
        }
        if (changed) _progress = p;
    }

    function _toState(AutocallPayout.Progress memory p) internal view returns (SeriesState memory s) {
        s.phase = p.phase;
        s.initialFixing = p.initialFixing;
        s.observationsDone = p.observationsDone;
        s.knockedIn = p.knockedIn;
        s.autocalled = p.autocalled;
        s.nextObservation = p.phase == Phase.Settled ? 0 : _nextTime(p);
        uint256 maturity =
            uint256(_terms.strikeTime) + (uint256(_terms.observationCount) + 1) * _terms.observationInterval;
        // the factory rejects schedules whose maturity doesn't fit uint40 (BadSchedule)
        // forge-lint: disable-next-line(unsafe-typecast)
        s.maturity = uint40(maturity);
        s.payoutPerNote = p.payoutPerNote;
    }
}
