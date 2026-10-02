// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {PerpPhase} from "./interfaces/IPerpSeries.sol";

/// Pure fixing rule of the v2 perpetual note, exactly the normative comment in
/// IPerpSeries.sol and docs/v2-spec.md §2:
///   first fixing:  H = f
///   fixing f:      1. f >= H          ->  H = f, knockedIn = false
///                  2. else f < k * H  ->  knockedIn = true
///                  3. m = floor(s * a / 1e18) of the live notional s melts and pays
///                     NOTE   floor(m * (1 + R))               if clean
///                            floor(m * (f / H + R))           if knocked in
///                     WRITER the rest of floor(m * (1 + R))
/// Barrier comparisons are exact integer cross-multiplications. Amounts are
/// per token base unit in 1e27 fixed point: NOTE rounds down, WRITER takes
/// what is left of the pair's release, and the pair's release rounds down, so
/// a fixing never releases more than the pair value it removes. The Python
/// twin is tools/perp_vectors.py; PerpVectors.t.sol replays its vectors
/// through this library and through a live series.
library PerpPayout {
    uint256 internal constant BPS = 10_000;
    uint256 internal constant UNIT = 1e6; // base units per 1 USDG of notional
    uint256 internal constant WAD = 1e18; // meltShare
    uint256 internal constant RAY = 1e27; // notional per token, indexes

    /// Everything a series needs to know about its path so far (3 slots).
    struct Progress {
        PerpPhase phase;
        bool knockedIn;
        uint8 missedInARow;
        uint32 fixingsDone; // processed fixings after the first
        uint96 referenceFixing; // H; 0 while Pending
        uint96 lastFixing; // what a missed fixing reuses
        uint128 notionalPerToken; // s, RAY; RAY at the first fixing
        uint128 noteIndex; // RAY; <= RAY * (1 + R / UNIT), which fits (factory bound on R)
        uint128 writerIndex; // RAY; <= RAY
    }

    /// Record the first fixing: Pending -> Live.
    function strike(Progress memory p, uint96 fixing) internal pure {
        p.phase = PerpPhase.Live;
        p.referenceFixing = fixing;
        p.lastFixing = fixing;
        // forge-lint: disable-next-line(unsafe-typecast)
        p.notionalPerToken = uint128(RAY);
    }

    /// Apply the next fixing to a Live note. `missed`: no price was recorded
    /// and `fixing` is the last one; the `closeAfter`-th miss in a row melts
    /// everything and closes the note. Returns the USDG released per token (RAY).
    function fix(
        Progress memory p,
        uint16 kiBarrierBps,
        uint64 meltShare,
        uint64 couponReserve,
        uint8 closeAfter,
        uint96 fixing,
        bool missed
    ) internal pure returns (uint256 noteRelease, uint256 writerRelease) {
        uint256 h = p.referenceFixing;
        if (fixing >= h) {
            h = fixing;
            p.referenceFixing = fixing;
            p.knockedIn = false;
        } else if (uint256(fixing) * BPS < uint256(kiBarrierBps) * h) {
            p.knockedIn = true;
        }
        p.lastFixing = fixing;
        p.fixingsDone += 1;

        bool close = false;
        if (missed) {
            p.missedInARow += 1;
            close = p.missedInARow >= closeAfter;
        } else {
            p.missedInARow = 0;
        }

        uint256 s = p.notionalPerToken;
        uint256 m = close ? s : Math.mulDiv(s, meltShare, WAD);
        // m <= 1e27, R <= 5e6 (factory bound), fixing and h <= 1e13 (recorder bound): no overflow
        uint256 total = m * (UNIT + couponReserve) / UNIT;
        noteRelease = p.knockedIn ? m * (uint256(fixing) * UNIT + uint256(couponReserve) * h) / (h * UNIT) : total;
        writerRelease = total - noteRelease; // knocked in means fixing < h, so noteRelease <= total

        // every cast below is bounded as in the struct comments
        // forge-lint: disable-start(unsafe-typecast)
        p.notionalPerToken = uint128(s - m);
        p.noteIndex += uint128(noteRelease);
        p.writerIndex += uint128(writerRelease);
        // forge-lint: disable-end(unsafe-typecast)
        if (close) p.phase = PerpPhase.Closed;
    }

    /// USDG a pair of `amount` tokens is worth at notional-per-token `s`: amount * s * (1 + R).
    function pairValue(uint256 amount, uint256 s, uint64 couponReserve, Math.Rounding rounding)
        internal
        pure
        returns (uint256)
    {
        return Math.mulDiv(amount, s * (UNIT + couponReserve), RAY * UNIT, rounding);
    }
}
