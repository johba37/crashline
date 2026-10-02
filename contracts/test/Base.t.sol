// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {MockChainlinkFeed} from "../src/mocks/MockChainlinkFeed.sol";
import {MockUSDG} from "../src/mocks/MockUSDG.sol";
import {SeriesFactory} from "../src/SeriesFactory.sol";
import {NoteSeries} from "../src/NoteSeries.sol";
import {SeriesToken} from "../src/SeriesToken.sol";
import {FixingsRecorder} from "../src/FixingsRecorder.sol";
import {SeriesTerms} from "../src/interfaces/INoteSeries.sol";

/// Shared fixture: a feed, USDG, a factory and the K1 product
/// (ki 60%, ac 100%, 25 bps/week, 26 weekly observations).
abstract contract Base is Test {
    uint32 internal constant WEEK = 604_800;
    uint96 internal constant INITIAL = 100e8; // $100, feed decimals
    uint40 internal constant T0 = 1_790_000_000;

    MockChainlinkFeed internal feed;
    MockUSDG internal usdg;
    SeriesFactory internal factory;

    address internal alice = makeAddr("alice");
    address internal bob = makeAddr("bob");

    function setUp() public virtual {
        vm.warp(T0);
        feed = new MockChainlinkFeed("RHTSLA / USD");
        usdg = new MockUSDG();
        factory = new SeriesFactory(address(usdg));
    }

    function _terms(uint40 strikeTime) internal view returns (SeriesTerms memory) {
        return SeriesTerms({
            feed: address(feed),
            strikeTime: strikeTime,
            observationInterval: WEEK,
            observationCount: 26,
            kiBarrierBps: 6000,
            acBarrierBps: 10000,
            couponBpsPerPeriod: 25
        });
    }

    function _create(SeriesTerms memory t) internal returns (NoteSeries s) {
        s = NoteSeries(factory.createSeries(t));
    }

    function _recorder(NoteSeries s) internal view returns (FixingsRecorder) {
        return FixingsRecorder(address(s.recorder()));
    }

    function _obsTime(NoteSeries s, uint256 i) internal view returns (uint40) {
        return uint40(s.terms().strikeTime + i * s.terms().observationInterval);
    }

    function _price(uint256 bps) internal pure returns (int256) {
        return int256(uint256(INITIAL) * bps / 10_000);
    }

    /// Stage a feed round exactly at obsTime and record it (warps past obsTime if needed).
    function _fixAt(NoteSeries s, uint40 obsTime, int256 price) internal {
        if (block.timestamp <= obsTime) vm.warp(uint256(obsTime) + 1);
        feed.pushRoundAt(price, obsTime);
        _recorder(s).recordFixing(obsTime, uint80(feed.latestRound()));
    }

    /// Fixing i (0 = strike) at `bps` of INITIAL.
    function _fix(NoteSeries s, uint256 i, uint256 bps) internal {
        _fixAt(s, _obsTime(s, i), _price(bps));
    }

    /// Mints `amount` pairs to `to`, funding `to` with USDG.
    function _mintPairs(NoteSeries s, address to, uint256 amount) internal returns (uint256 collateralIn) {
        uint256 need = s.previewMint(amount);
        usdg.mint(to, need);
        vm.startPrank(to);
        usdg.approve(address(s), need);
        collateralIn = s.mint(amount, to);
        vm.stopPrank();
    }

    function _note(NoteSeries s) internal view returns (SeriesToken) {
        return SeriesToken(s.note());
    }

    function _writer(NoteSeries s) internal view returns (SeriesToken) {
        return SeriesToken(s.writer());
    }
}
