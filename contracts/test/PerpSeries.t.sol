// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {PerpBase} from "./PerpBase.t.sol";
import {MockUSDG} from "../src/MockUSDG.sol";
import {SeriesFactory} from "../src/SeriesFactory.sol";
import {PerpFactory} from "../src/PerpFactory.sol";
import {PerpSeries} from "../src/PerpSeries.sol";
import {PerpToken} from "../src/PerpToken.sol";
import {IPerpSeries, PerpState, PerpTerms, PerpPhase} from "../src/interfaces/IPerpSeries.sol";
import {ISeriesToken} from "../src/interfaces/ISeriesToken.sol";

/// 6-decimal collateral that re-enters the series from inside transferFrom.
contract ReentrantPerpUSDG is ERC20 {
    PerpSeries public target;

    constructor() ERC20("Reentrant", "RE") {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function setTarget(PerpSeries t) external {
        target = t;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function transferFrom(address from, address to, uint256 value) public override returns (bool) {
        if (address(target) != address(0)) target.claim(from); // must hit the guard
        return super.transferFrom(from, to, value);
    }
}

contract PerpSeriesTest is PerpBase {
    PerpSeries s;
    PerpToken noteT; // read once: a getter call inside vm.prank would use up the prank
    PerpToken writerT;
    uint40 first;

    // one fixing melts m of a token's notional; clean, NOTE gets all of the pair's release
    uint256 constant M1 = RAY * uint256(MELT) / WAD;
    uint256 constant S1 = RAY - M1;

    function setUp() public override {
        super.setUp();
        first = T0 + 1 hours;
        s = _create(_terms(first));
        noteT = _note(s);
        writerT = _writer(s);
    }

    // --- phases and pending fixings -----------------------------------------------

    function test_pending_until_first_fixing_recorded() public {
        PerpState memory st = s.state();
        assertEq(uint8(st.phase), uint8(PerpPhase.Pending));
        assertEq(st.nextFixing, first);
        assertEq(st.notionalPerToken, 0);
        (bool pending, uint40 t) = s.pendingFixing();
        assertFalse(pending);
        assertEq(t, first);

        vm.warp(first + 1);
        (pending, t) = s.pendingFixing();
        assertTrue(pending);
        assertEq(t, first);

        usdg.mint(alice, 10e6);
        vm.startPrank(alice);
        usdg.approve(address(s), type(uint256).max);
        vm.expectRevert(IPerpSeries.NotStruck.selector);
        s.mint(1e6, alice);
        vm.expectRevert(IPerpSeries.NotStruck.selector);
        s.redeemPair(1e6, alice);
        vm.stopPrank();
    }

    function test_first_fixing_never_falls_back() public {
        vm.warp(first + 60 days);
        assertEq(uint8(s.state().phase), uint8(PerpPhase.Pending));
        (bool pending,) = s.pendingFixing();
        assertTrue(pending);
        assertEq(uint8(s.advance().phase), uint8(PerpPhase.Pending));
    }

    function test_first_fixing_then_live() public {
        _fix(s, 0, 10000);
        // state() includes the recorded but unprocessed first fixing
        PerpState memory st = s.state();
        assertEq(uint8(st.phase), uint8(PerpPhase.Live));
        assertEq(st.referenceFixing, INITIAL);
        assertEq(st.lastFixing, INITIAL);
        assertEq(st.nextFixing, first + WEEK);
        assertEq(st.notionalPerToken, RAY);
        assertEq(st.fixingsDone, 0);

        vm.expectEmit(address(s));
        emit IPerpSeries.Struck(INITIAL);
        s.advance();
        (bool pending, uint40 t) = s.pendingFixing();
        assertFalse(pending);
        assertEq(t, first + WEEK);
    }

    function test_fixing_pending_is_reported_from_one_second_after() public {
        _fix(s, 0, 10000);
        vm.warp(first + WEEK); // exactly at fixing 1: not passed yet
        (bool pending,) = s.pendingFixing();
        assertFalse(pending);
        vm.warp(first + WEEK + 1);
        (pending,) = s.pendingFixing();
        assertTrue(pending);
    }

    // --- the fixing rule ----------------------------------------------------------

    function test_clean_fixing_releases_everything_to_note() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 100e6);
        _fix(s, 1, 9000);

        uint256 release = M1 * (UNIT + RESERVE) / UNIT;
        vm.expectEmit(address(s));
        emit IPerpSeries.FixingProcessed(
            1, first + WEEK, uint96(uint256(_price(9000))), INITIAL, false, false, S1, release, 0
        );
        PerpState memory st = s.advance();
        assertEq(st.notionalPerToken, S1);
        assertEq(st.noteIndex, release);
        assertEq(st.writerIndex, 0);
        assertEq(st.referenceFixing, INITIAL); // below the reference: it stays
        assertFalse(st.knockedIn);
        // 100 NOTE: a * (1 + R) = 1.8995% * 1.235 of 100 USDG
        assertEq(s.claimable(alice), 100e6 * release / RAY);
        assertEq(s.claimable(alice), 2_345_926);
    }

    function test_ratchet_moves_the_reference_and_redeems_nothing() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 100e6);
        _fix(s, 1, 12000);
        PerpState memory st = s.advance();
        assertEq(st.referenceFixing, uint96(uint256(_price(12000))));
        assertEq(st.noteIndex, M1 * (UNIT + RESERVE) / UNIT); // the same release as any clean fixing
        assertEq(noteT.totalSupply(), 100e6);
        // the knock-in barrier follows: 60% of the new reference is 72
        _fix(s, 2, 7300);
        assertFalse(s.advance().knockedIn);
        _fix(s, 3, 7100);
        assertTrue(s.advance().knockedIn);
    }

    function test_knocked_in_fixing_splits_the_release() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 100e6);
        _fix(s, 1, 5000); // below 60%: knocked in at this fixing, which already pays x = 0.5
        PerpState memory st = s.advance();
        assertTrue(st.knockedIn);
        uint256 total = M1 * (UNIT + RESERVE) / UNIT;
        uint256 noteRelease =
            M1 * (uint256(_price(5000)) * UNIT + uint256(RESERVE) * INITIAL) / (uint256(INITIAL) * UNIT);
        assertEq(st.noteIndex, noteRelease);
        assertEq(st.writerIndex, total - noteRelease);
        // NOTE: a * (0.5 + 0.235), WRITER: a * 0.5, of 100 USDG
        assertEq(100e6 * st.noteIndex / RAY, 1_396_158);
        assertEq(100e6 * st.writerIndex / RAY, 949_767);

        // stays knocked in above the barrier, pays x
        _fix(s, 2, 9000);
        st = s.advance();
        assertTrue(st.knockedIn);
        // heals at the reference: clean again, the reference stays
        _fix(s, 3, 10000);
        st = s.advance();
        assertFalse(st.knockedIn);
        assertEq(st.referenceFixing, INITIAL);
    }

    function test_fixing_at_the_barrier_does_not_knock_in() public {
        _fix(s, 0, 10000);
        _fix(s, 1, 6000);
        assertFalse(s.advance().knockedIn);
        _fixAt(s, _fixingTime(s, 2), _price(6000) - 1);
        assertTrue(s.advance().knockedIn);
    }

    // --- mint / redeemPair --------------------------------------------------------

    function test_mint_locks_ceil_and_redeemPair_pays_floor() public {
        _fix(s, 0, 10000);
        assertEq(s.pairValuePerNotional(), 1_235_000);
        assertEq(s.previewMint(1e6), 1_235_000);
        assertEq(s.previewMint(3), 4); // 3 * 1.235 = 3.705 -> 4
        assertEq(s.previewRedeemPair(3), 3);
        assertEq(s.notionalOf(1e6), 1e6);

        uint256 inAmt = _mintPairs(s, alice, 3);
        assertEq(inAmt, 4);
        assertEq(noteT.balanceOf(alice), 3);
        assertEq(writerT.balanceOf(alice), 3);

        vm.prank(alice);
        uint256 outAmt = s.redeemPair(3, bob);
        assertEq(outAmt, 3);
        assertEq(usdg.balanceOf(bob), 3);
        assertEq(noteT.totalSupply(), 0);
        assertEq(usdg.balanceOf(address(s)), 1); // the rounding stays in the escrow
    }

    function test_mint_after_fixings_costs_the_melted_pair_value() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 100e6);
        _fix(s, 1, 9000);
        s.advance();
        // a token now stands for (1 - a) of a USDG of notional
        assertEq(s.notionalOf(1e6), 981_004);
        assertEq(s.previewMint(100e6), 121_154_074); // 100 * 0.98100465 * 1.235, rounded up
        uint256 inAmt = _mintPairs(s, bob, 100e6);
        assertEq(inAmt, 121_154_074);
        // the new tokens earned nothing from fixing 1
        assertEq(s.claimable(bob), 0);
        assertEq(s.claimable(alice), 2_345_926);
        // a pair bought and redeemed returns its cost, rounded down
        vm.prank(bob);
        assertEq(s.redeemPair(100e6, bob), 121_154_073);
    }

    function test_zero_amounts_revert() public {
        _fix(s, 0, 10000);
        vm.expectRevert(IPerpSeries.ZeroAmount.selector);
        s.mint(0, alice);
        vm.expectRevert(IPerpSeries.ZeroAmount.selector);
        s.redeemPair(0, alice);
    }

    function test_redeemPair_needs_both_legs() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 10e6);
        vm.startPrank(alice);
        noteT.transfer(bob, 4e6);
        vm.expectRevert(); // ERC20InsufficientBalance on NOTE
        s.redeemPair(7e6, alice);
        assertEq(s.redeemPair(6e6, alice), 7_410_000);
        vm.stopPrank();
    }

    // --- holders: the index is checkpointed on every balance change ----------------

    function test_transfer_settles_both_holders() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 100e6);
        _fix(s, 1, 9000);
        s.advance();
        uint256 rel1 = s.state().noteIndex;

        vm.prank(alice);
        noteT.transfer(bob, 40e6);
        assertEq(s.claimable(alice), 100e6 * rel1 / RAY);
        assertEq(s.claimable(bob), 0);

        _fix(s, 2, 9500);
        s.advance();
        uint256 rel2 = s.state().noteIndex - rel1;
        assertEq(s.claimable(alice), 100e6 * rel1 / RAY + 60e6 * rel2 / RAY);
        assertEq(s.claimable(bob), 40e6 * rel2 / RAY);

        vm.prank(bob);
        assertEq(s.claim(bob), 40e6 * rel2 / RAY);
        assertEq(s.claimable(bob), 0);
        assertEq(usdg.balanceOf(bob), 40e6 * rel2 / RAY);
    }

    function test_transfer_processes_a_recorded_fixing_first() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 100e6);
        _fix(s, 1, 9000); // recorded, not advanced
        vm.expectEmit(true, false, false, false, address(s));
        emit IPerpSeries.FixingProcessed(1, 0, 0, 0, false, false, 0, 0, 0);
        vm.prank(alice);
        noteT.transfer(bob, 100e6);
        // the release of fixing 1 belongs to the holder at the fixing
        assertEq(s.claimable(alice), 2_345_926);
        assertEq(s.claimable(bob), 0);
        assertEq(s.state().fixingsDone, 1);
    }

    function test_writer_leg_is_settled_separately() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 100e6);
        vm.prank(alice);
        writerT.transfer(bob, 100e6); // bob is the hedger, alice keeps NOTE
        _fix(s, 1, 5000);
        s.advance();
        assertEq(s.claimable(alice), 1_396_158);
        assertEq(s.claimable(bob), 949_767);
        // transferFrom goes through the same hook
        uint256 w1 = s.state().writerIndex;
        vm.prank(bob);
        writerT.approve(address(this), 50e6);
        writerT.transferFrom(bob, alice, 50e6);
        _fix(s, 2, 5000);
        uint256 w2 = s.advance().writerIndex - w1;
        assertEq(s.claimable(bob), 949_767 + 50e6 * w2 / RAY);
        assertEq(
            s.claimable(alice),
            1_396_158 + 100e6 * (s.state().noteIndex - 1_396_158 * RAY / 100e6) / RAY + 50e6 * w2 / RAY
        );
    }

    function test_self_transfer_and_zero_transfer_change_nothing() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 100e6);
        _fix(s, 1, 9000);
        vm.startPrank(alice);
        noteT.transfer(alice, 100e6);
        noteT.transfer(bob, 0);
        vm.stopPrank();
        assertEq(s.claimable(alice), 2_345_926);
        assertEq(s.claimable(bob), 0);
    }

    function test_mint_to_a_holder_settles_it_first() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 100e6);
        _fix(s, 1, 9000);
        // bob mints more to alice after the fixing: her old tokens' release is kept, the new ones get none
        uint256 need = s.previewMint(50e6);
        usdg.mint(bob, need);
        vm.startPrank(bob);
        usdg.approve(address(s), need);
        s.mint(50e6, alice);
        vm.stopPrank();
        assertEq(s.claimable(alice), 2_345_926);
    }

    function test_claim_pays_any_address_and_survives_a_frozen_holder() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 100e6);
        _fix(s, 1, 9000);
        usdg.setFrozen(alice, true);
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(MockUSDG.Frozen.selector, alice));
        s.claim(alice);
        vm.expectEmit(address(s));
        emit IPerpSeries.Claimed(alice, bob, 2_345_926);
        vm.prank(alice);
        assertEq(s.claim(bob), 2_345_926);
        assertEq(usdg.balanceOf(bob), 2_345_926);
        // nothing left: a second claim pays 0
        vm.prank(alice);
        assertEq(s.claim(bob), 0);
    }

    function test_checkpoint_is_only_for_the_tokens() public {
        _fix(s, 0, 10000);
        vm.expectRevert(IPerpSeries.OnlyToken.selector);
        s.checkpoint(alice, 1e6, bob, 0);
    }

    function test_tokens_mint_and_burn_only_through_the_series() public {
        PerpToken n = _note(s);
        vm.expectRevert(ISeriesToken.OnlySeries.selector);
        n.mint(alice, 1);
        vm.expectRevert(ISeriesToken.OnlySeries.selector);
        n.burn(alice, 1);
        assertEq(n.decimals(), 6);
        assertTrue(n.isNote());
        assertFalse(writerT.isNote());
        assertEq(n.series(), address(s));
    }

    // --- missed fixings and the end -------------------------------------------------

    function test_missed_fixing_reuses_the_last_one_after_the_deadline() public {
        _fix(s, 0, 10000);
        _mintPairs(s, alice, 100e6);
        _fix(s, 1, 5000); // knocked in at 50
        s.advance();
        uint40 t2 = _fixingTime(s, 2);
        uint256 deadline = uint256(t2) + 8 days + 1 days;
        vm.warp(deadline - 1);
        (bool pending,) = s.pendingFixing();
        assertTrue(pending);
        assertEq(s.state().fixingsDone, 1);

        vm.warp(deadline);
        PerpState memory st = s.state();
        assertEq(st.fixingsDone, 2);
        assertEq(st.missedInARow, 1);
        assertEq(st.lastFixing, uint96(uint256(_price(5000))));
        assertTrue(st.knockedIn);
        vm.expectEmit(true, false, false, false, address(s));
        emit IPerpSeries.FixingProcessed(2, 0, 0, 0, false, false, 0, 0, 0);
        s.advance();
        // fixing 3 is already past its time too, but not past its own deadline
        (pending,) = s.pendingFixing();
        assertTrue(pending);
    }

    function test_recorded_fixing_wins_over_the_fallback() public {
        _fix(s, 0, 10000);
        uint40 t1 = _fixingTime(s, 1);
        feed.pushRoundAt(_price(5000), t1);
        uint80 round = uint80(feed.latestRound());
        vm.warp(uint256(t1) + 30 days); // far past the deadline, nobody advanced
        assertFalse(s.state().knockedIn); // the fallback would reuse 100
        _recorder(s).recordFixing(t1, round);
        PerpState memory st = s.state();
        assertTrue(st.knockedIn);
        assertEq(st.lastFixing, uint96(uint256(_price(5000))));
    }

    function test_four_missed_fixings_close_and_release_everything() public {
        _fix(s, 0, 10000);
        uint256 locked = _mintPairs(s, alice, 100e6);
        vm.prank(alice);
        writerT.transfer(bob, 100e6);
        _fix(s, 1, 5000);
        s.advance();

        // the feed dies: fixings 2..5 are never recorded
        vm.warp(uint256(_fixingTime(s, 5)) + 9 days - 1);
        PerpState memory st = s.state();
        assertEq(uint8(st.phase), uint8(PerpPhase.Live));
        assertEq(st.missedInARow, 3);

        vm.warp(uint256(_fixingTime(s, 5)) + 9 days);
        vm.expectEmit(address(s));
        emit IPerpSeries.Closed(5, uint96(uint256(_price(5000))));
        st = s.advance();
        assertEq(uint8(st.phase), uint8(PerpPhase.Closed));
        assertEq(st.notionalPerToken, 0);
        assertEq(st.nextFixing, 0);
        assertEq(st.missedInARow, 4);
        (bool pending, uint40 t) = s.pendingFixing();
        assertFalse(pending);
        assertEq(t, 0);

        // everything was released at the last good fixing, x = 0.5: NOTE 0.5 + R, WRITER 0.5 per notional
        uint256 noteCash = s.claimable(alice);
        uint256 writerCash = s.claimable(bob);
        assertApproxEqAbs(noteCash, 73_500_000, 10);
        assertApproxEqAbs(writerCash, 50_000_000, 10);
        assertLe(noteCash + writerCash, locked);
        assertGe(noteCash + writerCash + 10, locked);

        // closed: no mint, a pair redeems for nothing, claims still pay
        usdg.mint(alice, 10e6);
        vm.startPrank(alice);
        usdg.approve(address(s), 10e6);
        vm.expectRevert(IPerpSeries.SeriesClosed.selector);
        s.mint(1e6, alice);
        assertEq(s.claim(alice), noteCash);
        vm.stopPrank();
        vm.prank(bob);
        assertEq(s.claim(bob), writerCash);
        assertEq(s.previewRedeemPair(100e6), 0);
        // later fixings change nothing
        vm.warp(block.timestamp + 365 days);
        assertEq(keccak256(abi.encode(s.advance())), keccak256(abi.encode(st)));
    }

    function test_a_recorded_fixing_resets_the_missed_count() public {
        _fix(s, 0, 10000);
        vm.warp(uint256(_fixingTime(s, 3)) + 9 days);
        assertEq(s.state().missedInARow, 3);
        _fix(s, 4, 9000);
        PerpState memory st = s.advance();
        assertEq(st.missedInARow, 0);
        assertEq(st.fixingsDone, 4);
        assertEq(uint8(st.phase), uint8(PerpPhase.Live));
    }

    // --- advance in chunks ----------------------------------------------------------

    function test_advanceBy_catches_up_in_steps() public {
        for (uint256 n = 0; n <= 6; n++) {
            _fix(s, n, 9000 + n * 50);
        }
        PerpState memory want = s.state(); // the view replays everything
        assertEq(want.fixingsDone, 6);
        // advanceBy returns what is stored after its steps
        assertEq(uint8(s.advanceBy(0).phase), uint8(PerpPhase.Pending));
        PerpState memory st = s.advanceBy(1); // the first fixing is one step
        assertEq(uint8(st.phase), uint8(PerpPhase.Live));
        assertEq(st.fixingsDone, 0);
        assertEq(s.advanceBy(2).fixingsDone, 2);
        assertEq(s.advanceBy(3).fixingsDone, 5);
        assertEq(keccak256(abi.encode(s.state())), keccak256(abi.encode(want)));
        PerpState memory got = s.advanceBy(100);
        assertEq(keccak256(abi.encode(got)), keccak256(abi.encode(want)));
    }

    // --- guards -----------------------------------------------------------------------

    function test_reentrant_collateral_is_blocked() public {
        ReentrantPerpUSDG re = new ReentrantPerpUSDG();
        PerpFactory f2 = new PerpFactory(address(re), new SeriesFactory(address(re)));
        PerpSeries s2 = PerpSeries(f2.createSeries(_terms(first)));
        _fixAt(s2, first, _price(10000));
        re.mint(alice, 10e6);
        vm.startPrank(alice);
        re.approve(address(s2), type(uint256).max);
        re.setTarget(s2);
        vm.expectRevert(ReentrancyGuard.ReentrancyGuardReentrantCall.selector);
        s2.mint(1e6, alice);
        vm.stopPrank();
    }

    function test_implementation_and_clones_cannot_be_reinitialized() public {
        PerpSeries impl = PerpSeries(factory.seriesImplementation());
        vm.expectRevert(PerpSeries.OnlyFactory.selector);
        impl.initialize(bytes32(uint256(2)), _terms(first), address(1), address(2), address(3), address(usdg));
        vm.prank(address(factory));
        vm.expectRevert(PerpSeries.AlreadyInitialized.selector);
        impl.initialize(bytes32(uint256(2)), _terms(first), address(1), address(2), address(3), address(usdg));
        vm.prank(address(factory));
        vm.expectRevert(PerpSeries.AlreadyInitialized.selector);
        s.initialize(bytes32(uint256(2)), _terms(first), address(1), address(2), address(3), address(usdg));

        PerpToken tokenImpl = PerpToken(factory.tokenImplementation());
        vm.expectRevert(PerpToken.OnlyFactory.selector);
        tokenImpl.initialize(address(this), true, 6, "x", "x");
        vm.prank(address(factory));
        vm.expectRevert(PerpToken.AlreadyInitialized.selector);
        tokenImpl.initialize(address(this), true, 6, "x", "x");
    }

    // --- fuzz: nobody is paid more than was locked, and almost all of it is paid ----

    function testFuzz_path_conserves_collateral(uint256 seed, uint8 fixings, uint64 amountA, uint64 amountB) public {
        fixings = uint8(bound(fixings, 1, 40));
        uint256 a = bound(amountA, 1, 1e12);
        uint256 b = bound(amountB, 1, 1e12);
        _fix(s, 0, 10000);
        uint256 lockedIn = _mintPairs(s, alice, a);
        vm.prank(alice);
        writerT.transfer(bob, a); // alice is long NOTE, bob long WRITER

        uint256 paidOut;
        uint256 price = 10000;
        for (uint256 n = 1; n <= fixings; n++) {
            seed = uint256(keccak256(abi.encode(seed)));
            price = bound(price * (7000 + seed % 6500) / 10000, 100, 60000);
            if (seed % 11 != 0) _fix(s, n, price); // sometimes nobody records: the deadline decides later
            else vm.warp(uint256(_fixingTime(s, n)) + 9 days);
            // four unrecorded fixings in a row close the series: nothing mints after that
            if (seed % 5 == 0 && s.state().phase == PerpPhase.Live) lockedIn += _mintPairs(s, bob, b);
            if (seed % 7 == 0) {
                vm.prank(alice);
                paidOut += s.claim(alice);
            }
        }
        vm.warp(block.timestamp + 9 days);
        s.advance();

        // everybody claims, then whoever can pair up redeems
        vm.prank(alice);
        paidOut += s.claim(alice);
        vm.prank(bob);
        paidOut += s.claim(bob);
        uint256 bobNote = noteT.balanceOf(bob);
        if (bobNote != 0) {
            vm.prank(bob);
            paidOut += s.redeemPair(bobNote, bob);
        }
        vm.prank(bob);
        writerT.transfer(alice, a);
        vm.prank(alice);
        paidOut += s.redeemPair(a, alice);

        assertEq(noteT.totalSupply(), 0);
        assertEq(writerT.totalSupply(), 0);
        assertEq(usdg.balanceOf(address(s)), lockedIn - paidOut);
        // what stays behind is rounding: under one base unit per mint, claim, leg and fixing
        assertLe(lockedIn - paidOut, 6 + 3 * uint256(fixings));
        assertEq(s.claimable(alice), 0);
        assertEq(s.claimable(bob), 0);
    }
}
