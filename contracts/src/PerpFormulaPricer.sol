// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IPerpPricer, PerpPricerInputs, PerpProduct} from "./interfaces/IPerpPricer.sol";
import {PerpFormula} from "./PerpFormula.sol";

/// The pricer with no student: PerpQuoter's price is the closed form
/// (PerpFormula) at this contract's pinned rates, plus the two things about
/// weekly fixings the formula leaves out and no model is needed for:
///
/// Accrual. The formula melts the notional continuously; the note releases a
/// share `a` in one step at each fixing. A week's release builds up in the
/// price and leaves it at the fixing, so this pricer's "correction" is
///
///   correctionBps = a * elapsed / interval * (p - formula)
///
/// with elapsed = interval - timeToNextFixing and p = what the melting slice
/// would pay if the fixing were now (1 if clean and at or above the barrier,
/// else the spot over the reference, at most 1). At the fixing itself that is
/// the release, a * p, plus (1 - a) of the formula: with an unchanged spot the
/// price does not step when the fixing is processed, so nobody gains by
/// trading, depositing or withdrawing around it.
///
/// Refusals. In the last `fixingBandSecs` before a fixing the value steps at
/// the knock-in barrier (a clean note) and at the reference (a knocked-in
/// note): 192-447 bps and 88-134 bps in the teacher (docs/p1-perp-student.md).
/// No formula follows a step, so inside `knockInBandBps` / `healBandBps` of
/// those levels this pricer refuses, like model/p1 (`Uncertified(0)` / `(1)`).
/// It also refuses a vol outside the range it was built for; the constructor
/// proves the closed form can carry both ends of that range.
///
/// What it cannot know is what the formula gets wrong elsewhere (mid-week
/// states near the barriers, jumps, earnings: up to ~270 bps, 13.5 on
/// average); a Desk listing that uses it should carry a wider spread.
contract PerpFormulaPricer is IPerpPricer {
    uint256 internal constant BPS = 10_000;
    uint256 internal constant WAD = 1e18;
    uint256 internal constant WAD_PER_BPS = WAD / BPS;
    int256 internal constant SIGNED_WAD_PER_BPS = 1e14;
    uint256 internal constant YEAR = 31_536_000;
    uint8 internal constant NUM_FIELDS = 5;

    /// Where this pricer answers.
    struct Bands {
        uint32 fixingBandSecs; // the two refusals apply this long before a fixing (0: never)
        uint16 knockInBandBps; // spot within this of the knock-in barrier, note not knocked in
        uint16 healBandBps; // spot within this of the reference, note knocked in
        uint16 volMinBps; // certified vol range, both ends carried by the closed form
        uint16 volMaxBps;
    }

    PerpProduct internal _product;
    Bands internal _bands;
    /// keccak256("PerpFormulaPricer", product, bands): there are no weights; the hash names
    /// the product, the rates and where the pricer answers.
    bytes32 public immutable weightsHash;

    error BadBands();

    constructor(PerpProduct memory product_, Bands memory bands_) {
        if (bands_.volMinBps > bands_.volMaxBps || bands_.fixingBandSecs > product_.fixingInterval) revert BadBands();
        _product = product_;
        _bands = bands_;
        weightsHash = keccak256(abi.encode("PerpFormulaPricer", product_, bands_));
        // the closed form refuses low vols (PerpFormula.BETA_MAX) and bad worlds: fail here, not in a quote
        PerpFormula.coefficients(_world(product_, bands_.volMinBps));
        PerpFormula.coefficients(_world(product_, bands_.volMaxBps));
    }

    function correctionBps(PerpPricerInputs calldata inputs) public view returns (int16) {
        PerpProduct memory p = _product;
        Bands memory b = _bands;
        uint256 x = inputs.spotBpsOfReference;
        bool knockedIn = inputs.flags != 0;
        // certified domain, in the order every model checks it: ranges by field, then exclusions
        if (inputs.volBpsAnnual < b.volMinBps || inputs.volBpsAnnual > b.volMaxBps) {
            revert OutOfRange(1, int64(uint64(inputs.volBpsAnnual)));
        }
        if (inputs.timeToNextFixingSecs > p.fixingInterval) {
            revert OutOfRange(2, int64(uint64(inputs.timeToNextFixingSecs)));
        }
        if (inputs.flags > 1) revert OutOfRange(4, int64(uint64(inputs.flags)));
        if (inputs.timeToNextFixingSecs <= b.fixingBandSecs && b.fixingBandSecs != 0) {
            if (!knockedIn && _within(x, p.kiBarrierBps, b.knockInBandBps)) revert Uncertified(0);
            if (knockedIn && _within(x, BPS, b.healBandBps)) revert Uncertified(1);
        }

        uint256 formulaBps =
            (PerpFormula.principal(_world(p, inputs.volBpsAnnual), x * WAD_PER_BPS, knockedIn) * BPS + WAD / 2) / WAD;
        // what the melting slice would pay if the fixing were now
        uint256 payBps = (knockedIn || x < p.kiBarrierBps) && x < BPS ? x : BPS;
        uint256 elapsed = uint256(p.fixingInterval) - inputs.timeToNextFixingSecs;
        // every factor is far below 2^64: no overflow; |result| <= 10_000
        // forge-lint: disable-start(unsafe-typecast)
        int256 accrual = int256(uint256(p.meltShare) * elapsed) * (int256(payBps) - int256(formulaBps))
            / int256(WAD * uint256(p.fixingInterval));
        return int16(accrual);
        // forge-lint: disable-end(unsafe-typecast)
    }

    function answer(PerpPricerInputs calldata inputs) external view returns (int16, PerpProduct memory, bytes32) {
        return (correctionBps(inputs), _product, weightsHash);
    }

    /// Spot and the earnings count: the whole range of the type (the formula knows no earnings).
    function certifiedRange(uint8 field) external view returns (int64 min, int64 max) {
        if (field >= NUM_FIELDS) revert OutOfRange(field, int64(uint64(field)));
        if (field == 0) return (0, int64(uint64(type(uint16).max)));
        if (field == 1) return (int64(uint64(_bands.volMinBps)), int64(uint64(_bands.volMaxBps)));
        if (field == 2) return (0, int64(uint64(_product.fixingInterval)));
        if (field == 3) return (0, int64(uint64(type(uint8).max)));
        return (0, 1);
    }

    function product() external view returns (PerpProduct memory) {
        return _product;
    }

    function bands() external view returns (Bands memory) {
        return _bands;
    }

    function featureSpecVersion() external pure returns (uint16) {
        return 2;
    }

    function _within(uint256 x, uint256 level, uint256 band) internal pure returns (bool) {
        return x + band >= level && x <= level + band;
    }

    /// The closed form's world for product `p` at `volBps`, exactly as PerpQuoter builds it.
    function _world(PerpProduct memory p, uint256 volBps) internal pure returns (PerpFormula.World memory) {
        uint256 dt = uint256(p.fixingInterval) * WAD / YEAR;
        return PerpFormula.World({
            sigma: volBps * WAD_PER_BPS,
            r: int256(p.driftBps) * SIGNED_WAD_PER_BPS,
            rho: int256(p.discountBps) * SIGNED_WAD_PER_BPS,
            phi: PerpFormula.meltRate(p.meltShare, dt),
            k: uint256(p.kiBarrierBps) * WAD_PER_BPS,
            dt: dt
        });
    }
}
