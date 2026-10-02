// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IAggregatorV3} from "./interfaces/IAggregatorV3.sol";
import {IFixingsRecorder} from "./interfaces/IFixingsRecorder.sol";

/// L0: one recorder per feed. Permissionless and trustless: anyone can record
/// a fixing, and every recorded fixing is verified against feed rounds
/// on-chain. Design: DESIGN.md, Decision 1.
///
/// Fixing rule for a scheduled observation time `obsTime`:
///   Case A (normal):  last round with updatedAt <= obsTime, age <= MAX_FIX_AGE.
///   Case B (rolled):  first round with updatedAt > obsTime, when the previous
///                     round proves a gap > MAX_FIX_AGE (disrupted day), at
///                     most MAX_ROLL late.
contract FixingsRecorder is IFixingsRecorder {
    /// How old the round of a normal fixing may be: covers a holiday weekend.
    uint40 public constant MAX_FIX_AGE = 96 hours;
    /// How late a rolled fixing may be. Beyond it the fallback applies, and that
    /// lives in NoteSeries: a fixing still unrecorded MAX_ROLL + FALLBACK_GRACE
    /// after its time reuses the previous fixing.
    uint40 public constant MAX_ROLL = 8 days;
    /// Highest accepted answer: $100k at 8 decimals. Rejects a mis-scaled
    /// answer from an early feed round.
    int256 public constant PRICE_MAX = 1e13;

    IAggregatorV3 public immutable feed;

    mapping(uint40 obsTime => Fixing) public fixings;

    constructor(address feed_) {
        feed = IAggregatorV3(feed_);
    }

    function recordFixing(uint40 obsTime, uint80 roundId) external {
        // Strictly in the past: several blocks share a timestamp, so a round
        // with updatedAt == obsTime can still arrive after this call.
        if (obsTime >= block.timestamp) revert FutureObservation(obsTime);
        if (fixings[obsTime].timestamp != 0) revert AlreadyRecorded(obsTime);

        (int256 answer, uint40 updatedAt) = _round(roundId);
        if (answer <= 0 || answer > PRICE_MAX) revert BadPrice(answer);

        if (updatedAt <= obsTime) {
            // Case A: must be the last round at or before obsTime, and fresh enough.
            if (obsTime - updatedAt > MAX_FIX_AGE) revert FixingTooStale(updatedAt, obsTime);
            try feed.getRoundData(roundId + 1) returns (uint80, int256, uint256, uint256 nextUpdatedAt, uint80) {
                if (nextUpdatedAt <= obsTime) revert NotLastRoundBefore(roundId, obsTime);
            } catch {
                // no next round: only acceptable if this is the latest round
                if (roundId != feed.latestRound()) revert NotLastRoundBefore(roundId, obsTime);
            }
        } else {
            // Case B: rolled observation. Must be the first round after obsTime,
            // with a proven gap > MAX_FIX_AGE before it, within the MAX_ROLL cap.
            if (updatedAt - obsTime > MAX_ROLL) revert RollTooLong(updatedAt, obsTime);
            try feed.getRoundData(roundId - 1) returns (uint80, int256, uint256, uint256 prevUpdatedAt, uint80) {
                if (prevUpdatedAt > obsTime) revert NoGapProof(roundId, obsTime); // roundId - 1 is also after obsTime
                if (obsTime - prevUpdatedAt <= MAX_FIX_AGE) revert NoGapProof(roundId, obsTime); // no disruption
            } catch {
                // roundId - 1 doesn't exist: genesis round, the gap proof holds trivially
            }
        }

        // 0 < answer <= PRICE_MAX (1e13) fits uint96
        // forge-lint: disable-next-line(unsafe-typecast)
        uint96 price = uint96(uint256(answer));
        fixings[obsTime] = Fixing({timestamp: updatedAt, price: price, roundId: roundId});
        emit FixingRecorded(obsTime, roundId, price, updatedAt);
    }

    function fixingOf(uint40 obsTime) external view returns (Fixing memory) {
        return fixings[obsTime];
    }

    function isRecorded(uint40 obsTime) external view returns (bool) {
        return fixings[obsTime].timestamp != 0;
    }

    function _round(uint80 roundId) internal view returns (int256 answer, uint40 updatedAt) {
        uint256 u;
        // forge-lint: disable-next-line(unused-return)
        (, answer,, u,) = feed.getRoundData(roundId);
        // unix seconds fit uint40 until the year 36812
        // forge-lint: disable-next-line(unsafe-typecast)
        updatedAt = uint40(u);
    }
}
