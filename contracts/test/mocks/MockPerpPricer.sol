// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IPerpPricer, PerpPricerInputs, PerpProduct} from "../../src/interfaces/IPerpPricer.sol";

/// Solidity stand-in for the Stylus v2 model in unit tests. Returns a set
/// correction (or a set refusal), exposes a settable certified range and
/// product, and can pin the exact input vector it expects (to prove what the
/// quoter sent).
contract MockPerpPricer is IPerpPricer {
    enum Mode {
        Correction,
        OutOfRangeErr,
        UncertifiedErr
    }

    int16 public correction;
    int16 public volSlope; // correction change in bps per 100 bps of vol above 5500
    Mode public mode;
    uint8 public errA;
    int64 public errB;
    bytes32 public expectedInputs; // 0 = accept any
    bytes32 public weightsHash = keccak256("mock perp model");
    PerpProduct internal _product;
    mapping(uint8 => int64) internal _min;
    mapping(uint8 => int64) internal _max;

    error UnexpectedInputs(PerpPricerInputs inputs);

    constructor() {
        // the P1 product and a P1-like domain
        _product = PerpProduct({
            kiBarrierBps: 6000,
            meltShare: 18_995_352_771_274_247,
            fixingInterval: 604_800,
            driftBps: 400,
            discountBps: 0
        });
        _setRange(0, 2000, 13000);
        _setRange(1, 2000, 9000);
        _setRange(2, 0, 604_800);
        _setRange(3, 0, 18);
        _setRange(4, 0, 1);
    }

    function _setRange(uint8 f, int64 lo, int64 hi) internal {
        _min[f] = lo;
        _max[f] = hi;
    }

    function setRange(uint8 f, int64 lo, int64 hi) external {
        _setRange(f, lo, hi);
    }

    function setProduct(PerpProduct calldata p) external {
        _product = p;
    }

    function setCorrection(int16 c) external {
        correction = c;
        mode = Mode.Correction;
    }

    /// A vol-sensitive model: correction + slope * (vol - 5500) / 100, refusing a vol outside its range.
    function setVolSlope(int16 slope) external {
        volSlope = slope;
    }

    function setRefusal(Mode m, uint8 a, int64 b) external {
        mode = m;
        errA = a;
        errB = b;
    }

    function expect(bytes32 inputsHash) external {
        expectedInputs = inputsHash;
    }

    function setWeightsHash(bytes32 h) external {
        weightsHash = h;
    }

    function answer(PerpPricerInputs calldata inputs) external view returns (int16, PerpProduct memory, bytes32) {
        return (correctionBps(inputs), _product, weightsHash);
    }

    function correctionBps(PerpPricerInputs calldata inputs) public view returns (int16) {
        if (expectedInputs != bytes32(0) && keccak256(abi.encode(inputs)) != expectedInputs) {
            revert UnexpectedInputs(inputs);
        }
        if (mode == Mode.OutOfRangeErr) revert OutOfRange(errA, errB);
        if (mode == Mode.UncertifiedErr) revert Uncertified(errA);
        int64 vol = int64(uint64(inputs.volBpsAnnual));
        if (vol < _min[1] || vol > _max[1]) revert OutOfRange(1, vol);
        return correction + int16(int256(volSlope) * (int256(vol) - 5500) / 100);
    }

    function certifiedRange(uint8 field) external view returns (int64, int64) {
        if (field > 4) revert OutOfRange(field, int64(uint64(field)));
        return (_min[field], _max[field]);
    }

    function product() external view returns (PerpProduct memory) {
        return _product;
    }

    function featureSpecVersion() external pure returns (uint16) {
        return 2;
    }
}
