// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// Raw feature vector, featureSpecVersion 1. Field order = model input order.
/// Each model accepts only its certified domain (docs/model-export-format.md):
/// per-field ranges (min == max for pinned note terms), excluded regions such as
/// the autocall observation band, and exact derived fields. Outside it the
/// model reverts; it never clamps or extrapolates.
struct PricerInputs {
    uint16 spotBpsOfInitial; // spot / initialFixing * 10_000
    int32 distToKnockInBps; // (spot - knockInLevel) / initialFixing * 10_000, signed
    uint16 volBpsAnnual; // implied vol, a Desk quoting parameter (no oracle)
    uint16 kiBarrierBps; // knock-in barrier, bps of initial fixing
    uint16 acBarrierBps; // autocall barrier, bps of initial fixing
    uint16 couponBpsPerPeriod; // accrued per observation, bps of notional
    uint32 timeToMaturitySecs;
    uint32 timeToNextObsSecs;
    uint8 observationsRemaining;
    uint8 flags; // bit0: knocked in
}

// Index of each PricerInputs field: the `field` of OutOfRange, Inconsistent,
// certifiedRange and IDesk.ModelMismatch.
uint8 constant FIELD_SPOT = 0;
uint8 constant FIELD_DIST_TO_KNOCK_IN = 1;
uint8 constant FIELD_VOL = 2;
uint8 constant FIELD_KI_BARRIER = 3;
uint8 constant FIELD_AC_BARRIER = 4;
uint8 constant FIELD_COUPON = 5;
uint8 constant FIELD_TIME_TO_MATURITY = 6;
uint8 constant FIELD_TIME_TO_NEXT_OBS = 7;
uint8 constant FIELD_OBSERVATIONS_REMAINING = 8;
uint8 constant FIELD_FLAGS = 9;

/// L0: the Stylus student model (stylus/pricer-model). Pure: no storage, no
/// external calls, no block.timestamp. Weights compiled in, pinned by hash.
interface ISurrogatePricer {
    /// `field` (PricerInputs index) is outside the certified range.
    error OutOfRange(uint8 field, int64 value);
    /// Inside excluded region `region` (e.g. 0 = autocall band on observation day).
    error Uncertified(uint8 region);
    /// A derived field doesn't match: 1 = distToKnockInBps, 6 = timeToMaturitySecs.
    error Inconsistent(uint8 field);

    /// Clean fair value of 1 NOTE in bps of its 1-USDG notional: excludes coupon
    /// accrued before now (INoteQuoter adds it).
    function priceBps(PricerInputs calldata inputs) external view returns (uint16 priceBpsOfNotional);

    /// Certified range of one input field (min == max for a pinned note term).
    function certifiedRange(uint8 field) external view returns (int64 min, int64 max);

    /// keccak256 of the canonical student_export.json (recomputed at build).
    function weightsHash() external view returns (bytes32);

    function featureSpecVersion() external view returns (uint16);
}
