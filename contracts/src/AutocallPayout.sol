// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Phase} from "./interfaces/INoteSeries.sol";

/// Pure payout rule of the autocallable note, exactly the normative comment in
/// INoteSeries.sol (and the instrument ml/teacher.py prices):
///   barrier observations i = 1..N at strikeTime + i * I, maturity fixing M at
///   strikeTime + (N + 1) * I
///   knock-in:  any observation fixing_i < ki * initial (latching; not checked at M)
///   autocall:  first i with fixing_i >= ac * initial  ->  1 + c * i, settle at i
///   maturity:  knocked in and fixing_M < initial      ->  fixing_M / initial + c * (N + 1)
///              otherwise                              ->  1 + c * (N + 1)
/// Barrier comparisons are exact integer cross-multiplications (bps of the
/// initial fixing, no division). Payouts are USDG base units per NOTE
/// (1 NOTE = 1e6 base units of notional); the only division, fixing_M /
/// initial, rounds down. The Python twin is tools/payout_vectors.py; the
/// forge test PayoutVectors.t.sol replays its vectors through this library and
/// through a live series.
library AutocallPayout {
    uint256 internal constant BPS = 10_000;
    uint256 internal constant UNIT = 1e6; // base units per 1 NOTE of notional
    uint256 internal constant UNIT_PER_BPS = UNIT / BPS; // 100

    /// Everything a series needs to know about its path so far.
    struct Progress {
        Phase phase;
        uint96 initialFixing; // 0 while Pending
        uint96 lastFixing; // the fixing a fallback reuses (initial before obs 1)
        uint8 observationsDone; // processed barrier observations, 0..N
        bool knockedIn;
        bool autocalled;
        uint8 settledAt; // i of the autocall, N + 1 at maturity, 0 while not settled
        uint128 payoutPerNote; // 0 until Settled
    }

    /// 1 + c * (N + 1) in base units: what one NOTE + WRITER pair locks.
    function maxPayoutPerNote(uint16 couponBpsPerPeriod, uint8 observationCount) internal pure returns (uint128) {
        return uint128(UNIT + couponUnits(couponBpsPerPeriod, uint256(observationCount) + 1));
    }

    /// c * periods in base units per NOTE (exact: c is in bps, 1 bps = 100 units).
    function couponUnits(uint16 couponBpsPerPeriod, uint256 periods) internal pure returns (uint256) {
        return uint256(couponBpsPerPeriod) * periods * UNIT_PER_BPS;
    }

    /// Record the strike (initial) fixing: Pending -> Live.
    function strike(Progress memory p, uint96 initialFixing) internal pure {
        p.phase = Phase.Live;
        p.initialFixing = initialFixing;
        p.lastFixing = initialFixing;
    }

    /// Index of the next fixing of a Live note: 1..N a barrier observation,
    /// N + 1 the maturity fixing.
    function nextIndex(Progress memory p) internal pure returns (uint8) {
        return p.observationsDone + 1;
    }

    /// Apply the next fixing (index nextIndex(p)) to a Live note. Settles on an
    /// autocall or at maturity. Returns true if the note settled.
    function observe(
        Progress memory p,
        uint16 kiBarrierBps,
        uint16 acBarrierBps,
        uint16 couponBpsPerPeriod,
        uint8 observationCount,
        uint96 fixing
    ) internal pure returns (bool settled) {
        uint8 i = p.observationsDone + 1;
        uint256 scaledFixing = uint256(fixing) * BPS;
        p.lastFixing = fixing;
        if (i <= observationCount) {
            p.observationsDone = i;
            if (scaledFixing >= uint256(acBarrierBps) * p.initialFixing) {
                p.autocalled = true;
                _settle(p, i, UNIT + couponUnits(couponBpsPerPeriod, i));
                return true;
            }
            if (scaledFixing < uint256(kiBarrierBps) * p.initialFixing) p.knockedIn = true;
            return false;
        }
        // maturity fixing, one interval after the last barrier observation
        uint256 coupons = couponUnits(couponBpsPerPeriod, uint256(observationCount) + 1);
        uint256 payout = p.knockedIn && fixing < p.initialFixing
            ? uint256(fixing) * UNIT / p.initialFixing + coupons
            : UNIT + coupons;
        _settle(p, i, payout);
        return true;
    }

    function _settle(Progress memory p, uint8 at, uint256 payout) private pure {
        p.phase = Phase.Settled;
        p.settledAt = at;
        p.payoutPerNote = uint128(payout);
    }
}
