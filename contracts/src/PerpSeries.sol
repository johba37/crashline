// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {IPerpSeries, PerpTerms, PerpState, PerpPhase} from "./interfaces/IPerpSeries.sol";
import {IFixingsRecorder} from "./interfaces/IFixingsRecorder.sol";
import {ISeriesToken} from "./interfaces/ISeriesToken.sol";
import {PerpPayout} from "./PerpPayout.sol";

/// L1 core of the v2 perpetual note: one series = one USDG escrow + NOTE +
/// WRITER, no expiry (IPerpSeries, docs/v2-spec.md §2). EIP-1167 clone,
/// initialized by PerpFactory in the creating transaction. No model, no
/// admin, no pause, no upgrade.
///
/// Solvency: mint locks ceil(n * s * (1 + R)), redeemPair pays the floor, a
/// fixing releases at most the pair value it melts, and a holder's claim
/// rounds down, so the escrow always covers the released USDG still unclaimed
/// plus the pair value of the supply (tested as an invariant).
/// Released USDG is pulled with `claim(to)` to any address (USDG can freeze a
/// single account); a fixing moves no tokens.
///
/// Fixings: read from the feed's FixingsRecorder. A fixing unrecorded
/// `recorder.MAX_ROLL() + FALLBACK_GRACE` after its time reuses the last one
/// and counts as missed; CLOSE_AFTER_MISSED in a row close the series with a
/// full release. A fixing recorded before the series processes it always
/// wins. The first fixing has no fallback: an unstruck series never holds
/// collateral.
contract PerpSeries is IPerpSeries, ReentrancyGuard {
    using SafeERC20 for IERC20;
    using PerpPayout for PerpPayout.Progress;

    uint40 public constant FALLBACK_GRACE = 1 days;
    uint8 public constant CLOSE_AFTER_MISSED = 4;

    /// The factory that deployed this implementation; clones see it through
    /// delegatecall, so only that factory can initialize them.
    address public immutable factory;

    /// What a holder was last settled at, per token, and the USDG settled but not yet claimed.
    struct Account {
        uint128 noteCheckpoint;
        uint128 writerCheckpoint;
        uint256 accrued;
    }

    bytes32 public id;
    PerpTerms internal _terms;
    address public note;
    address public writer;
    IFixingsRecorder public recorder;
    address public collateral;
    uint40 internal _fallbackDelay; // recorder.MAX_ROLL() + FALLBACK_GRACE
    PerpPayout.Progress internal _progress;
    mapping(address holder => Account) internal _accounts;

    error AlreadyInitialized();
    error OnlyFactory();

    constructor() {
        factory = msg.sender;
        id = bytes32(uint256(1)); // the implementation itself is never initialized
    }

    /// Called once by the factory right after cloning. Terms are validated there.
    function initialize(
        bytes32 id_,
        PerpTerms calldata terms_,
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
        _fallbackDelay = IFixingsRecorder(recorder_).MAX_ROLL() + FALLBACK_GRACE;
    }

    // --- views ------------------------------------------------------------------

    function terms() external view returns (PerpTerms memory) {
        return _terms;
    }

    function pairValuePerNotional() external view returns (uint256) {
        return PerpPayout.UNIT + _terms.couponReserve;
    }

    function state() external view returns (PerpState memory) {
        return _toState(_replay());
    }

    function pendingFixing() external view returns (bool pending, uint40 fixingTime) {
        PerpPayout.Progress memory p = _replay();
        if (p.phase == PerpPhase.Closed) return (false, 0);
        fixingTime = _nextTime(p);
        // _replay processed everything that is recorded or past its fallback
        // deadline, so a past fixing time here means its fixing is missing.
        pending = fixingTime < block.timestamp;
    }

    function claimable(address holder) external view returns (uint256) {
        PerpPayout.Progress memory p = _replay();
        Account memory acc = _accounts[holder];
        return acc.accrued + _owed(IERC20(note).balanceOf(holder), p.noteIndex, acc.noteCheckpoint)
            + _owed(IERC20(writer).balanceOf(holder), p.writerIndex, acc.writerCheckpoint);
    }

    function previewMint(uint256 amount) external view returns (uint256) {
        return _pairValue(amount, _replay(), Math.Rounding.Ceil);
    }

    function previewRedeemPair(uint256 amount) external view returns (uint256) {
        return _pairValue(amount, _replay(), Math.Rounding.Floor);
    }

    function notionalOf(uint256 amount) external view returns (uint256) {
        return Math.mulDiv(amount, _replay().notionalPerToken, PerpPayout.RAY);
    }

    // --- state changes ----------------------------------------------------------

    function advance() external nonReentrant returns (PerpState memory) {
        return _toState(_advance(type(uint256).max));
    }

    function advanceBy(uint256 maxFixings) external nonReentrant returns (PerpState memory) {
        return _toState(_advance(maxFixings));
    }

    function mint(uint256 amount, address to) external nonReentrant returns (uint256 collateralIn) {
        if (amount == 0) revert ZeroAmount();
        PerpPayout.Progress memory p = _advance(type(uint256).max);
        if (p.phase == PerpPhase.Pending) revert NotStruck();
        if (p.phase == PerpPhase.Closed) revert SeriesClosed();
        _settle(to, p);
        collateralIn = _pairValue(amount, p, Math.Rounding.Ceil);
        IERC20(collateral).safeTransferFrom(msg.sender, address(this), collateralIn);
        ISeriesToken(note).mint(to, amount);
        ISeriesToken(writer).mint(to, amount);
        emit Minted(msg.sender, to, amount, collateralIn);
    }

    function redeemPair(uint256 amount, address to) external nonReentrant returns (uint256 collateralOut) {
        if (amount == 0) revert ZeroAmount();
        PerpPayout.Progress memory p = _advance(type(uint256).max);
        if (p.phase == PerpPhase.Pending) revert NotStruck();
        _settle(msg.sender, p);
        ISeriesToken(note).burn(msg.sender, amount);
        ISeriesToken(writer).burn(msg.sender, amount);
        collateralOut = _pairValue(amount, p, Math.Rounding.Floor);
        if (collateralOut != 0) IERC20(collateral).safeTransfer(to, collateralOut);
        emit PairRedeemed(msg.sender, to, amount, collateralOut);
    }

    function claim(address to) external nonReentrant returns (uint256 amount) {
        _settle(msg.sender, _advance(type(uint256).max));
        Account storage acc = _accounts[msg.sender];
        amount = acc.accrued;
        acc.accrued = 0;
        if (amount != 0) IERC20(collateral).safeTransfer(to, amount);
        emit Claimed(msg.sender, to, amount);
    }

    /// Only NOTE or WRITER, before a transfer moves balances. The token passes
    /// the balances it is about to change, which are its own.
    function checkpoint(address from, uint256 fromBalance, address to, uint256 toBalance) external nonReentrant {
        bool isNote = msg.sender == note;
        if (!isNote && msg.sender != writer) revert OnlyToken();
        PerpPayout.Progress memory p = _advance(type(uint256).max);
        _settleLeg(from, fromBalance, isNote, p);
        _settleLeg(to, toBalance, isNote, p);
    }

    // --- internals --------------------------------------------------------------

    function _pairValue(uint256 amount, PerpPayout.Progress memory p, Math.Rounding rounding)
        internal
        view
        returns (uint256)
    {
        return PerpPayout.pairValue(amount, p.notionalPerToken, _terms.couponReserve, rounding);
    }

    /// floor(balance * (index - checkpoint)): what one leg released to a holder since it was last settled.
    function _owed(uint256 balance, uint256 index, uint256 checkpoint_) internal pure returns (uint256) {
        if (balance == 0 || index == checkpoint_) return 0;
        return Math.mulDiv(balance, index - checkpoint_, PerpPayout.RAY);
    }

    /// Settles one leg of a holder at the indexes of `p`, for the balance it holds before a change.
    function _settleLeg(address holder, uint256 balance, bool isNote, PerpPayout.Progress memory p) internal {
        Account storage acc = _accounts[holder];
        uint256 owed;
        if (isNote) {
            owed = _owed(balance, p.noteIndex, acc.noteCheckpoint);
            acc.noteCheckpoint = p.noteIndex;
        } else {
            owed = _owed(balance, p.writerIndex, acc.writerCheckpoint);
            acc.writerCheckpoint = p.writerIndex;
        }
        if (owed != 0) acc.accrued += owed;
    }

    function _settle(address holder, PerpPayout.Progress memory p) internal {
        _settleLeg(holder, IERC20(note).balanceOf(holder), true, p);
        _settleLeg(holder, IERC20(writer).balanceOf(holder), false, p);
    }

    /// Time of the next fixing: the first one while Pending, then one interval after the last processed.
    function _nextTime(PerpPayout.Progress memory p) internal view returns (uint40) {
        uint256 n = p.phase == PerpPhase.Pending ? 0 : uint256(p.fixingsDone) + 1;
        // unix seconds on a grid of >= 1 h steps fit uint40 for tens of thousands of years
        // forge-lint: disable-next-line(unsafe-typecast)
        return uint40(uint256(_terms.firstFixing) + n * _terms.fixingInterval);
    }

    /// The next fixing that can be processed now, if any.
    function _nextFixing(PerpPayout.Progress memory p)
        internal
        view
        returns (bool ok, uint40 fixingTime, uint96 fixing, bool missed)
    {
        if (p.phase == PerpPhase.Closed) return (false, 0, 0, false);
        fixingTime = _nextTime(p);
        IFixingsRecorder.Fixing memory f = recorder.fixingOf(fixingTime);
        if (f.timestamp != 0) return (true, fixingTime, f.price, false);
        if (p.phase == PerpPhase.Live && block.timestamp >= uint256(fixingTime) + _fallbackDelay) {
            return (true, fixingTime, p.lastFixing, true);
        }
        return (false, fixingTime, 0, false);
    }

    /// Applies one fixing in memory. Returns the USDG released per token (0, 0 for the first fixing).
    function _apply(PerpPayout.Progress memory p, uint96 fixing, bool missed)
        internal
        view
        returns (uint256 noteRelease, uint256 writerRelease)
    {
        if (p.phase == PerpPhase.Pending) {
            p.strike(fixing);
            return (0, 0);
        }
        PerpTerms storage t = _terms;
        return p.fix(t.kiBarrierBps, t.meltShare, t.couponReserve, CLOSE_AFTER_MISSED, fixing, missed);
    }

    /// state() without writing: every processable fixing applied in memory.
    function _replay() internal view returns (PerpPayout.Progress memory p) {
        p = _progress;
        while (true) {
            (bool ok,, uint96 fixing, bool missed) = _nextFixing(p);
            if (!ok) return p;
            _apply(p, fixing, missed);
        }
    }

    /// Processes up to `maxFixings` processable fixings in order, emits, and stores the result.
    function _advance(uint256 maxFixings) internal returns (PerpPayout.Progress memory p) {
        p = _progress;
        bool changed = false;
        for (; maxFixings != 0; maxFixings--) {
            (bool ok, uint40 fixingTime, uint96 fixing, bool missed) = _nextFixing(p);
            if (!ok) break;
            changed = true;
            bool first = p.phase == PerpPhase.Pending;
            (uint256 noteRelease, uint256 writerRelease) = _apply(p, fixing, missed);
            if (first) {
                emit Struck(fixing);
                continue;
            }
            emit FixingProcessed(
                p.fixingsDone,
                fixingTime,
                fixing,
                p.referenceFixing,
                p.knockedIn,
                missed,
                p.notionalPerToken,
                noteRelease,
                writerRelease
            );
            if (p.phase == PerpPhase.Closed) emit Closed(p.fixingsDone, fixing);
        }
        if (changed) _progress = p;
    }

    function _toState(PerpPayout.Progress memory p) internal view returns (PerpState memory s) {
        s.phase = p.phase;
        s.referenceFixing = p.referenceFixing;
        s.lastFixing = p.lastFixing;
        s.knockedIn = p.knockedIn;
        s.missedInARow = p.missedInARow;
        s.fixingsDone = p.fixingsDone;
        s.nextFixing = p.phase == PerpPhase.Closed ? 0 : _nextTime(p);
        s.notionalPerToken = p.notionalPerToken;
        s.noteIndex = p.noteIndex;
        s.writerIndex = p.writerIndex;
    }
}
