// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {PerpBase} from "./PerpBase.t.sol";
import {MockChainlinkFeed} from "../src/MockChainlinkFeed.sol";
import {PerpSeries} from "../src/PerpSeries.sol";
import {FixingsRecorder} from "../src/FixingsRecorder.sol";
import {PerpPayout} from "../src/PerpPayout.sol";
import {Vm} from "forge-std/Vm.sol";
import {IPerpSeries, PerpState, PerpTerms, PerpPhase} from "../src/interfaces/IPerpSeries.sol";

/// Payout conformity for the perpetual note: every vector from
/// tools/perp_vectors.py (the scalar Python reference of the normative rule)
/// must match exactly at every fixing, both through the PerpPayout library
/// and through a live series fed by a recorder, where a holder's claim must
/// equal the indexes times its balance.
contract PerpVectorsTest is PerpBase {
    using PerpPayout for PerpPayout.Progress;

    uint256 internal constant HELD = 1_234_567_891; // tokens the test holder mints at the first fixing

    struct Vector {
        string tag;
        uint16 ki;
        uint64 meltShare;
        uint64 couponReserve;
        uint96 first;
        uint256[] fixings; // fixing 1, 2, ...; 0 = never recorded
        uint8 phase;
        uint96 referenceFixing;
        uint96 lastFixing;
        bool knockedIn;
        uint8 missedInARow;
        uint32 fixingsDone;
        uint256 notionalPerToken;
        uint256 noteIndex;
        uint256 writerIndex;
        uint256[] noteRelease;
        uint256[] writerRelease;
    }

    function _load() internal view returns (string memory json, uint256 count) {
        json = vm.readFile(string.concat(vm.projectRoot(), "/test/vectors/perp_vectors.json"));
        count = vm.parseJsonUint(json, ".count");
        assertEq(vm.parseJsonUint(json, ".closeAfterMissed"), 4);
    }

    function _vector(string memory json, uint256 i) internal pure returns (Vector memory v) {
        string memory k = string.concat(".vectors[", vm.toString(i), "]");
        v.tag = vm.parseJsonString(json, string.concat(k, ".tag"));
        v.ki = uint16(vm.parseJsonUint(json, string.concat(k, ".ki")));
        v.meltShare = uint64(vm.parseJsonUint(json, string.concat(k, ".meltShare")));
        v.couponReserve = uint64(vm.parseJsonUint(json, string.concat(k, ".couponReserve")));
        v.first = uint96(vm.parseJsonUint(json, string.concat(k, ".first")));
        v.fixings = vm.parseJsonUintArray(json, string.concat(k, ".fixings"));
        v.phase = uint8(vm.parseJsonUint(json, string.concat(k, ".phase")));
        v.referenceFixing = uint96(vm.parseJsonUint(json, string.concat(k, ".reference")));
        v.lastFixing = uint96(vm.parseJsonUint(json, string.concat(k, ".lastFixing")));
        v.knockedIn = vm.parseJsonBool(json, string.concat(k, ".knockedIn"));
        v.missedInARow = uint8(vm.parseJsonUint(json, string.concat(k, ".missedInARow")));
        v.fixingsDone = uint32(vm.parseJsonUint(json, string.concat(k, ".fixingsDone")));
        v.notionalPerToken = vm.parseJsonUint(json, string.concat(k, ".notionalPerToken"));
        v.noteIndex = vm.parseJsonUint(json, string.concat(k, ".noteIndex"));
        v.writerIndex = vm.parseJsonUint(json, string.concat(k, ".writerIndex"));
        v.noteRelease = vm.parseJsonUintArray(json, string.concat(k, ".noteRelease"));
        v.writerRelease = vm.parseJsonUintArray(json, string.concat(k, ".writerRelease"));
    }

    function test_vector_file_coverage() public view {
        (string memory json, uint256 count) = _load();
        assertGe(count, 150);
        uint256 closed;
        uint256 knockIns;
        for (uint256 i = 0; i < count; i++) {
            string memory k = string.concat(".vectors[", vm.toString(i), "]");
            if (vm.parseJsonUint(json, string.concat(k, ".phase")) == uint8(PerpPhase.Closed)) closed++;
            if (vm.parseJsonBool(json, string.concat(k, ".knockedIn"))) knockIns++;
        }
        assertGt(closed, 0);
        assertGt(knockIns, 0);
    }

    /// Vectors run in eighths (each test has a gas budget; the ten-year paths are the last two).
    function test_vectors_p1() public {
        _replay(0, 8);
    }

    function test_vectors_p2() public {
        _replay(1, 8);
    }

    function test_vectors_p3() public {
        _replay(2, 8);
    }

    function test_vectors_p4() public {
        _replay(3, 8);
    }

    function test_vectors_p5() public {
        _replay(4, 8);
    }

    function test_vectors_p6() public {
        _replay(5, 8);
    }

    function test_vectors_p7() public {
        _replay(6, 8);
    }

    function test_vectors_p8() public {
        _replay(7, 8);
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

    /// Library path: fold the fixings (with the series' missed-fixing rule) through PerpPayout.
    function _checkLibrary(Vector memory v) internal pure {
        PerpPayout.Progress memory p;
        p.strike(v.first);
        uint256 done;
        for (uint256 j = 0; j < v.fixings.length && p.phase != PerpPhase.Closed; j++) {
            bool missed = v.fixings[j] == 0;
            uint96 f = missed ? p.lastFixing : uint96(v.fixings[j]);
            (uint256 n, uint256 w) = p.fix(v.ki, v.meltShare, v.couponReserve, 4, f, missed);
            assertEq(n, v.noteRelease[j], v.tag);
            assertEq(w, v.writerRelease[j], v.tag);
            done++;
        }
        assertEq(done, v.fixingsDone, v.tag);
        assertEq(uint8(p.phase), v.phase, v.tag);
        assertEq(p.referenceFixing, v.referenceFixing, v.tag);
        assertEq(p.lastFixing, v.lastFixing, v.tag);
        assertEq(p.knockedIn, v.knockedIn, v.tag);
        assertEq(p.missedInARow, v.missedInARow, v.tag);
        assertEq(p.fixingsDone, v.fixingsDone, v.tag);
        assertEq(p.notionalPerToken, v.notionalPerToken, v.tag);
        assertEq(p.noteIndex, v.noteIndex, v.tag);
        assertEq(p.writerIndex, v.writerIndex, v.tag);
    }

    /// Series path: stage the path on a feed, strike, mint, record the rest, advance.
    function _checkSeries(Vector memory v) internal {
        MockChainlinkFeed f = new MockChainlinkFeed("vector");
        PerpTerms memory t = PerpTerms({
            feed: address(f),
            firstFixing: T0 + 1 hours,
            fixingInterval: WEEK,
            kiBarrierBps: v.ki,
            meltShare: v.meltShare,
            couponReserve: v.couponReserve
        });
        PerpSeries s = PerpSeries(factory.createSeries(t));
        FixingsRecorder r = FixingsRecorder(address(s.recorder()));

        // the first fixing, then a holder mints before anything else happens
        vm.warp(uint256(t.firstFixing) + 1);
        f.pushRoundAt(int256(uint256(v.first)), t.firstFixing);
        r.recordFixing(t.firstFixing, uint80(f.latestRound()));
        uint256 locked = s.previewMint(HELD);
        usdg.mint(alice, locked);
        vm.startPrank(alice);
        usdg.approve(address(s), locked);
        s.mint(HELD, alice);
        vm.stopPrank();

        // stage the rest of the path, then record it after every fallback deadline
        uint80[] memory rounds = new uint80[](v.fixings.length);
        for (uint256 j = 0; j < v.fixings.length; j++) {
            if (v.fixings[j] == 0) continue;
            f.pushRoundAt(int256(v.fixings[j]), _at(t, j + 1));
            rounds[j] = uint80(f.latestRound());
        }
        vm.warp(uint256(_at(t, v.fixings.length)) + 10 days);
        for (uint256 j = 0; j < v.fixings.length; j++) {
            if (rounds[j] != 0) r.recordFixing(_at(t, j + 1), rounds[j]);
        }

        PerpState memory viewState = s.state(); // replayed, not yet stored
        uint256 claimableBefore = s.claimable(alice);
        vm.recordLogs();
        PerpState memory st = s.advance();
        _checkEvents(v, address(s));
        assertEq(keccak256(abi.encode(viewState)), keccak256(abi.encode(st)), v.tag);
        assertEq(uint8(st.phase), v.phase, v.tag);
        assertEq(st.referenceFixing, v.referenceFixing, v.tag);
        assertEq(st.lastFixing, v.lastFixing, v.tag);
        assertEq(st.knockedIn, v.knockedIn, v.tag);
        assertEq(st.missedInARow, v.missedInARow, v.tag);
        assertEq(st.fixingsDone, v.fixingsDone, v.tag);
        assertEq(st.notionalPerToken, v.notionalPerToken, v.tag);
        assertEq(st.noteIndex, v.noteIndex, v.tag);
        assertEq(st.writerIndex, v.writerIndex, v.tag);

        // the holder's claim is the indexes times its balance, and the escrow covers it
        // together with the pair value still locked
        uint256 owed = HELD * v.noteIndex / RAY + HELD * v.writerIndex / RAY;
        assertEq(claimableBefore, owed, v.tag);
        assertEq(s.claimable(alice), owed, v.tag);
        assertGe(usdg.balanceOf(address(s)), owed + s.previewRedeemPair(HELD), v.tag);
        // the rounding left behind is below one base unit per rounding step (mint, two legs, pair)
        assertLe(usdg.balanceOf(address(s)) - owed - s.previewRedeemPair(HELD), 3, v.tag);

        vm.startPrank(alice);
        assertEq(s.claim(bob), owed, v.tag);
        uint256 pair = s.redeemPair(HELD, bob);
        vm.stopPrank();
        assertEq(usdg.balanceOf(bob), owed + pair, v.tag);
        if (v.phase == uint8(PerpPhase.Closed)) assertEq(pair, 0, v.tag);
    }

    function _checkEvents(Vector memory v, address series) internal view {
        Vm.Log[] memory logs = vm.getRecordedLogs();
        uint256 found;
        uint256 closed;
        for (uint256 k = 0; k < logs.length; k++) {
            if (logs[k].emitter != series) continue;
            if (logs[k].topics[0] == IPerpSeries.Closed.selector) closed++;
            if (logs[k].topics[0] != IPerpSeries.FixingProcessed.selector) continue;
            (,,,, bool missed,, uint256 noteRelease, uint256 writerRelease) =
                abi.decode(logs[k].data, (uint40, uint96, uint96, bool, bool, uint256, uint256, uint256));
            assertEq(uint256(logs[k].topics[1]), found + 1, v.tag);
            assertEq(missed, v.fixings[found] == 0, v.tag);
            assertEq(noteRelease, v.noteRelease[found], v.tag);
            assertEq(writerRelease, v.writerRelease[found], v.tag);
            found++;
        }
        assertEq(found, v.fixingsDone, v.tag);
        assertEq(closed, v.phase == uint8(PerpPhase.Closed) ? 1 : 0, v.tag);
    }

    function _at(PerpTerms memory t, uint256 n) internal pure returns (uint40) {
        return uint40(uint256(t.firstFixing) + n * t.fixingInterval);
    }
}
