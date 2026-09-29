// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Base} from "./Base.t.sol";
import {MockChainlinkFeed} from "../src/MockChainlinkFeed.sol";
import {NoteSeries} from "../src/NoteSeries.sol";
import {FixingsRecorder} from "../src/FixingsRecorder.sol";
import {AutocallPayout} from "../src/AutocallPayout.sol";
import {Vm} from "forge-std/Vm.sol";
import {INoteSeries, SeriesState, SeriesTerms, Phase} from "../src/interfaces/INoteSeries.sol";

/// Payout conformity: every vector from tools/payout_vectors.py (the scalar
/// Python reference of the normative rule) must match exactly, both through the
/// AutocallPayout library and through a live series fed by a recorder.
contract PayoutVectorsTest is Base {
    using AutocallPayout for AutocallPayout.Progress;

    struct Vector {
        string tag;
        uint16 ki;
        uint16 ac;
        uint16 coupon;
        uint8 n;
        uint96 initial;
        uint256[] fixings; // obs 1..N, then maturity; 0 = never recorded
        uint128 payoutPerNote;
        uint128 maxPayoutPerNote;
        uint8 settledAt;
        uint8 observationsDone;
        bool autocalled;
        bool knockedIn;
    }

    function _load() internal view returns (string memory json, uint256 count) {
        json = vm.readFile(string.concat(vm.projectRoot(), "/test/vectors/payout_vectors.json"));
        count = vm.parseJsonUint(json, ".count");
    }

    function _vector(string memory json, uint256 i) internal pure returns (Vector memory v) {
        string memory k = string.concat(".vectors[", vm.toString(i), "]");
        v.tag = vm.parseJsonString(json, string.concat(k, ".tag"));
        v.ki = uint16(vm.parseJsonUint(json, string.concat(k, ".ki")));
        v.ac = uint16(vm.parseJsonUint(json, string.concat(k, ".ac")));
        v.coupon = uint16(vm.parseJsonUint(json, string.concat(k, ".coupon")));
        v.n = uint8(vm.parseJsonUint(json, string.concat(k, ".n")));
        v.initial = uint96(vm.parseJsonUint(json, string.concat(k, ".initial")));
        v.fixings = vm.parseJsonUintArray(json, string.concat(k, ".fixings"));
        v.payoutPerNote = uint128(vm.parseJsonUint(json, string.concat(k, ".payoutPerNote")));
        v.maxPayoutPerNote = uint128(vm.parseJsonUint(json, string.concat(k, ".maxPayoutPerNote")));
        v.settledAt = uint8(vm.parseJsonUint(json, string.concat(k, ".settledAt")));
        v.observationsDone = uint8(vm.parseJsonUint(json, string.concat(k, ".observationsDone")));
        v.autocalled = vm.parseJsonBool(json, string.concat(k, ".autocalled"));
        v.knockedIn = vm.parseJsonBool(json, string.concat(k, ".knockedIn"));
    }

    function test_vector_file_coverage() public view {
        (string memory json, uint256 count) = _load();
        assertGe(count, 200);
        uint256 autocalls;
        uint256 knockIns;
        for (uint256 i = 0; i < count; i++) {
            if (vm.parseJsonBool(json, string.concat(".vectors[", vm.toString(i), "].autocalled"))) autocalls++;
            if (vm.parseJsonBool(json, string.concat(".vectors[", vm.toString(i), "].knockedIn"))) knockIns++;
        }
        assertGt(autocalls, 0);
        assertGt(knockIns, 0);
    }

    /// Library path: fold the fixings (with the series' fallback rule) through
    /// AutocallPayout. Series path: stage the path on a feed, record it, advance
    /// a live series. Vectors run in quarters (each test has a gas budget).
    function test_vectors_q1() public {
        _replay(0, 4);
    }

    function test_vectors_q2() public {
        _replay(1, 4);
    }

    function test_vectors_q3() public {
        _replay(2, 4);
    }

    function test_vectors_q4() public {
        _replay(3, 4);
    }

    function _replay(uint256 part, uint256 parts) internal {
        (string memory json, uint256 count) = _load();
        uint256 to = part + 1 == parts ? count : count * (part + 1) / parts;
        for (uint256 i = count * part / parts; i < to; i++) {
            Vector memory v = _vector(json, i);
            _checkLibrary(v);
            uint256 snap = vm.snapshotState();
            _checkSeries(v);
            vm.revertToState(snap);
        }
    }

    function _checkLibrary(Vector memory v) internal pure {
        AutocallPayout.Progress memory p;
        p.strike(v.initial);
        for (uint256 j = 0; j < v.fixings.length && p.phase != Phase.Settled; j++) {
            uint96 f = v.fixings[j] == 0 ? p.lastFixing : uint96(v.fixings[j]);
            p.observe(v.ki, v.ac, v.coupon, v.n, f);
        }
        assertEq(uint8(p.phase), uint8(Phase.Settled), v.tag);
        assertEq(p.payoutPerNote, v.payoutPerNote, v.tag);
        assertEq(p.settledAt, v.settledAt, v.tag);
        assertEq(p.observationsDone, v.observationsDone, v.tag);
        assertEq(p.autocalled, v.autocalled, v.tag);
        assertEq(p.knockedIn, v.knockedIn, v.tag);
        assertEq(AutocallPayout.maxPayoutPerNote(v.coupon, v.n), v.maxPayoutPerNote, v.tag);
    }

    function _checkSeries(Vector memory v) internal {
        MockChainlinkFeed f = new MockChainlinkFeed("vector");
        SeriesTerms memory t = SeriesTerms({
            feed: address(f),
            strikeTime: T0 + 1 hours,
            observationInterval: WEEK,
            observationCount: v.n,
            kiBarrierBps: v.ki,
            acBarrierBps: v.ac,
            couponBpsPerPeriod: v.coupon
        });
        NoteSeries s = NoteSeries(factory.createSeries(t));
        FixingsRecorder r = FixingsRecorder(address(s.recorder()));

        // stage the whole path on the feed, then record it after the fact
        f.pushRoundAt(int256(uint256(v.initial)), t.strikeTime);
        uint80[] memory rounds = new uint80[](v.fixings.length);
        for (uint256 j = 0; j < v.fixings.length; j++) {
            if (v.fixings[j] == 0) continue;
            f.pushRoundAt(int256(v.fixings[j]), _at(t, j + 1));
            rounds[j] = uint80(f.latestRound());
        }
        vm.warp(uint256(_at(t, uint256(v.n) + 1)) + 10 days); // every fallback deadline passed
        r.recordFixing(t.strikeTime, uint80(f.latestRound() - _recordedCount(v)));
        for (uint256 j = 0; j < v.fixings.length; j++) {
            if (rounds[j] != 0) r.recordFixing(_at(t, j + 1), rounds[j]);
        }

        SeriesState memory viewState = s.state(); // replayed, not yet stored
        vm.recordLogs();
        SeriesState memory st = s.advance();
        _checkSettledEvent(v, address(s));
        assertEq(keccak256(abi.encode(viewState)), keccak256(abi.encode(st)), v.tag);
        assertEq(uint8(st.phase), uint8(Phase.Settled), v.tag);
        assertEq(st.payoutPerNote, v.payoutPerNote, v.tag);
        assertEq(st.observationsDone, v.observationsDone, v.tag);
        assertEq(st.autocalled, v.autocalled, v.tag);
        assertEq(st.knockedIn, v.knockedIn, v.tag);
        assertEq(st.initialFixing, v.initial, v.tag);
        assertEq(s.maxPayoutPerNote(), v.maxPayoutPerNote, v.tag);
    }

    function _checkSettledEvent(Vector memory v, address series) internal view {
        Vm.Log[] memory logs = vm.getRecordedLogs();
        uint256 found;
        for (uint256 k = 0; k < logs.length; k++) {
            if (logs[k].emitter != series || logs[k].topics[0] != INoteSeries.Settled.selector) continue;
            (uint8 at, bool ac, bool ki, uint128 pNote, uint128 pWriter) =
                abi.decode(logs[k].data, (uint8, bool, bool, uint128, uint128));
            assertEq(at, v.settledAt, v.tag);
            assertEq(ac, v.autocalled, v.tag);
            assertEq(ki, v.knockedIn, v.tag);
            assertEq(pNote, v.payoutPerNote, v.tag);
            assertEq(uint256(pNote) + pWriter, v.maxPayoutPerNote, v.tag);
            found++;
        }
        assertEq(found, 1, v.tag);
    }

    function _at(SeriesTerms memory t, uint256 i) internal pure returns (uint40) {
        return uint40(uint256(t.strikeTime) + i * t.observationInterval);
    }

    function _recordedCount(Vector memory v) internal pure returns (uint256 c) {
        for (uint256 j = 0; j < v.fixings.length; j++) {
            if (v.fixings[j] != 0) c++;
        }
    }
}
