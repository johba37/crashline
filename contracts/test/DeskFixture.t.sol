// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Base} from "./Base.t.sol";
import {MockPricer} from "./mocks/MockPricer.sol";
import {Desk} from "../src/Desk.sol";
import {NoteQuoter} from "../src/NoteQuoter.sol";
import {NoteSeries} from "../src/NoteSeries.sol";

/// Shared Desk fixture: one listed, struck series of the K1 product on day 0 of
/// week 1, spot 90%, the mock model at 9500 bps, 1,000,000 USDG of LP capital
/// and a risk budget of the whole vault.
abstract contract DeskFixture is Base {
    uint16 internal constant VOL = 5500;
    uint128 internal constant CAP = 100_000e6;
    uint256 internal constant LP_CAPITAL = 1_000_000e6;

    NoteQuoter internal quoter;
    MockPricer internal pricer;
    Desk internal desk;
    NoteSeries internal s;
    uint40 internal strikeTime;
    address internal lp = makeAddr("lp");
    address internal integrator = makeAddr("integrator");

    function setUp() public override {
        super.setUp();
        quoter = new NoteQuoter(26 hours);
        pricer = new MockPricer();
        desk = new Desk(IERC20(address(usdg)), factory, quoter, address(this), 1 hours);
        strikeTime = T0 + 1 hours;
        s = _create(_terms(strikeTime));
        _fixAt(s, strikeTime, int256(uint256(INITIAL)));
        _spot(9000); // fresh round, day 0 of week 1
        desk.listSeries(address(s), pricer, VOL, CAP);
        desk.setRiskBudget(address(feed), 10_000); // the whole vault may be at risk in this stock
        pricer.setPrice(9500);
        _deposit(lp, LP_CAPITAL);
    }

    // --- helpers ------------------------------------------------------------------

    function _spot(uint256 bps) internal {
        feed.pushRoundAt(_price(bps), uint40(block.timestamp));
    }

    /// Move time forward and push a fresh round.
    function _later(uint256 secs, uint256 bps) internal {
        vm.warp(block.timestamp + secs);
        _spot(bps);
    }

    /// Observation 1 has passed with a feed round at it, but nobody recorded it.
    function _pendingObs1() internal returns (uint80 round) {
        feed.pushRoundAt(_price(9000), _obsTime(s, 1));
        round = uint80(feed.latestRound());
        vm.warp(uint256(_obsTime(s, 1)) + 60);
        _spot(9000);
    }

    function _deposit(address who, uint256 amount) internal returns (uint256 shares) {
        usdg.mint(who, amount);
        vm.startPrank(who);
        usdg.approve(address(desk), amount);
        shares = desk.deposit(amount, who);
        vm.stopPrank();
    }

    function _buy(address who, uint256 n, uint16 feeBps) internal returns (uint256 cost) {
        (uint256 quoted,) = desk.quoteBuy(address(s), n, feeBps);
        usdg.mint(who, quoted);
        vm.startPrank(who);
        usdg.approve(address(desk), quoted);
        cost = desk.buy(address(s), n, quoted, feeBps, integrator, who);
        vm.stopPrank();
    }

    function _sell(address who, uint256 n, uint16 feeBps) internal returns (uint256 proceeds) {
        address noteToken = s.note();
        vm.startPrank(who);
        IERC20(noteToken).approve(address(desk), n);
        proceeds = desk.sell(address(s), n, 0, feeBps, integrator, who);
        vm.stopPrank();
    }

    function _buyCover(address who, uint256 n, uint16 feeBps) internal returns (uint256 cost) {
        (uint256 quoted,) = desk.quoteBuyCover(address(s), n, feeBps);
        usdg.mint(who, quoted);
        vm.startPrank(who);
        usdg.approve(address(desk), quoted);
        cost = desk.buyCover(address(s), n, quoted, feeBps, integrator, who);
        vm.stopPrank();
    }

    function _sellCover(address who, uint256 n, uint16 feeBps) internal returns (uint256 proceeds) {
        address writerToken = s.writer();
        vm.startPrank(who);
        IERC20(writerToken).approve(address(desk), n);
        proceeds = desk.sellCover(address(s), n, 0, feeBps, integrator, who);
        vm.stopPrank();
    }
}
