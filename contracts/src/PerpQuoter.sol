// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {SafeCast} from "@openzeppelin/contracts/utils/math/SafeCast.sol";
import {IPerpQuoter, PerpQuote} from "./interfaces/IPerpQuoter.sol";
import {IPerpSeries, PerpState, PerpTerms, PerpPhase} from "./interfaces/IPerpSeries.sol";
import {IPerpPricer, PerpPricerInputs, PerpProduct} from "./interfaces/IPerpPricer.sol";
import {IAggregatorV3} from "./interfaces/IAggregatorV3.sol";
import {PerpFormula} from "./PerpFormula.sol";

/// L2 of the v2 perpetual note (IPerpQuoter): on-chain price = closed form +
/// model correction + the coupon reserve's value. Every note-state input
/// comes from the series, the spot from the series' feed (or, in
/// `quoteAtSpot`, from the caller); the caller picks the model, the vol and
/// the next earnings date. Refuses instead of guessing: NotLive,
/// FixingPending, FeedStale, BadFeedAnswer, EarningsDatePassed,
/// ProductMismatch, and the model's and the formula's own errors bubble up.
///
/// Feature derivation (featureSpecVersion 2; tools/perp_quoter_vectors.py is
/// the Python twin checked by PerpQuoter.t.sol):
///   spotBpsOfReference     = floor(spot * 10_000 / reference)
///   timeToNextFixingSecs   = nextFixing - now
///   fixingsBeforeEarnings  = 0 if nextEarnings <= nextFixing, else
///                            ceil((nextEarnings - nextFixing) / interval), capped at 255
///   flags                  = knockedIn ? 1 : 0
/// Price assembly, per unit of live notional:
///   formulaBps = round(PerpFormula.principal(x = spotBpsOfReference / 1e4) * 1e4)
///   couponBps  = floor(couponReserve * PerpFormula.coupon / 100)
///   priceBps   = max(0, formulaBps + correctionBps) + couponBps
/// The formula's world is the series' terms (k, melt share, interval), the
/// model's pinned rates and the caller's vol; the model answers only for a
/// series whose terms are its product.
contract PerpQuoter is IPerpQuoter {
    uint256 internal constant BPS = 10_000;
    uint256 internal constant WAD = 1e18;
    uint256 internal constant WAD_PER_BPS = WAD / BPS;
    int256 internal constant SIGNED_WAD_PER_BPS = 1e14;
    uint256 internal constant YEAR = 31_536_000; // 365 days, the teacher's year
    uint256 internal constant UNIT_PER_BPS = 100; // couponReserve is per 1e6 of notional

    /// A feed older than this refuses quotes. The Robinhood Chain stock feeds
    /// are 24/5: a heartbeat plus margin keeps weekday quotes alive and turns
    /// weekends and halts into FeedStale.
    uint40 public immutable MAX_FEED_STALENESS;

    constructor(uint40 maxFeedStaleness) {
        MAX_FEED_STALENESS = maxFeedStaleness;
    }

    /// @inheritdoc IPerpQuoter
    function spot(IPerpSeries series) external view returns (uint256) {
        return _spot(series.terms().feed);
    }

    /// @inheritdoc IPerpQuoter
    function inputs(IPerpSeries series, uint16 volBpsAnnual, uint40 nextEarnings)
        external
        view
        returns (PerpPricerInputs memory)
    {
        PerpState memory st = _liveState(series);
        PerpTerms memory t = series.terms();
        return _inputs(st, t.fixingInterval, volBpsAnnual, nextEarnings, _spot(t.feed));
    }

    /// @inheritdoc IPerpQuoter
    function quote(IPerpSeries series, IPerpPricer pricer, uint16 volBpsAnnual, uint40 nextEarnings)
        public
        view
        returns (PerpQuote memory)
    {
        PerpState memory st = _liveState(series);
        PerpTerms memory t = series.terms();
        return _quote(t, st, pricer, volBpsAnnual, nextEarnings, _spot(t.feed));
    }

    /// @inheritdoc IPerpQuoter
    function quoteAtSpot(
        IPerpSeries series,
        IPerpPricer pricer,
        uint16 volBpsAnnual,
        uint40 nextEarnings,
        uint256 spot_
    ) external view returns (PerpQuote memory) {
        if (spot_ == 0) revert BadFeedAnswer();
        return _quote(series.terms(), _liveState(series), pricer, volBpsAnnual, nextEarnings, spot_);
    }

    /// @inheritdoc IPerpQuoter
    function notePriceBps(IPerpSeries series, IPerpPricer pricer, uint16 volBpsAnnual, uint40 nextEarnings)
        external
        view
        returns (uint16 priceBps, bytes32 weightsHash)
    {
        PerpQuote memory q = quote(series, pricer, volBpsAnnual, nextEarnings);
        return (q.priceBps, q.weightsHash);
    }

    // --- internals --------------------------------------------------------------

    function _spot(address feed) internal view returns (uint256) {
        // forge-lint: disable-next-line(unused-return)
        (, int256 answer,, uint256 updatedAt,) = IAggregatorV3(feed).latestRoundData();
        if (answer <= 0) revert BadFeedAnswer();
        // forge-lint: disable-next-line(unsafe-typecast)
        if (updatedAt + MAX_FEED_STALENESS < block.timestamp) revert FeedStale(uint40(updatedAt));
        // forge-lint: disable-next-line(unsafe-typecast)
        return uint256(answer); // > 0, checked above
    }

    /// The series' state, which must be Live with no fixing pending. `state()` has
    /// processed everything that can be processed, so a next fixing time in the past
    /// means that fixing is missing (IPerpSeries.pendingFixing).
    function _liveState(IPerpSeries series) internal view returns (PerpState memory st) {
        st = series.state();
        if (st.phase != PerpPhase.Live) revert NotLive();
        if (st.nextFixing < block.timestamp) revert FixingPending(st.nextFixing);
    }

    function _inputs(PerpState memory st, uint32 interval, uint16 volBpsAnnual, uint40 nextEarnings, uint256 spot_)
        internal
        view
        returns (PerpPricerInputs memory in_)
    {
        if (nextEarnings < block.timestamp) revert EarningsDatePassed(nextEarnings);

        // Every cast below is range-checked first (or exact by construction).
        // forge-lint: disable-start(unsafe-typecast)
        uint256 spotBps = spot_ * BPS / st.referenceFixing;
        if (spotBps > type(uint16).max) {
            revert IPerpPricer.OutOfRange(
                0, spotBps > uint64(type(int64).max) ? type(int64).max : int64(uint64(spotBps))
            );
        }
        // not pending, so the next fixing time is now or later
        uint256 tNext = uint256(st.nextFixing) - block.timestamp;
        if (tNext > type(uint32).max) revert IPerpPricer.OutOfRange(2, int64(uint64(tNext)));
        // fixings strictly before the release: one exactly at a fixing time counts as before it
        uint256 fixingsBefore =
            nextEarnings <= st.nextFixing ? 0 : (uint256(nextEarnings) - st.nextFixing + interval - 1) / interval;

        in_.spotBpsOfReference = uint16(spotBps);
        in_.volBpsAnnual = volBpsAnnual;
        in_.timeToNextFixingSecs = uint32(tNext);
        in_.fixingsBeforeEarnings = fixingsBefore > type(uint8).max ? type(uint8).max : uint8(fixingsBefore);
        in_.flags = st.knockedIn ? 1 : 0;
        // forge-lint: disable-end(unsafe-typecast)
    }

    function _quote(
        PerpTerms memory t,
        PerpState memory st,
        IPerpPricer pricer,
        uint16 volBpsAnnual,
        uint40 nextEarnings,
        uint256 spot_
    ) internal view returns (PerpQuote memory q) {
        PerpPricerInputs memory in_ = _inputs(st, t.fixingInterval, volBpsAnnual, nextEarnings, spot_);
        // one call into the model: its refusals come first, then the product check
        PerpProduct memory p;
        (q.correctionBps, p, q.weightsHash) = pricer.answer(in_);
        if (p.kiBarrierBps != t.kiBarrierBps) revert ProductMismatch(0);
        if (p.meltShare != t.meltShare) revert ProductMismatch(1);
        if (p.fixingInterval != t.fixingInterval) revert ProductMismatch(2);
        (uint256 formulaBps, uint256 couponBps) = _closedForm(t, p, in_);

        // formulaBps fits int256 by far
        // forge-lint: disable-next-line(unsafe-typecast)
        int256 corrected = int256(formulaBps) + q.correctionBps;
        // forge-lint: disable-next-line(unsafe-typecast)
        uint256 principalBps = corrected > 0 ? uint256(corrected) : 0;
        q.formulaBps = SafeCast.toUint16(formulaBps);
        q.couponBps = SafeCast.toUint16(couponBps);
        q.priceBps = SafeCast.toUint16(principalBps + couponBps);
    }

    /// The closed form's principal and the coupon reserve's value, in bps of live notional:
    /// the series' terms, the model's rates, the inputs the model saw.
    function _closedForm(PerpTerms memory t, PerpProduct memory p, PerpPricerInputs memory in_)
        internal
        pure
        returns (uint256 formulaBps, uint256 couponBps)
    {
        uint256 dt = uint256(t.fixingInterval) * WAD / YEAR;
        int256 rho = int256(p.discountBps) * SIGNED_WAD_PER_BPS;
        uint256 principal = PerpFormula.principal(
            PerpFormula.World({
                sigma: uint256(in_.volBpsAnnual) * WAD_PER_BPS,
                r: int256(p.driftBps) * SIGNED_WAD_PER_BPS,
                rho: rho,
                phi: PerpFormula.meltRate(t.meltShare, dt),
                k: uint256(t.kiBarrierBps) * WAD_PER_BPS,
                dt: dt
            }),
            uint256(in_.spotBpsOfReference) * WAD_PER_BPS,
            in_.flags != 0
        );
        formulaBps = (principal * BPS + WAD / 2) / WAD;
        uint256 tau = uint256(in_.timeToNextFixingSecs) * WAD / YEAR;
        couponBps = uint256(t.couponReserve) * PerpFormula.coupon(t.meltShare, rho, dt, tau) / (WAD * UNIT_PER_BPS);
    }
}
