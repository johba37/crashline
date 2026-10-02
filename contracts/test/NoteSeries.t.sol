// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Base} from "./Base.t.sol";
import {MockUSDG} from "../src/mocks/MockUSDG.sol";
import {NoteSeries} from "../src/NoteSeries.sol";
import {SeriesFactory} from "../src/SeriesFactory.sol";
import {INoteSeries, SeriesState, SeriesTerms, Phase} from "../src/interfaces/INoteSeries.sol";

/// 6-decimal collateral that re-enters the series from inside transferFrom.
contract ReentrantUSDG is ERC20 {
    NoteSeries public target;

    constructor() ERC20("Reentrant", "RE") {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function setTarget(NoteSeries t) external {
        target = t;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function transferFrom(address from, address to, uint256 value) public override returns (bool) {
        if (address(target) != address(0)) target.mint(1, from); // must hit the guard
        return super.transferFrom(from, to, value);
    }
}

contract NoteSeriesTest is Base {
    NoteSeries s;
    uint40 strikeTime;

    function setUp() public override {
        super.setUp();
        strikeTime = T0 + 1 hours;
        s = _create(_terms(strikeTime));
    }

    // --- phases and pending fixings -----------------------------------------------

    function test_pending_until_strike_recorded() public {
        SeriesState memory st = s.state();
        assertEq(uint8(st.phase), uint8(Phase.Pending));
        assertEq(st.nextObservation, strikeTime);
        assertEq(st.maturity, strikeTime + 27 * WEEK);
        (bool pending, uint40 t) = s.pendingObservation();
        assertFalse(pending);
        assertEq(t, strikeTime);

        vm.warp(strikeTime + 1);
        (pending, t) = s.pendingObservation();
        assertTrue(pending);
        assertEq(t, strikeTime);

        usdg.mint(alice, 10e6);
        vm.startPrank(alice);
        usdg.approve(address(s), type(uint256).max);
        vm.expectRevert(INoteSeries.NotStruck.selector);
        s.mint(1e6, alice);
        vm.expectRevert(INoteSeries.NotStruck.selector);
        s.redeemPair(1e6, alice);
        vm.stopPrank();
    }

    function test_strike_never_falls_back() public {
        vm.warp(strikeTime + 30 days);
        assertEq(uint8(s.state().phase), uint8(Phase.Pending));
        (bool pending,) = s.pendingObservation();
        assertTrue(pending);
    }

    function test_strike_then_live() public {
        _fix(s, 0, 10000);
        // state() includes the recorded but unprocessed strike
        SeriesState memory st = s.state();
        assertEq(uint8(st.phase), uint8(Phase.Live));
        assertEq(st.initialFixing, INITIAL);
        assertEq(st.nextObservation, strikeTime + WEEK);

        vm.expectEmit(address(s));
        emit INoteSeries.Struck(INITIAL);
        s.advance();
        (bool pending, uint40 t) = s.pendingObservation();
        assertFalse(pending);
        assertEq(t, strikeTime + WEEK);
    }

    function test_observation_pending_blocks_nothing_but_is_reported() public {
        _fix(s, 0, 10000);
        vm.warp(strikeTime + WEEK); // exactly at obs 1: not passed yet
        (bool pending,) = s.pendingObservation();
        assertFalse(pending);
        vm.warp(strikeTime + WEEK + 1);
        (pending,) = s.pendingObservation();
        assertTrue(pending);
    }

    // --- mint / redeemPair --------------------------------------------------------

    function test_mint_locks_ceil_and_redeemPair_pays_floor() public {
        _fix(s, 0, 10000);
        // 1 NOTE = 1e6 units; max 1_067_500 per NOTE
        assertEq(s.previewMint(1e6), 1_067_500);
        assertEq(s.previewMint(3), 4); // 3 * 1.0675 = 3.2025 -> 4
        assertEq(s.previewRedeemPair(3), 3);

        uint256 inAmt = _mintPairs(s, alice, 3);
        assertEq(inAmt, 4);
        assertEq(_note(s).balanceOf(alice), 3);
        assertEq(_writer(s).balanceOf(alice), 3);

        vm.prank(alice);
        uint256 outAmt = s.redeemPair(3, bob);
        assertEq(outAmt, 3);
        assertEq(usdg.balanceOf(bob), 3);
        assertEq(usdg.balanceOf(address(s)), 1, "dust stays in the escrow");
        assertEq(_note(s).totalSupply(), 0);
    }

    function test_mint_emits_and_pulls() public {
        _fix(s, 0, 10000);
        usdg.mint(alice, 1_067_500);
        vm.startPrank(alice);
        usdg.approve(address(s), 1_067_500);
        vm.expectEmit(address(s));
        emit INoteSeries.Minted(alice, bob, 1e6, 1_067_500);
        s.mint(1e6, bob);
        vm.stopPrank();
        assertEq(_note(s).balanceOf(bob), 1e6);
        assertEq(usdg.balanceOf(address(s)), 1_067_500);
    }

    function test_revert_ZeroAmount() public {
        _fix(s, 0, 10000);
        vm.expectRevert(INoteSeries.ZeroAmount.selector);
        s.mint(0, alice);
        vm.expectRevert(INoteSeries.ZeroAmount.selector);
        s.redeemPair(0, alice);
        vm.expectRevert(INoteSeries.ZeroAmount.selector);
        s.redeem(0, 0, alice);
    }

    function test_redeemPair_needs_both_tokens() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 1e6);
        address noteToken = s.note();
        vm.prank(alice);
        ERC20(noteToken).transfer(bob, 1);
        vm.prank(alice);
        vm.expectRevert(); // ERC20InsufficientBalance on NOTE
        s.redeemPair(1e6, alice);
    }

    // --- settlement paths ---------------------------------------------------------

    function test_autocall_settles_and_redeems() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 2e6);
        _fix(s, 1, 9000);
        _fix(s, 2, 5000); // knock-in
        _fix(s, 3, 10000); // autocall at exactly the barrier

        SeriesState memory st = s.state();
        assertEq(uint8(st.phase), uint8(Phase.Settled));
        assertTrue(st.autocalled);
        assertTrue(st.knockedIn);
        assertEq(st.observationsDone, 3);
        assertEq(st.nextObservation, 0);
        assertEq(st.payoutPerNote, 1_007_500); // 1 + 3 * 25 bps

        address noteToken = s.note();
        vm.prank(alice);
        ERC20(noteToken).transfer(bob, 1e6);

        vm.expectEmit(address(s));
        emit INoteSeries.Settled(3, true, true, 1_007_500, 60_000);
        vm.prank(bob);
        uint256 out = s.redeem(1e6, 0, bob);
        assertEq(out, 1_007_500);

        vm.prank(alice);
        out = s.redeem(1e6, 2e6, alice);
        assertEq(out, 1_007_500 + 2 * 60_000);
        assertEq(usdg.balanceOf(address(s)), 0);

        // no mint after settlement
        usdg.mint(alice, 2e6);
        vm.startPrank(alice);
        usdg.approve(address(s), 2e6);
        vm.expectRevert(INoteSeries.AlreadySettled.selector);
        s.mint(1e6, alice);
        vm.stopPrank();
    }

    function test_redeem_before_settlement_reverts() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 1e6);
        vm.prank(alice);
        vm.expectRevert(INoteSeries.NotSettled.selector);
        s.redeem(1e6, 0, alice);
        vm.expectRevert(INoteSeries.NotSettled.selector);
        s.previewRedeem(1e6, 0);
    }

    function test_maturity_knocked_in_below_initial() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 1e6);
        for (uint256 i = 1; i <= 26; i++) {
            _fix(s, i, i == 10 ? 5999 : 8000);
        }
        assertFalse(s.state().phase == Phase.Settled);
        _fix(s, 27, 7000);
        SeriesState memory st = s.state();
        assertEq(uint8(st.phase), uint8(Phase.Settled));
        assertTrue(st.knockedIn);
        assertFalse(st.autocalled);
        assertEq(st.payoutPerNote, 700_000 + 67_500);
        assertEq(s.previewRedeem(1e6, 1e6), 1_067_500);
    }

    function test_maturity_knocked_in_recovered_above_initial() public {
        _fix(s, 0, 10000);
        _fix(s, 1, 5000);
        for (uint256 i = 2; i <= 26; i++) {
            _fix(s, i, 9999);
        }
        _fix(s, 27, 12000); // maturity above initial: par + coupons, no autocall check at M
        SeriesState memory st = s.state();
        assertTrue(st.knockedIn);
        assertFalse(st.autocalled);
        assertEq(st.payoutPerNote, 1_067_500);
    }

    function test_maturity_not_knocked_in() public {
        _fix(s, 0, 10000);
        for (uint256 i = 1; i <= 26; i++) {
            _fix(s, i, 6000); // exactly at ki: not knocked in
        }
        _fix(s, 27, 1000);
        SeriesState memory st = s.state();
        assertFalse(st.knockedIn);
        assertEq(st.payoutPerNote, 1_067_500);
    }

    function test_fallback_reuses_previous_fixing() public {
        _fix(s, 0, 10000);
        _fix(s, 1, 9000);
        uint40 t2 = _obsTime(s, 2);
        uint256 deadline = uint256(t2) + 8 days + 1 days;
        vm.warp(deadline - 1);
        (bool pending, uint40 pt) = s.pendingObservation();
        assertTrue(pending);
        assertEq(pt, t2);
        vm.warp(deadline);
        (pending, pt) = s.pendingObservation();
        assertTrue(pending);
        assertEq(pt, _obsTime(s, 3), "obs 2 processed by fallback in the view; obs 3 now pending");
        SeriesState memory st = s.state();
        assertEq(st.observationsDone, 2);

        vm.expectEmit(address(s));
        emit INoteSeries.ObservationProcessed(2, t2, uint96(uint256(_price(9000))), false, false, true);
        s.advance();
    }

    function test_fallback_for_first_observation_uses_strike() public {
        _fix(s, 0, 10000);
        vm.warp(uint256(_obsTime(s, 1)) + 9 days);
        // strike fixing reused at obs 1: 10000 >= ac -> autocall
        SeriesState memory st = s.state();
        assertEq(uint8(st.phase), uint8(Phase.Settled));
        assertTrue(st.autocalled);
        assertEq(st.payoutPerNote, 1_002_500);
    }

    function test_recorded_fixing_beats_fallback() public {
        _fix(s, 0, 10000);
        uint40 t1 = _obsTime(s, 1);
        feed.pushRoundAt(_price(8000), t1);
        vm.warp(uint256(t1) + 30 days);
        _recorder(s).recordFixing(t1, uint80(feed.latestRound()));
        assertEq(s.state().observationsDone, 4, "obs 1 recorded, obs 2..4 fall back to it");
        assertFalse(s.state().autocalled);
    }

    function test_redeem_to_other_account_when_frozen() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 1e6);
        _fix(s, 1, 10500);
        usdg.setFrozen(alice, true);
        vm.startPrank(alice);
        vm.expectRevert(abi.encodeWithSelector(MockUSDG.Frozen.selector, alice));
        s.redeem(1e6, 1e6, alice);
        s.redeem(1e6, 1e6, bob); // pull-based: any recipient
        vm.stopPrank();
        assertEq(usdg.balanceOf(bob), 1_067_500);
    }

    function test_usdg_pause_blocks_then_resumes() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 1e6);
        _fix(s, 1, 10500);
        usdg.setPaused(true);
        s.advance(); // settlement moves no tokens: works while paused
        assertEq(uint8(s.state().phase), uint8(Phase.Settled));
        vm.prank(alice);
        vm.expectRevert(MockUSDG.Paused.selector);
        s.redeem(1e6, 1e6, alice);
        usdg.setPaused(false);
        vm.prank(alice);
        assertEq(s.redeem(1e6, 1e6, alice), 1_067_500);
    }

    function test_redeemPair_after_settlement() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 1e6);
        _fix(s, 1, 10500);
        vm.prank(alice);
        assertEq(s.redeemPair(1e6, alice), 1_067_500);
    }

    function test_advance_is_idempotent() public {
        _fix(s, 0, 10000);
        _fix(s, 1, 9000);
        s.advance();
        vm.recordLogs();
        s.advance();
        assertEq(vm.getRecordedLogs().length, 0);
    }

    function test_reentrancy_guard() public {
        ReentrantUSDG re = new ReentrantUSDG();
        SeriesFactory f2 = new SeriesFactory(address(re));
        NoteSeries s2 = NoteSeries(f2.createSeries(_terms(strikeTime)));
        _fixAt(s2, strikeTime, _price(10000));
        re.mint(alice, 10e6);
        vm.prank(alice);
        re.approve(address(s2), type(uint256).max);
        re.setTarget(s2);
        vm.prank(alice);
        vm.expectRevert(ReentrancyGuard.ReentrancyGuardReentrantCall.selector);
        s2.mint(1e6, alice);
    }

    // --- fuzz ---------------------------------------------------------------------

    /// A pair round trip never pays out more than it locked, and loses < 1 unit.
    function testFuzz_pair_round_trip(uint256 amount) public {
        amount = bound(amount, 1, 1e15);
        _fix(s, 0, 10000);
        uint256 inAmt = _mintPairs(s, alice, amount);
        vm.prank(alice);
        uint256 outAmt = s.redeemPair(amount, alice);
        assertLe(outAmt, inAmt);
        assertLe(inAmt - outAmt, 1);
    }

    /// NOTE and WRITER payouts of a pair always add up to at most the collateral
    /// the pair locked, whatever the path.
    function testFuzz_settled_pair_is_covered(uint256 amount, uint16 obsBps, uint16 matBps, uint8 kiAt) public {
        amount = bound(amount, 1, 1e15);
        obsBps = uint16(bound(obsBps, 1, 30000));
        matBps = uint16(bound(matBps, 1, 30000));
        _fix(s, 0, 10000);
        uint256 inAmt = _mintPairs(s, alice, amount);
        kiAt = uint8(bound(kiAt, 1, 26));
        for (uint256 i = 1; i <= 26 && s.state().phase != Phase.Settled; i++) {
            _fix(s, i, i == kiAt ? obsBps : 9000);
        }
        if (s.state().phase != Phase.Settled) _fix(s, 27, matBps);
        uint128 p = s.state().payoutPerNote;
        assertLe(p, s.maxPayoutPerNote());
        vm.prank(alice);
        uint256 outAmt = s.redeem(amount, amount, alice);
        assertLe(outAmt, inAmt);
        assertLe(inAmt - outAmt, 2);
    }
}
