// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {MockChainlinkFeed} from "../src/MockChainlinkFeed.sol";
import {MockUSDG} from "../src/MockUSDG.sol";
import {SeriesFactory} from "../src/SeriesFactory.sol";
import {PerpFactory} from "../src/PerpFactory.sol";
import {PerpSeries} from "../src/PerpSeries.sol";
import {PerpToken} from "../src/PerpToken.sol";
import {FixingsRecorder} from "../src/FixingsRecorder.sol";
import {PerpTerms} from "../src/interfaces/IPerpSeries.sol";
import {PerpFormulaPricer} from "../src/PerpFormulaPricer.sol";

/// Shared fixture for the v2 perpetual note: a feed, USDG, the v1 factory (it
/// owns the recorders), the perpetual factory and the P1 product
/// (k 60%, phi 1/yr at weekly fixings, coupon reserve 23.5%).
abstract contract PerpBase is Test {
    uint32 internal constant WEEK = 604_800;
    uint96 internal constant INITIAL = 100e8; // $100, feed decimals
    uint40 internal constant T0 = 1_790_000_000;
    uint64 internal constant MELT = 18_995_352_771_274_247; // 1 - exp(-7/365)
    uint64 internal constant RESERVE = 235_000; // R = 0.235 per unit of notional
    uint256 internal constant UNIT = 1e6;
    uint256 internal constant WAD = 1e18;
    uint256 internal constant RAY = 1e27;

    MockChainlinkFeed internal feed;
    MockUSDG internal usdg;
    SeriesFactory internal v1Factory;
    PerpFactory internal factory;

    address internal alice = makeAddr("alice");
    address internal bob = makeAddr("bob");

    function setUp() public virtual {
        vm.warp(T0);
        feed = new MockChainlinkFeed("RHTSLA / USD");
        usdg = new MockUSDG();
        v1Factory = new SeriesFactory(address(usdg));
        factory = new PerpFactory(address(usdg), v1Factory);
    }

    function _terms(uint40 firstFixing) internal view returns (PerpTerms memory) {
        return PerpTerms({
            feed: address(feed),
            firstFixing: firstFixing,
            fixingInterval: WEEK,
            kiBarrierBps: 6000,
            meltShare: MELT,
            couponReserve: RESERVE
        });
    }

    /// Where the formula-only pricer answers in the tests: model/p1's bands and vol range.
    function _bands() internal pure returns (PerpFormulaPricer.Bands memory) {
        return PerpFormulaPricer.Bands({
            fixingBandSecs: 6 hours, knockInBandBps: 1000, healBandBps: 500, volMinBps: 2000, volMaxBps: 9000
        });
    }

    function _create(PerpTerms memory t) internal returns (PerpSeries s) {
        s = PerpSeries(factory.createSeries(t));
    }

    function _recorder(PerpSeries s) internal view returns (FixingsRecorder) {
        return FixingsRecorder(address(s.recorder()));
    }

    function _fixingTime(PerpSeries s, uint256 n) internal view returns (uint40) {
        return uint40(s.terms().firstFixing + n * s.terms().fixingInterval);
    }

    function _price(uint256 bps) internal pure returns (int256) {
        return int256(uint256(INITIAL) * bps / 10_000);
    }

    /// Stage a feed round exactly at `fixingTime` and record it (warps past it if needed).
    function _fixAt(PerpSeries s, uint40 fixingTime, int256 price) internal {
        if (block.timestamp <= fixingTime) vm.warp(uint256(fixingTime) + 1);
        feed.pushRoundAt(price, fixingTime);
        _recorder(s).recordFixing(fixingTime, uint80(feed.latestRound()));
    }

    /// Fixing n (0 = the first) at `bps` of INITIAL.
    function _fix(PerpSeries s, uint256 n, uint256 bps) internal {
        _fixAt(s, _fixingTime(s, n), _price(bps));
    }

    /// Mints `amount` pairs to `to`, funding `to` with USDG.
    function _mintPairs(PerpSeries s, address to, uint256 amount) internal returns (uint256 collateralIn) {
        uint256 need = s.previewMint(amount);
        usdg.mint(to, need);
        vm.startPrank(to);
        usdg.approve(address(s), need);
        collateralIn = s.mint(amount, to);
        vm.stopPrank();
    }

    function _note(PerpSeries s) internal view returns (PerpToken) {
        return PerpToken(s.note());
    }

    function _writer(PerpSeries s) internal view returns (PerpToken) {
        return PerpToken(s.writer());
    }
}
