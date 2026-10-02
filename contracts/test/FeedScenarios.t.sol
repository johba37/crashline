// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {MockChainlinkFeed} from "../src/mocks/MockChainlinkFeed.sol";
import {FixingsRecorder} from "../src/FixingsRecorder.sol";
import {IFixingsRecorder} from "../src/interfaces/IFixingsRecorder.sol";

contract FeedScenariosTest is Test {
    uint80 constant R1 = uint80(1) << 64 | 1; // 2^64 + 1
    int256 constant PRICE = 354_28_499_999; // $354.28 @ 8dec

    MockChainlinkFeed feed;
    FixingsRecorder recorder;

    function setUp() public {
        vm.warp(1_790_699_124); // 2026-09-29 16:25 UTC, matches the probed fork
        feed = new MockChainlinkFeed("RHTSLA / USD");
        recorder = new FixingsRecorder(address(feed));
    }

    // --- Case A: normal observation -------------------------------------

    function test_caseA_normal_week() public {
        feed.pushRound(PRICE); // R1 at t0
        vm.warp(block.timestamp + 30 minutes);
        feed.pushRound(PRICE + 1e8); // R2
        uint40 obsTime = uint40(block.timestamp) - 10 minutes; // between R1 and R2

        recorder.recordFixing(obsTime, R1);
        (uint40 ts, uint96 price, uint80 rid) = recorder.fixings(obsTime);
        assertEq(ts, 1_790_699_124);
        assertEq(price, uint96(uint256(PRICE)));
        assertEq(rid, R1);
        assertTrue(recorder.isRecorded(obsTime));
    }

    function test_caseA_rejects_older_round() public {
        feed.pushRound(PRICE); // R1
        vm.warp(block.timestamp + 30 minutes);
        feed.pushRound(PRICE); // R2
        uint40 obsTime = uint40(block.timestamp); // at/after R2
        vm.warp(obsTime + 1);
        // R1 is not the last round at-or-before obsTime — R2 is
        vm.expectRevert(abi.encodeWithSelector(IFixingsRecorder.NotLastRoundBefore.selector, R1, obsTime));
        recorder.recordFixing(obsTime, R1);
    }

    function test_already_recorded() public {
        feed.pushRound(PRICE);
        uint40 obsTime = uint40(block.timestamp);
        vm.warp(obsTime + 1);
        recorder.recordFixing(obsTime, R1);
        vm.expectRevert(abi.encodeWithSelector(IFixingsRecorder.AlreadyRecorded.selector, obsTime));
        recorder.recordFixing(obsTime, R1);
    }

    function test_bad_price_anomaly_guard() public {
        feed.pushRound(int256(3.96e18)); // the real feed's round-1 scale artifact
        uint40 obsTime = uint40(block.timestamp);
        vm.warp(obsTime + 1);
        vm.expectRevert(abi.encodeWithSelector(IFixingsRecorder.BadPrice.selector, int256(3.96e18)));
        recorder.recordFixing(obsTime, R1);
    }

    function test_bad_price_non_positive() public {
        feed.pushRound(0);
        uint40 obsTime = uint40(block.timestamp);
        vm.warp(obsTime + 1);
        vm.expectRevert(abi.encodeWithSelector(IFixingsRecorder.BadPrice.selector, int256(0)));
        recorder.recordFixing(obsTime, R1);
    }

    function test_future_observation_rejected() public {
        feed.pushRound(PRICE);
        uint40 obsTime = uint40(block.timestamp);
        // obsTime == now is still "future": a later block in the same second
        // could add a round with updatedAt == obsTime.
        vm.expectRevert(abi.encodeWithSelector(IFixingsRecorder.FutureObservation.selector, obsTime));
        recorder.recordFixing(obsTime, R1);
        vm.expectRevert(abi.encodeWithSelector(IFixingsRecorder.FutureObservation.selector, obsTime + 1));
        recorder.recordFixing(obsTime + 1, R1);
    }

    function test_caseA_too_stale() public {
        feed.pushRound(PRICE);
        uint40 obsTime = uint40(block.timestamp) + 96 hours + 1;
        vm.warp(obsTime + 1);
        vm.expectRevert(
            abi.encodeWithSelector(IFixingsRecorder.FixingTooStale.selector, uint40(1_790_699_124), obsTime)
        );
        recorder.recordFixing(obsTime, R1);
    }

    // --- Case B: halted feed, observation rolls to first fresh round ------

    function test_caseB_disrupted_day_roll() public {
        feed.pushRound(PRICE); // R1: Friday close
        uint40 obsTime = uint40(block.timestamp) + 7 days; // next Friday observation
        vm.warp(obsTime + 100 hours); // feed was halted >96h across the observation
        feed.pushRound(PRICE + 2e8); // R2: first round after the halt

        // the stale R1 is rejected for this observation
        vm.expectRevert(
            abi.encodeWithSelector(IFixingsRecorder.FixingTooStale.selector, uint40(1_790_699_124), obsTime)
        );
        recorder.recordFixing(obsTime, R1);

        // R2 is accepted as the rolled fixing
        recorder.recordFixing(obsTime, R1 + 1);
        (uint40 ts,, uint80 rid) = recorder.fixings(obsTime);
        assertEq(ts, obsTime + 100 hours);
        assertEq(rid, R1 + 1);
    }

    function test_caseB_rejects_beyond_hard_cap() public {
        feed.pushRound(PRICE);
        uint40 obsTime = uint40(block.timestamp) + 7 days;
        vm.warp(obsTime + 9 days); // dead > 8 days: vault-level settlement territory
        feed.pushRound(PRICE);
        vm.expectRevert(
            abi.encodeWithSelector(IFixingsRecorder.RollTooLong.selector, uint40(obsTime + 9 days), obsTime)
        );
        recorder.recordFixing(obsTime, R1 + 1);
    }

    function test_caseB_rejects_roll_without_gap() public {
        feed.pushRound(PRICE);
        uint40 obsTime = uint40(block.timestamp) + 30 minutes;
        vm.warp(obsTime + 10 minutes); // short gap, well under 96h
        feed.pushRound(PRICE);
        // R2 is after obsTime, but R1 proves there was NO disruption
        vm.expectRevert(abi.encodeWithSelector(IFixingsRecorder.NoGapProof.selector, R1 + 1, obsTime));
        recorder.recordFixing(obsTime, R1 + 1);
    }

    // --- recorder reads a backdated staged round correctly -----------------

    function test_staged_backdated_round() public {
        feed.pushRoundAt(PRICE, uint40(1_790_600_000)); // staged "yesterday"
        recorder.recordFixing(uint40(1_790_600_000), R1);
        assertTrue(recorder.isRecorded(uint40(1_790_600_000)));
    }

    function test_caseB_rejects_when_previous_round_is_after_obs() public {
        feed.pushRound(PRICE); // R1
        uint40 obsTime = uint40(block.timestamp) - 1 hours; // before R1
        vm.warp(block.timestamp + 1 hours);
        feed.pushRound(PRICE); // R2
        // R2 is after obsTime, but so is R1: R2 is not the first round after it
        vm.expectRevert(abi.encodeWithSelector(IFixingsRecorder.NoGapProof.selector, R1 + 1, obsTime));
        recorder.recordFixing(obsTime, R1 + 1);
    }

    // --- mock feed guards -------------------------------------------------------

    function test_mock_feed_guards() public {
        vm.expectRevert(MockChainlinkFeed.NoRounds.selector);
        feed.latestRound();
        vm.expectRevert(abi.encodeWithSelector(MockChainlinkFeed.UnknownRound.selector, R1));
        feed.getRoundData(R1);
        vm.prank(address(0xBEEF));
        vm.expectRevert(MockChainlinkFeed.NotOwner.selector);
        feed.pushRound(PRICE);
        feed.pushRound(PRICE);
        vm.expectRevert(
            abi.encodeWithSelector(
                MockChainlinkFeed.NonMonotonic.selector, uint40(block.timestamp), uint40(block.timestamp)
            )
        );
        feed.pushRoundAt(PRICE, uint40(block.timestamp));
        assertEq(feed.decimals(), 8);
        (uint80 id, int256 answer,, uint256 updatedAt,) = feed.latestRoundData();
        assertEq(id, R1);
        assertEq(answer, PRICE);
        assertEq(updatedAt, block.timestamp);
    }
}
