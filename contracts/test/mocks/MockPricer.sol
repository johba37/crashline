// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ISurrogatePricer, PricerInputs} from "../../src/interfaces/ISurrogatePricer.sol";

/// Solidity stand-in for the Stylus model in unit tests. Returns a set clean
/// price (or a set refusal), exposes a settable certified range, and can pin
/// the exact input vector it expects (to prove what the quoter sent).
contract MockPricer is ISurrogatePricer {
    enum Mode {
        Price,
        OutOfRangeErr,
        InconsistentErr,
        UncertifiedErr
    }

    uint16 public price = 9500;
    Mode public mode;
    uint8 public errA;
    int64 public errB;
    bytes32 public expectedInputs; // 0 = accept any
    bytes32 public weightsHash = keccak256("mock model");
    mapping(uint8 => int64) internal _min;
    mapping(uint8 => int64) internal _max;

    error UnexpectedInputs(PricerInputs inputs);

    constructor() {
        // the K1 product, observationsRemaining 1..26 (a k2-like domain)
        _setRange(0, 5000, 12000);
        _setRange(1, -1000, 6000);
        _setRange(2, 5500, 5500);
        _setRange(3, 6000, 6000);
        _setRange(4, 10000, 10000);
        _setRange(5, 25, 25);
        _setRange(6, 604800, 16329600);
        _setRange(7, 0, 604800);
        _setRange(8, 1, 26);
        _setRange(9, 0, 1);
    }

    function _setRange(uint8 f, int64 lo, int64 hi) internal {
        _min[f] = lo;
        _max[f] = hi;
    }

    function setRange(uint8 f, int64 lo, int64 hi) external {
        _setRange(f, lo, hi);
    }

    function setPrice(uint16 p) external {
        price = p;
        mode = Mode.Price;
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

    function priceBps(PricerInputs calldata inputs) external view returns (uint16) {
        if (expectedInputs != bytes32(0) && keccak256(abi.encode(inputs)) != expectedInputs) {
            revert UnexpectedInputs(inputs);
        }
        if (mode == Mode.OutOfRangeErr) revert OutOfRange(errA, errB);
        if (mode == Mode.InconsistentErr) revert Inconsistent(errA);
        if (mode == Mode.UncertifiedErr) revert Uncertified(errA);
        return price;
    }

    function certifiedRange(uint8 field) external view returns (int64, int64) {
        if (field > 9) revert OutOfRange(field, int64(uint64(field)));
        return (_min[field], _max[field]);
    }

    function featureSpecVersion() external pure returns (uint16) {
        return 1;
    }
}
