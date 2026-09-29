// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {SafeCast} from "@openzeppelin/contracts/utils/math/SafeCast.sol";
import {Base} from "./Base.t.sol";
import {MockPricer} from "./mocks/MockPricer.sol";
import {NoteQuoter} from "../src/NoteQuoter.sol";
import {NoteSeries} from "../src/NoteSeries.sol";
import {INoteQuoter} from "../src/interfaces/INoteQuoter.sol";
import {INoteSeries} from "../src/interfaces/INoteSeries.sol";
import {ISurrogatePricer, PricerInputs} from "../src/interfaces/ISurrogatePricer.sol";

contract NoteQuoterTest is Base {
    uint16 constant VOL = 5500;
    NoteQuoter quoter;
    MockPricer pricer;
    NoteSeries s;
    uint40 strikeTime;

    function setUp() public override {
        super.setUp();
        quoter = new NoteQuoter(26 hours);
        pricer = new MockPricer();
        strikeTime = T0 + 1 hours;
        s = _create(_terms(strikeTime));
    }

    /// Strike at INITIAL, then `done` observations at 80% (or 50% at obs 1 when
    /// knocked in), then `now` = strike + secsSinceStrike with a fresh round at `spot`.
    function _stage(uint96 initial, uint256 spot, uint256 done, bool knockedIn, uint256 secsSinceStrike) internal {
        _fixAt(s, strikeTime, int256(uint256(initial)));
        for (uint256 i = 1; i <= done; i++) {
            uint256 bps = (knockedIn && i == 1) ? 5000 : 8000;
            _fixAt(s, _obsTime(s, i), int256(uint256(initial) * bps / 10_000));
        }
        vm.warp(uint256(strikeTime) + secsSinceStrike);
        feed.pushRoundAt(int256(spot), uint40(block.timestamp));
    }

    function _inputs(int256[] memory v) internal pure returns (PricerInputs memory in_) {
        in_.spotBpsOfInitial = uint16(uint256(v[0]));
        in_.distToKnockInBps = int32(v[1]);
        in_.volBpsAnnual = uint16(uint256(v[2]));
        in_.kiBarrierBps = uint16(uint256(v[3]));
        in_.acBarrierBps = uint16(uint256(v[4]));
        in_.couponBpsPerPeriod = uint16(uint256(v[5]));
        in_.timeToMaturitySecs = uint32(uint256(v[6]));
        in_.timeToNextObsSecs = uint32(uint256(v[7]));
        in_.observationsRemaining = uint8(uint256(v[8]));
        in_.flags = uint8(uint256(v[9]));
    }

    // --- conformity with tools/quoter_vectors.py (pricer_quant twin) ----------------

    function test_quoter_vectors() public {
        string memory json = vm.readFile(string.concat(vm.projectRoot(), "/test/vectors/quoter_vectors.json"));
        uint256 count = vm.parseJsonUint(json, ".count");
        assertGe(count, 10);
        for (uint256 i = 0; i < count; i++) {
            string memory k = string.concat(".vectors[", vm.toString(i), "]");
            string memory label = vm.parseJsonString(json, string.concat(k, ".label"));
            uint256 snap = vm.snapshotState();
            _stage(
                uint96(vm.parseJsonUint(json, string.concat(k, ".initial"))),
                vm.parseJsonUint(json, string.concat(k, ".spot")),
                vm.parseJsonUint(json, string.concat(k, ".observationsDone")),
                vm.parseJsonUint(json, string.concat(k, ".knockedIn")) == 1,
                vm.parseJsonUint(json, string.concat(k, ".secsSinceStrike"))
            );
            // distToKnockInBps may be negative: parse the array as signed
            PricerInputs memory want = _inputs(vm.parseJsonIntArray(json, string.concat(k, ".inputs")));
            PricerInputs memory got = quoter.inputs(s, VOL);
            assertEq(keccak256(abi.encode(got)), keccak256(abi.encode(want)), label);
            assertEq(int256(got.distToKnockInBps), int256(uint256(got.spotBpsOfInitial)) - 6000, label);

            pricer.expect(keccak256(abi.encode(want)));
            uint256 result = vm.parseJsonUint(json, string.concat(k, ".result"));
            uint256 a = vm.parseJsonUint(json, string.concat(k, ".a"));
            if (result == 0) {
                pricer.setPrice(uint16(a));
                (uint16 p, bytes32 h) = quoter.notePriceBps(s, pricer, VOL);
                assertEq(p, vm.parseJsonUint(json, string.concat(k, ".notePriceBps")), label);
                assertEq(h, pricer.weightsHash());
            } else {
                int256 b = vm.parseJsonInt(json, string.concat(k, ".b"));
                pricer.setRefusal(MockPricer.Mode(result), uint8(a), int64(b));
                if (result == 1) {
                    vm.expectRevert(abi.encodeWithSelector(ISurrogatePricer.OutOfRange.selector, uint8(a), int64(b)));
                } else if (result == 2) {
                    vm.expectRevert(abi.encodeWithSelector(ISurrogatePricer.Inconsistent.selector, uint8(a)));
                } else {
                    vm.expectRevert(abi.encodeWithSelector(ISurrogatePricer.Uncertified.selector, uint8(a)));
                }
                quoter.notePriceBps(s, pricer, VOL);
            }
            pricer.expect(bytes32(0));
            vm.revertToState(snap);
        }
    }

    // --- errors -------------------------------------------------------------------

    function test_NotLive_before_strike_and_after_settlement() public {
        vm.expectRevert(INoteQuoter.NotLive.selector);
        quoter.inputs(s, VOL);
        _stage(INITIAL, 100e8, 0, false, 3 days);
        quoter.inputs(s, VOL);
        _fix(s, 1, 10000); // autocall, recorded but not advanced: still NotLive
        feed.pushRoundAt(100e8, uint40(block.timestamp));
        vm.expectRevert(INoteQuoter.NotLive.selector);
        quoter.notePriceBps(s, pricer, VOL);
    }

    function test_FixingPending() public {
        _stage(INITIAL, 90e8, 0, false, 3 days);
        vm.warp(uint256(_obsTime(s, 1)) + 1);
        feed.pushRoundAt(90e8, uint40(block.timestamp));
        vm.expectRevert(abi.encodeWithSelector(INoteQuoter.FixingPending.selector, _obsTime(s, 1)));
        quoter.notePriceBps(s, pricer, VOL);
    }

    function test_FeedStale_weekend() public {
        _stage(INITIAL, 90e8, 0, false, 1 days);
        uint40 friday = uint40(block.timestamp);
        quoter.notePriceBps(s, pricer, VOL);
        vm.warp(uint256(friday) + 26 hours);
        quoter.notePriceBps(s, pricer, VOL); // exactly at the limit: still fresh
        vm.warp(uint256(friday) + 26 hours + 1);
        vm.expectRevert(abi.encodeWithSelector(INoteQuoter.FeedStale.selector, friday));
        quoter.notePriceBps(s, pricer, VOL);
        assertEq(quoter.MAX_FEED_STALENESS(), 26 hours);
    }

    function test_BadFeedAnswer() public {
        _stage(INITIAL, 90e8, 0, false, 1 days);
        vm.warp(block.timestamp + 1);
        feed.pushRoundAt(0, uint40(block.timestamp));
        vm.expectRevert(INoteQuoter.BadFeedAnswer.selector);
        quoter.inputs(s, VOL);
        vm.warp(block.timestamp + 1);
        feed.pushRoundAt(-1, uint40(block.timestamp));
        vm.expectRevert(INoteQuoter.BadFeedAnswer.selector);
        quoter.inputs(s, VOL);
    }

    function test_spot_beyond_uint16_reverts_OutOfRange() public {
        _stage(INITIAL, 700e8, 0, false, 1 days); // 7x initial = 70_000 bps
        vm.expectRevert(abi.encodeWithSelector(ISurrogatePricer.OutOfRange.selector, uint8(0), int64(70_000)));
        quoter.inputs(s, VOL);
    }

    function test_model_refusals_bubble_up() public {
        _stage(INITIAL, 90e8, 0, false, 1 days);
        pricer.setRefusal(MockPricer.Mode.InconsistentErr, 6, 0);
        vm.expectRevert(abi.encodeWithSelector(ISurrogatePricer.Inconsistent.selector, uint8(6)));
        quoter.notePriceBps(s, pricer, VOL);
        pricer.setRefusal(MockPricer.Mode.UncertifiedErr, 0, 0);
        vm.expectRevert(abi.encodeWithSelector(ISurrogatePricer.Uncertified.selector, uint8(0)));
        quoter.notePriceBps(s, pricer, VOL);
    }

    function test_price_overflow_reverts() public {
        _stage(INITIAL, 90e8, 2, false, 3 weeks - 1);
        pricer.setPrice(type(uint16).max);
        uint256 accrued = 25 * (3 * uint256(WEEK) - 1) / WEEK; // 74
        vm.expectRevert(abi.encodeWithSelector(SafeCast.SafeCastOverflowedUintDowncast.selector, 16, 65_535 + accrued));
        quoter.notePriceBps(s, pricer, VOL);
    }

    function test_final_period_has_zero_observations_remaining() public {
        _stage(INITIAL, 90e8, 26, false, 26 * uint256(WEEK) + 3600);
        PricerInputs memory in_ = quoter.inputs(s, VOL);
        assertEq(in_.observationsRemaining, 0);
        assertEq(in_.timeToMaturitySecs, in_.timeToNextObsSecs);
        assertEq(in_.timeToNextObsSecs, WEEK - 3600);
    }

    // --- fuzz ---------------------------------------------------------------------

    /// Derived fields are exact and the accrued coupon is floor(c * elapsed / I),
    /// anywhere in the life of the note.
    function testFuzz_inputs_and_accrual(uint256 secs, uint256 spotBps, uint16 clean) public {
        uint256 done = bound(secs, 0, 26 * uint256(WEEK) - 1) / WEEK;
        secs = done * WEEK + bound(secs, 1, WEEK);
        spotBps = bound(spotBps, 6001, 9999); // no knock-in, no autocall at the staged 80% fixings
        clean = uint16(bound(clean, 0, 60_000));
        _stage(INITIAL, uint256(INITIAL) * spotBps / 10_000, done, false, secs);
        PricerInputs memory in_ = quoter.inputs(s, VOL);
        assertEq(in_.spotBpsOfInitial, spotBps);
        assertEq(int256(in_.distToKnockInBps), int256(spotBps) - 6000);
        assertEq(in_.observationsRemaining, 26 - done);
        assertEq(in_.timeToNextObsSecs, (done + 1) * WEEK - secs);
        assertEq(in_.timeToMaturitySecs, uint256(in_.timeToNextObsSecs) + uint256(26 - done) * WEEK);
        assertEq(in_.flags, 0);
        pricer.setPrice(clean);
        (uint16 p,) = quoter.notePriceBps(s, pricer, VOL);
        assertEq(p, uint256(clean) + 25 * secs / WEEK);
    }
}
