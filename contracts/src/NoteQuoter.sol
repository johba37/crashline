// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {SafeCast} from "@openzeppelin/contracts/utils/math/SafeCast.sol";
import {INoteQuoter} from "./interfaces/INoteQuoter.sol";
import {INoteSeries, SeriesState, SeriesTerms, Phase} from "./interfaces/INoteSeries.sol";
import {ISurrogatePricer, PricerInputs} from "./interfaces/ISurrogatePricer.sol";
import {IAggregatorV3} from "./interfaces/IAggregatorV3.sol";

/// L2: view-only glue between a series and a model (INoteQuoter). Every
/// note-state input comes from the series (which replays its recorder), the
/// spot from the series' feed; the caller only picks the model and the vol.
/// One stateless quoter serves every model. Refuses instead of guessing:
/// NotLive, FixingPending, FeedStale, BadFeedAnswer, and the model's own
/// OutOfRange / Inconsistent / Uncertified bubble up unchanged.
///
/// Feature derivation (featureSpecVersion 1, tools/quoter_vectors.py is the
/// Python twin checked by NoteQuoter.t.sol):
///   spotBpsOfInitial      = floor(spot * 10_000 / initialFixing)
///   distToKnockInBps      = spotBpsOfInitial - kiBarrierBps          (exact)
///   observationsRemaining = observationCount - observationsDone      (0 in the final period)
///   timeToNextObsSecs     = nextObservation - now
///   timeToMaturitySecs    = timeToNextObsSecs + observationsRemaining * interval (exact)
///   flags                 = knockedIn ? 1 : 0
/// A value its PricerInputs type can't hold reverts as
/// ISurrogatePricer.OutOfRange(field, value), like the model would.
contract NoteQuoter is INoteQuoter {
    uint256 internal constant BPS = 10_000;

    /// A feed older than this refuses quotes. The Robinhood Chain stock feeds
    /// are 24/5: a heartbeat plus margin keeps weekday quotes alive and turns
    /// weekends and halts into FeedStale.
    uint40 public immutable MAX_FEED_STALENESS;

    constructor(uint40 maxFeedStaleness) {
        MAX_FEED_STALENESS = maxFeedStaleness;
    }

    /// @inheritdoc INoteQuoter
    function inputs(INoteSeries series, uint16 volBpsAnnual) public view returns (PricerInputs memory in_) {
        SeriesState memory st = series.state();
        if (st.phase != Phase.Live) revert NotLive();
        (bool pending, uint40 obsTime) = series.pendingObservation();
        if (pending) revert FixingPending(obsTime);
        SeriesTerms memory t = series.terms();

        // Every cast below is range-checked first (or exact by construction).
        // forge-lint: disable-start(unsafe-typecast)
        uint256 spot = _spot(IAggregatorV3(t.feed));
        uint256 spotBps = spot * BPS / st.initialFixing;
        if (spotBps > type(uint16).max) revert ISurrogatePricer.OutOfRange(0, int64(uint64(spotBps)));

        uint256 obsRemaining = uint256(t.observationCount) - st.observationsDone;
        // not pending, so the next fixing time is now or later
        uint256 tNext = uint256(st.nextObservation) - block.timestamp;
        if (tNext > type(uint32).max) revert ISurrogatePricer.OutOfRange(7, int64(uint64(tNext)));
        uint256 ttm = tNext + obsRemaining * t.observationInterval;
        if (ttm > type(uint32).max) revert ISurrogatePricer.OutOfRange(6, int64(uint64(ttm)));

        in_.spotBpsOfInitial = uint16(spotBps);
        in_.distToKnockInBps = int32(int256(spotBps) - int256(uint256(t.kiBarrierBps)));
        in_.volBpsAnnual = volBpsAnnual;
        in_.kiBarrierBps = t.kiBarrierBps;
        in_.acBarrierBps = t.acBarrierBps;
        in_.couponBpsPerPeriod = t.couponBpsPerPeriod;
        in_.timeToMaturitySecs = uint32(ttm);
        in_.timeToNextObsSecs = uint32(tNext);
        in_.observationsRemaining = uint8(obsRemaining);
        in_.flags = st.knockedIn ? 1 : 0;
        // forge-lint: disable-end(unsafe-typecast)
    }

    /// @inheritdoc INoteQuoter
    /// @dev The accrued coupon rounds down to whole bps.
    function notePriceBps(INoteSeries series, ISurrogatePricer pricer, uint16 volBpsAnnual)
        external
        view
        returns (uint16 priceBps, bytes32 weightsHash)
    {
        PricerInputs memory in_ = inputs(series, volBpsAnnual);
        uint256 clean = pricer.priceBps(in_);
        SeriesTerms memory t = series.terms();
        uint256 accrued = uint256(t.couponBpsPerPeriod) * (block.timestamp - t.strikeTime) / t.observationInterval;
        priceBps = SafeCast.toUint16(clean + accrued);
        weightsHash = pricer.weightsHash();
    }

    function _spot(IAggregatorV3 feed) internal view returns (uint256) {
        // forge-lint: disable-next-line(unused-return)
        (, int256 answer,, uint256 updatedAt,) = feed.latestRoundData();
        if (answer <= 0) revert BadFeedAnswer();
        // forge-lint: disable-next-line(unsafe-typecast)
        if (updatedAt + MAX_FEED_STALENESS < block.timestamp) revert FeedStale(uint40(updatedAt));
        // forge-lint: disable-next-line(unsafe-typecast)
        return uint256(answer); // > 0, checked above
    }
}
