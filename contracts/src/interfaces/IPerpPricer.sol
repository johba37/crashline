// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// Raw feature vector, featureSpecVersion 2 (docs/v2-spec.md §5). Field order =
/// model input order. Five positions in a repeating cycle: nothing counts
/// down to an end. The model accepts only its certified domain (per-field
/// ranges and excluded regions) and reverts outside it; it never clamps.
struct PerpPricerInputs {
    uint16 spotBpsOfReference; // floor(spot * 10_000 / reference)
    uint16 volBpsAnnual; // total implied vol, a Desk quoting parameter (no oracle)
    uint32 timeToNextFixingSecs;
    // Fixings before the stock's next earnings release; 0: it comes before or at the next
    // fixing. The earnings date is a Desk parameter like vol: price, never payout. A count,
    // not a time: where in a week the release falls doesn't change the value, but whether it
    // comes before or after a fixing does, by up to ~200 bps (docs/p1-perp-student.md).
    uint8 fixingsBeforeEarnings;
    uint8 flags; // bit0: knocked in
}

/// What a model is pinned to (inside its weightsHash). A series can be priced
/// by a model only if its barrier, melt share and interval are these. The
/// coupon reserve is not pinned: the note's price is linear in it, and the
/// model prices the principal only.
struct PerpProduct {
    uint16 kiBarrierBps;
    uint64 meltShare; // 1e18 fixed point
    uint32 fixingInterval; // seconds
    int16 driftBps; // risk-neutral drift of the stock per year (the closed form's r)
    int16 discountBps; // discount rate of the payouts per year (the closed form's rho; 0: the escrow earns nothing)
}

/// L0: the v2 student (stylus/pricer-model built from a featureSpecVersion 2
/// export), or PerpFormulaPricer (no student: correction 0). Pure: no storage,
/// no external calls, no block.timestamp. On-chain price = closed form
/// (PerpFormula, computed by PerpQuoter from `product()`'s rates) + this
/// model's correction: the student learns only what the formula gets wrong.
interface IPerpPricer {
    /// `field` (PerpPricerInputs index) is outside the certified range.
    error OutOfRange(uint8 field, int64 value);
    /// Inside excluded region `region` (e.g. next to the knock-in barrier on a fixing day).
    error Uncertified(uint8 region);

    /// Correction to the closed form's principal, in bps of live notional.
    function correctionBps(PerpPricerInputs calldata inputs) external view returns (int16);

    /// The same correction together with `product()` and `weightsHash()`: everything
    /// PerpQuoter needs for one quote in one call (entering a Stylus contract costs gas
    /// each time).
    function answer(PerpPricerInputs calldata inputs)
        external
        view
        returns (int16 correctionBps_, PerpProduct memory product_, bytes32 weightsHash_);

    /// Certified range of one input field.
    function certifiedRange(uint8 field) external view returns (int64 min, int64 max);

    function product() external view returns (PerpProduct memory);

    /// keccak256 of the canonical student_export.json (recomputed at build);
    /// pins the weights, the certified domain and the product.
    function weightsHash() external view returns (bytes32);

    function featureSpecVersion() external view returns (uint16);
}
