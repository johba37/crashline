// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC4626} from "@openzeppelin/contracts/interfaces/IERC4626.sol";
import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {DeskFixture} from "./DeskFixture.t.sol";
import {MockPricer} from "./mocks/MockPricer.sol";
import {MockUSDG} from "../src/MockUSDG.sol";
import {Desk} from "../src/Desk.sol";
import {NoteQuoter} from "../src/NoteQuoter.sol";
import {NoteSeries} from "../src/NoteSeries.sol";
import {SeriesFactory} from "../src/SeriesFactory.sol";
import {IDesk} from "../src/interfaces/IDesk.sol";
import {INoteQuoter} from "../src/interfaces/INoteQuoter.sol";
import {INoteSeries, SeriesTerms, Phase} from "../src/interfaces/INoteSeries.sol";
import {ISurrogatePricer} from "../src/interfaces/ISurrogatePricer.sol";
import {ISeriesFactory} from "../src/interfaces/ISeriesFactory.sol";

contract DeskTest is DeskFixture {
    // --- construction and listing -------------------------------------------------

    function test_constructor_wrong_asset() public {
        MockUSDG other = new MockUSDG();
        vm.expectRevert(Desk.WrongAsset.selector);
        new Desk(IERC20(address(other)), factory, quoter, address(this), 1 hours);
    }

    function test_config() public view {
        assertEq(address(desk.factory()), address(factory));
        assertEq(address(desk.quoter()), address(quoter));
        assertEq(desk.MAX_FEE_BPS(), 200);
        assertEq(desk.BACKSTOP_SHARE_BPS(), 5000);
        assertEq(desk.minSecsToObservation(), 1 hours);
        assertEq(desk.asset(), address(usdg));
        assertEq(desk.decimals(), 12);
        IDesk.Listing memory l = desk.listing(address(s));
        assertTrue(l.active);
        assertEq(address(l.pricer), address(pricer));
        assertEq(l.volBpsAnnual, VOL);
        assertEq(l.capNotional, CAP);
        assertEq(desk.listedSeries().length, 1);
    }

    function test_listSeries_emits_and_updates_in_place() public {
        pricer.setWeightsHash(keccak256("v2"));
        vm.expectEmit(address(desk));
        emit IDesk.SeriesListed(address(s), address(pricer), keccak256("v2"), VOL, 5e6);
        desk.listSeries(address(s), pricer, VOL, 5e6);
        assertEq(desk.listing(address(s)).capNotional, 5e6);
        assertEq(desk.listedSeries().length, 1, "no duplicate entry");
    }

    function test_listSeries_only_owner() public {
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, alice));
        desk.listSeries(address(s), pricer, VOL, CAP);
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, alice));
        desk.delistSeries(address(s));
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, alice));
        desk.setMinSecsToObservation(0);
    }

    function test_listSeries_NotFactorySeries() public {
        vm.expectRevert(abi.encodeWithSelector(IDesk.NotFactorySeries.selector, alice));
        desk.listSeries(alice, pricer, VOL, CAP);
        // a series from another factory (same code, different provenance)
        SeriesFactory other = new SeriesFactory(address(usdg));
        address foreign = other.createSeries(_terms(strikeTime));
        vm.expectRevert(abi.encodeWithSelector(IDesk.NotFactorySeries.selector, foreign));
        desk.listSeries(foreign, pricer, VOL, CAP);
    }

    function test_listSeries_ModelMismatch_each_field() public {
        vm.expectRevert(abi.encodeWithSelector(IDesk.ModelMismatch.selector, uint8(2)));
        desk.listSeries(address(s), pricer, 5000, CAP);

        uint8[4] memory fields = [3, 4, 5, 8];
        for (uint256 i = 0; i < 4; i++) {
            SeriesTerms memory t = _terms(strikeTime);
            if (fields[i] == 3) t.kiBarrierBps = 7000;
            if (fields[i] == 4) t.acBarrierBps = 10500;
            if (fields[i] == 5) t.couponBpsPerPeriod = 30;
            if (fields[i] == 8) t.observationCount = 27;
            address other = factory.createSeries(t);
            vm.expectRevert(abi.encodeWithSelector(IDesk.ModelMismatch.selector, fields[i]));
            desk.listSeries(other, pricer, VOL, CAP);
        }
        // a vol-pinned k1-r1-like model only takes 26 observations
        pricer.setRange(8, 26, 26);
        SeriesTerms memory t13 = _terms(strikeTime);
        t13.observationCount = 13;
        address s13 = factory.createSeries(t13);
        vm.expectRevert(abi.encodeWithSelector(IDesk.ModelMismatch.selector, uint8(8)));
        desk.listSeries(s13, pricer, VOL, CAP);
    }

    function test_listing_is_static_before_strike() public {
        address future = factory.createSeries(_terms(strikeTime + 1 weeks));
        desk.listSeries(future, pricer, VOL, CAP); // no fixing, no feed read needed
        assertEq(desk.listedSeries().length, 2);
    }

    function test_delist_stops_buys_not_sells() public {
        _buy(alice, 1_000e6, 0);
        vm.expectEmit(address(desk));
        emit IDesk.SeriesDelisted(address(s));
        desk.delistSeries(address(s));
        vm.expectRevert(abi.encodeWithSelector(IDesk.NotListed.selector, address(s)));
        desk.quoteBuy(address(s), 1e6, 0);
        _sell(alice, 1_000e6, 0);

        vm.expectRevert(abi.encodeWithSelector(IDesk.NotListed.selector, alice));
        desk.delistSeries(alice);
        vm.expectRevert(abi.encodeWithSelector(IDesk.NotListed.selector, alice));
        desk.quoteSell(alice, 1e6, 0);
    }

    // --- buy ----------------------------------------------------------------------

    function test_buy_pays_quote_plus_fee_and_keeps_writer() public {
        uint256 n = 1_000e6 + 3; // odd amount to exercise rounding
        uint16 feeBps = 50;
        (uint256 quoted, uint16 priceBps) = desk.quoteBuy(address(s), n, feeBps);
        assertEq(priceBps, 9500); // clean 9500 + 0 accrued on day 0
        uint256 fee = (n * feeBps + 9999) / 10_000;
        assertEq(quoted, (n * 9500 + 9999) / 10_000 + fee);

        usdg.mint(alice, quoted);
        vm.startPrank(alice);
        usdg.approve(address(desk), quoted);
        vm.expectEmit(address(desk));
        emit IDesk.NoteBought(address(s), alice, bob, n, 9500, quoted, feeBps, integrator, pricer.weightsHash());
        uint256 cost = desk.buy(address(s), n, quoted, feeBps, integrator, bob);
        vm.stopPrank();

        assertEq(cost, quoted);
        assertEq(_note(s).balanceOf(bob), n);
        assertEq(_writer(s).balanceOf(address(desk)), n);
        assertEq(desk.listing(address(s)).soldNotional, n);
        uint256 slice = (fee * 5000 + 9999) / 10_000;
        assertEq(usdg.balanceOf(integrator), fee - slice);
        assertEq(desk.heldSeries().length, 1);
    }

    function test_buy_errors() public {
        vm.expectRevert(abi.encodeWithSelector(IDesk.FeeTooHigh.selector, uint16(201)));
        desk.quoteBuy(address(s), 1e6, 201);
        vm.expectRevert(INoteSeries.ZeroAmount.selector);
        desk.quoteBuy(address(s), 0, 0);
        vm.expectRevert(abi.encodeWithSelector(IDesk.NotListed.selector, alice));
        desk.quoteBuy(alice, 1e6, 0);
        vm.expectRevert(abi.encodeWithSelector(IDesk.CapExceeded.selector, uint256(CAP) + 1, uint256(CAP)));
        desk.quoteBuy(address(s), uint256(CAP) + 1, 0);

        (uint256 quoted,) = desk.quoteBuy(address(s), 1e6, 0);
        usdg.mint(alice, quoted);
        vm.startPrank(alice);
        usdg.approve(address(desk), quoted);
        vm.expectRevert(abi.encodeWithSelector(IDesk.Slippage.selector, quoted, quoted - 1));
        desk.buy(address(s), 1e6, quoted - 1, 0, integrator, alice);
        vm.stopPrank();
    }

    function test_cap_counts_net_sold() public {
        desk.listSeries(address(s), pricer, VOL, 10e6);
        _buy(alice, 10e6, 0);
        vm.expectRevert(abi.encodeWithSelector(IDesk.CapExceeded.selector, uint256(1), uint256(0)));
        desk.quoteBuy(address(s), 1, 0);
        _sell(alice, 4e6, 0);
        assertEq(desk.listing(address(s)).soldNotional, 6e6);
        _buy(bob, 4e6, 0);
        // lowering the cap below what is sold blocks buys, not sells
        desk.listSeries(address(s), pricer, VOL, 1e6);
        vm.expectRevert(abi.encodeWithSelector(IDesk.CapExceeded.selector, uint256(1), uint256(0)));
        desk.quoteBuy(address(s), 1, 0);
        _sell(bob, 4e6, 0);
    }

    function test_TooCloseToObservation() public {
        uint40 obs1 = _obsTime(s, 1);
        vm.warp(uint256(obs1) - 1 hours);
        _spot(9000);
        desk.quoteBuy(address(s), 1e6, 0); // exactly 1 hour before: allowed
        vm.warp(uint256(obs1) - 1 hours + 1);
        _spot(9000);
        vm.expectRevert(abi.encodeWithSelector(IDesk.TooCloseToObservation.selector, obs1));
        desk.quoteBuy(address(s), 1e6, 0);
        vm.expectRevert(abi.encodeWithSelector(IDesk.TooCloseToObservation.selector, obs1));
        desk.quoteSell(address(s), 1e6, 0);
        desk.setMinSecsToObservation(60);
        desk.quoteBuy(address(s), 1e6, 0);
    }

    function test_quote_errors_bubble() public {
        // weekend: feed stale
        vm.warp(block.timestamp + 27 hours);
        vm.expectRevert(abi.encodeWithSelector(INoteQuoter.FeedStale.selector, uint40(block.timestamp - 27 hours)));
        desk.quoteBuy(address(s), 1e6, 0);
        // pending fixing
        uint80 r1 = _pendingObs1();
        vm.expectRevert(abi.encodeWithSelector(INoteQuoter.FixingPending.selector, _obsTime(s, 1)));
        desk.quoteSell(address(s), 1e6, 0);
        // model refusal
        _recorder(s).recordFixing(_obsTime(s, 1), r1);
        pricer.setRefusal(MockPricer.Mode.UncertifiedErr, 0, 0);
        vm.expectRevert(abi.encodeWithSelector(ISurrogatePricer.Uncertified.selector, uint8(0)));
        desk.quoteBuy(address(s), 1e6, 0);
    }

    // --- sell ---------------------------------------------------------------------

    function test_sell_unwinds_pairs() public {
        _buy(alice, 1_000e6, 0);
        _later(3 days, 8500);
        pricer.setPrice(9000);
        uint256 usdgBefore = usdg.balanceOf(address(desk));
        (uint256 quoted, uint16 priceBps) = desk.quoteSell(address(s), 400e6, 100);
        assertEq(priceBps, 9000 + 25 * (3 days + 0) / WEEK); // accrued coupon added
        uint256 proceeds = _sell(alice, 400e6, 100);
        assertEq(proceeds, quoted);
        assertEq(proceeds, 400e6 * uint256(priceBps) / 10_000 - 4e6);
        assertEq(_writer(s).balanceOf(address(desk)), 600e6);
        assertEq(_note(s).balanceOf(address(desk)), 0);
        // the Desk got 400 pairs' collateral back and paid proceeds + fee share
        assertEq(usdg.balanceOf(address(desk)), usdgBefore + 400 * 1_067_500 - proceeds - (4e6 - 2e6));
    }

    function test_sell_slippage_and_zero_proceeds() public {
        _buy(alice, 1e6, 0);
        address noteToken = s.note();
        vm.startPrank(alice);
        IERC20(noteToken).approve(address(desk), 1e6);
        (uint256 quoted,) = desk.quoteSell(address(s), 1e6, 0);
        vm.expectRevert(abi.encodeWithSelector(IDesk.Slippage.selector, quoted, quoted + 1));
        desk.sell(address(s), 1e6, quoted + 1, 0, integrator, alice);
        vm.stopPrank();
        pricer.setPrice(100); // fee exceeds gross: proceeds clamp to 0
        (uint256 p,) = desk.quoteSell(address(s), 1, 200);
        assertEq(p, 0);
    }

    /// Regression (found by the round-trip fuzz): a fee above the gross amount
    /// of a sell is capped at the gross, so the integrator's cut never comes
    /// out of the LPs.
    function test_sell_fee_capped_at_gross() public {
        _buy(alice, 100, 0);
        pricer.setPrice(100); // 1% of notional: gross 1 unit for 100 units of NOTE
        uint256 navBefore = desk.totalAssets();
        uint256 proceeds = _sell(alice, 100, 200); // fee 2 units > gross 1 unit
        assertEq(proceeds, 0);
        assertEq(usdg.balanceOf(integrator), 0, "fee capped at gross 1, the LPs' half rounds up to all of it");
        assertGe(desk.totalAssets() + 1, navBefore);
    }

    function test_hedger_note_becomes_inventory_then_sold() public {
        _mintPairs(s, bob, 50e6); // hedger mints directly
        _sell(bob, 50e6, 0); // Desk has no WRITER: keeps NOTE inventory
        assertEq(_note(s).balanceOf(address(desk)), 50e6);
        assertEq(desk.heldSeries().length, 1);
        uint256 writerBefore = _writer(s).balanceOf(address(desk));
        _buy(alice, 30e6, 0); // served from inventory, no mint
        assertEq(_writer(s).balanceOf(address(desk)), writerBefore);
        assertEq(_note(s).balanceOf(address(desk)), 20e6);
        _buy(alice, 30e6, 0); // 20 from inventory, 10 minted
        assertEq(_writer(s).balanceOf(address(desk)), 10e6);
    }

    // --- LP accounting --------------------------------------------------------------

    function test_totalAssets_marks_writer_at_the_quote() public {
        uint256 cost = _buy(alice, 1_000e6, 0);
        // idle: capital + cost - collateral; WRITER worth max - price
        uint256 idle = LP_CAPITAL + cost - 1_000 * 1_067_500;
        uint256 writerValue = 1_000e6 * (1_067_500 - 950_000) / 1e6;
        assertEq(desk.totalAssets(), idle + writerValue);
        assertEq(desk.totalAssets(), LP_CAPITAL, "sold at the mark, fee 0: NAV unchanged");
    }

    function test_fees_accrue_to_lps() public {
        uint256 before = desk.totalAssets();
        _buy(alice, 1_000e6, 100); // fee 10 USDG, 5 stay in the vault
        assertEq(desk.totalAssets(), before + 5e6);
        assertEq(usdg.balanceOf(integrator), 5e6);
        // no receiver: the whole fee stays with the LPs
        (uint256 quoted,) = desk.quoteBuy(address(s), 1_000e6, 100);
        usdg.mint(alice, quoted);
        vm.startPrank(alice);
        usdg.approve(address(desk), quoted);
        desk.buy(address(s), 1_000e6, quoted, 100, address(0), alice);
        vm.stopPrank();
        assertEq(desk.totalAssets(), before + 15e6);
    }

    function test_maxDeposit_maxWithdraw_zero_while_unquotable() public {
        _buy(alice, 1_000e6, 0);
        assertEq(desk.maxDeposit(lp), type(uint256).max);
        assertGt(desk.maxWithdraw(lp), 0);

        // weekend
        vm.warp(block.timestamp + 27 hours);
        assertEq(desk.maxDeposit(lp), 0);
        assertEq(desk.maxMint(lp), 0);
        assertEq(desk.maxWithdraw(lp), 0);
        assertEq(desk.maxRedeem(lp), 0);
        usdg.mint(bob, 1e6);
        vm.startPrank(bob);
        usdg.approve(address(desk), 1e6);
        vm.expectRevert(abi.encodeWithSelector(ERC4626.ERC4626ExceededMaxDeposit.selector, bob, 1e6, 0));
        desk.deposit(1e6, bob);
        vm.stopPrank();
        vm.expectRevert(); // totalAssets reverts with the quoter's FeedStale
        desk.totalAssets();

        // market reopens
        _spot(9000);
        assertEq(desk.maxDeposit(lp), type(uint256).max);

        // pending fixing
        uint80 r1 = _pendingObs1();
        assertEq(desk.maxWithdraw(lp), 0);
        _recorder(s).recordFixing(_obsTime(s, 1), r1);
        assertGt(desk.maxWithdraw(lp), 0);

        // model refuses (e.g. observation-day band)
        pricer.setRefusal(MockPricer.Mode.UncertifiedErr, 0, 0);
        assertEq(desk.maxDeposit(lp), 0);
        pricer.setPrice(9500);
        assertEq(desk.maxDeposit(lp), type(uint256).max);
    }

    function test_maxWithdraw_capped_by_idle_usdg() public {
        desk.listSeries(address(s), pricer, VOL, type(uint128).max);
        uint256 idleBefore = usdg.balanceOf(address(desk));
        _buy(alice, 900_000e6, 0); // locks most capital in the series
        uint256 idle = usdg.balanceOf(address(desk));
        assertLt(idle, idleBefore);
        assertEq(desk.maxWithdraw(lp), idle);
        uint256 shares = desk.maxRedeem(lp);
        assertLe(desk.previewRedeem(shares), idle);
        vm.prank(lp);
        desk.withdraw(idle, lp, lp);
        assertEq(usdg.balanceOf(address(desk)), 0);
    }

    function test_inflation_attack_is_unprofitable() public {
        // fresh desk, attacker first
        Desk d = new Desk(IERC20(address(usdg)), factory, quoter, address(this), 1 hours);
        address attacker = makeAddr("attacker");
        usdg.mint(attacker, 1 + 10_000e6);
        vm.startPrank(attacker);
        usdg.approve(address(d), 1);
        d.deposit(1, attacker);
        usdg.transfer(address(d), 10_000e6); // donation
        vm.stopPrank();

        usdg.mint(bob, 1_000e6);
        vm.startPrank(bob);
        usdg.approve(address(d), 1_000e6);
        uint256 shares = d.deposit(1_000e6, bob);
        vm.stopPrank();
        assertGt(shares, 0);
        // bob can take out nearly all of it; the attacker's donation is mostly lost
        // with a 6-decimal offset the victim's loss is bounded by donation / 1e6
        uint256 bobOut = d.previewRedeem(shares);
        assertGe(bobOut, 1_000e6 - 10_000e6 / 1e6, "victim loses <= donation / 1e6");
        // the virtual shares keep about half of the donation: the attack loses money
        uint256 attackerOut = d.previewRedeem(d.balanceOf(attacker));
        assertLt(attackerOut, 10_000e6 * 51 / 100, "attacker loses about half of the donation");
    }

    function test_settlement_collect_and_withdraw_all() public {
        _buy(alice, 1_000e6, 0);
        _later(3 days, 9500);
        _sell(alice, 200e6, 0);
        _fix(s, 1, 10200); // autocall: NOTE pays 1.0025
        assertEq(desk.totalAssets(), usdg.balanceOf(address(desk)) + 800e6 * (1_067_500 - 1_002_500) / 1e6);
        vm.expectRevert(abi.encodeWithSelector(IDesk.NotFactorySeries.selector, alice));
        desk.collect(alice);
        vm.expectEmit(address(desk));
        emit IDesk.Collected(address(s), 800 * (1_067_500 - 1_002_500));
        desk.collect(address(s));
        assertEq(desk.heldSeries().length, 0);
        vm.prank(alice);
        s.redeem(800e6, 0, alice);

        uint256 shares = desk.maxRedeem(lp);
        assertGe(shares, desk.balanceOf(lp) - 1e6, "all but share rounding is redeemable");
        uint256 nav = desk.totalAssets();
        vm.prank(lp);
        uint256 out = desk.redeem(shares, lp, lp);
        assertGe(out + 1, nav);
        assertLe(usdg.balanceOf(address(desk)), 1, "only rounding dust left");
        assertLe(usdg.balanceOf(address(s)), 1);
    }

    function test_final_period_not_knocked_in_valued_without_model() public {
        _buy(alice, 1_000e6, 0);
        for (uint256 i = 1; i <= 26; i++) {
            _fix(s, i, 9000);
        }
        _spot(9000);
        pricer.setRefusal(MockPricer.Mode.OutOfRangeErr, 8, 0); // the model refuses the final period
        assertEq(desk.maxDeposit(lp), type(uint256).max, "certain payout: LP flows stay open");
        // NOTE worth max, WRITER worth 0
        assertEq(desk.totalAssets(), usdg.balanceOf(address(desk)));
    }

    function test_final_period_knocked_in_pauses_lp_flows() public {
        _buy(alice, 1_000e6, 0);
        for (uint256 i = 1; i <= 26; i++) {
            _fix(s, i, i == 2 ? 5000 : 9000);
        }
        _spot(9000);
        pricer.setRefusal(MockPricer.Mode.OutOfRangeErr, 8, 0);
        assertEq(desk.maxDeposit(lp), 0);
    }

    /// totalAssets loops over held series: the Desk refuses to hold more than MAX_HELD_SERIES.
    function test_held_series_limit() public {
        // 64 more series on the same feed, strikes 1 s apart, all fixed by one round
        uint40 base = uint40(block.timestamp) + 1 days;
        vm.warp(base - 1);
        _spot(9000);
        address[] memory more = new address[](64);
        for (uint256 i = 0; i < 64; i++) {
            more[i] = factory.createSeries(_terms(base + uint40(i)));
        }
        vm.warp(uint256(base) + 64);
        uint80 r = uint80(feed.latestRound());
        for (uint256 i = 0; i < 64; i++) {
            _recorder(s).recordFixing(base + uint40(i), r);
        }
        _spot(9000);
        usdg.mint(alice, 1_000e6);
        vm.startPrank(alice);
        usdg.approve(address(desk), type(uint256).max);
        vm.stopPrank();
        desk.listSeries(address(s), pricer, VOL, CAP);
        vm.prank(alice);
        desk.buy(address(s), 1e6, type(uint256).max, 0, address(0), alice); // held #1
        for (uint256 i = 0; i < 63; i++) {
            desk.listSeries(more[i], pricer, VOL, CAP);
            vm.prank(alice);
            desk.buy(more[i], 1e6, type(uint256).max, 0, address(0), alice);
        }
        assertEq(desk.heldSeries().length, 64);
        desk.listSeries(more[63], pricer, VOL, CAP);
        vm.prank(alice);
        vm.expectRevert(Desk.HeldSeriesLimit.selector);
        desk.buy(more[63], 1e6, type(uint256).max, 0, address(0), alice);
    }

    function test_mock_usdg_only_issuer() public {
        vm.startPrank(alice);
        vm.expectRevert(MockUSDG.NotIssuer.selector);
        usdg.setPaused(true);
        vm.expectRevert(MockUSDG.NotIssuer.selector);
        usdg.setFrozen(bob, true);
        vm.stopPrank();
    }

    function test_usdg_pause_blocks_trading_not_views() public {
        _buy(alice, 1e6, 0);
        usdg.setPaused(true);
        desk.totalAssets();
        address noteToken = s.note();
        vm.startPrank(alice);
        IERC20(noteToken).approve(address(desk), 1e6);
        vm.expectRevert(MockUSDG.Paused.selector);
        desk.sell(address(s), 1e6, 0, 0, integrator, alice);
        vm.stopPrank();
    }

    // --- fuzz ---------------------------------------------------------------------

    /// A buy followed by a sell at the same quote never lowers the LPs' assets by
    /// more than rounding, whatever the amounts and fees.
    function testFuzz_round_trip_never_costs_lps(uint256 n, uint16 feeBps, uint16 price) public {
        n = bound(n, 1, 50_000e6);
        feeBps = uint16(bound(feeBps, 0, 200));
        price = uint16(bound(price, 1, 10_675));
        pricer.setPrice(price);
        uint256 before = desk.totalAssets();
        _buy(alice, n, feeBps);
        // the only leak: < 1 base unit of mint rounding, locked in the series escrow,
        // plus < 1 unit from flooring the WRITER mark
        assertGe(desk.totalAssets() + 2, before);
        _sell(alice, n, feeBps);
        assertGe(desk.totalAssets() + 2, before);
    }

    function testFuzz_fee_cap(uint16 feeBps) public {
        if (feeBps > 200) {
            vm.expectRevert(abi.encodeWithSelector(IDesk.FeeTooHigh.selector, feeBps));
            desk.quoteBuy(address(s), 1e6, feeBps);
        } else {
            (uint256 cost,) = desk.quoteBuy(address(s), 1e6, feeBps);
            assertEq(cost, 950_000 + (uint256(feeBps) * 1e6 + 9999) / 10_000);
        }
    }
}
