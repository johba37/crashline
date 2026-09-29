// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {INoteSeries} from "./INoteSeries.sol";
import {ISurrogatePricer, PricerInputs} from "./ISurrogatePricer.sol";

/// L2: view-only glue between a series and the model. Reads every note-state
/// input from the series and the recorder, never from the caller; the only
/// caller-supplied input is implied vol. Refuses (reverts) instead of guessing.
interface INoteQuoter {
    error NotLive(); // Pending or Settled
    error FixingPending(uint40 obsTime); // an observation passed, its fixing isn't recorded
    error FeedStale(uint40 updatedAt); // older than MAX_FEED_STALENESS (weekends, halts)
    error BadFeedAnswer();
    // plus ISurrogatePricer.OutOfRange(field, value), bubbled up from the model

    function pricer() external view returns (ISurrogatePricer);
    function MAX_FEED_STALENESS() external view returns (uint40);

    /// Exactly what the model sees: show it in the UI next to the price.
    /// Derived fields are exact: distToKnockInBps = spotBpsOfInitial - kiBarrierBps,
    /// timeToMaturitySecs = timeToNextObsSecs + observationsRemaining * interval.
    function inputs(INoteSeries series, uint16 volBpsAnnual) external view returns (PricerInputs memory);

    /// Fair value of 1 NOTE in bps of its 1-USDG notional, and the model version used.
    /// = the model's clean price + coupon accrued since strike, linear in time:
    ///   c * (block.timestamp - strikeTime) / observationInterval
    /// (the model prices only coupon accruing from now on; the accrued part is
    /// certain, since coupons are paid whatever the stock does).
    /// WRITER fair value = maxPayoutPerNote - NOTE fair value.
    /// No quote in the final period (no barrier observation left): the model's
    /// domain starts at observationsRemaining = 1.
    function notePriceBps(INoteSeries series, uint16 volBpsAnnual)
        external
        view
        returns (uint16 priceBps, bytes32 weightsHash);
}
