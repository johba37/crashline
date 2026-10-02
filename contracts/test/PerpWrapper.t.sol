// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {PerpDeskFixture, MockWeekendSource} from "./PerpDeskFixture.t.sol";
import {PerpWrapper} from "../src/PerpWrapper.sol";
import {MockUSDG} from "../src/MockUSDG.sol";
import {PerpSeries} from "../src/PerpSeries.sol";
import {IPerpDesk} from "../src/interfaces/IPerpDesk.sol";
import {IPerpQuoter} from "../src/interfaces/IPerpQuoter.sol";
import {IPerpSeries, PerpPhase} from "../src/interfaces/IPerpSeries.sol";

/// A contract that holds whatever it is given and never claims anything: an AMM pool, a lending market.
contract PassiveHolder {
    function give(IERC20 token, address to, uint256 amount) external {
        token.transfer(to, amount);
    }
}

contract PerpWrapperTest is PerpDeskFixture {
    PerpWrapper wNote;
    PerpWrapper wWriter;
    address hedger = makeAddr("hedger");

    function setUp() public override {
        super.setUp();
        wNote = new PerpWrapper(desk, s, true);
        wWriter = new PerpWrapper(desk, s, false);
        _mintPairs(s, alice, 1_000e6); // alice holds both legs
        vm.startPrank(alice);
        noteT.approve(address(wNote), type(uint256).max);
        writerT.approve(address(wWriter), type(uint256).max);
        vm.stopPrank();
    }

    function test_metadata() public {
        assertEq(wNote.decimals(), 12);
        assertEq(address(wNote.token()), address(noteT));
        assertEq(address(wWriter.token()), address(writerT));
        assertTrue(wNote.isNote());
        assertFalse(wWriter.isNote());
        assertEq(wNote.name(), "Wrapped Perpetual NOTE");
        assertEq(wWriter.name(), "Wrapped Perpetual WRITER");
        assertEq(bytes(wNote.symbol()).length, bytes("wpNOTE-0x00000000").length);
        vm.expectRevert(abi.encodeWithSelector(PerpWrapper.NotFactorySeries.selector, address(this)));
        new PerpWrapper(desk, IPerpSeries(address(this)), true);
    }

    function test_wrap_and_unwrap_before_any_fixing_is_one_to_one() public {
        vm.startPrank(alice);
        vm.expectEmit(address(wNote));
        emit PerpWrapper.Wrapped(alice, alice, 100e6, 100e12);
        assertEq(wNote.wrap(100e6, alice), 100e12); // a million shares per token base unit
        assertEq(wNote.totalTokens(), 100e6);
        (uint256 amount, uint256 cash) = wNote.previewUnwrap(100e12);
        assertEq(amount, 100e6);
        assertEq(cash, 0);
        (amount, cash) = wNote.unwrap(40e12, bob);
        vm.stopPrank();
        assertEq(amount, 40e6);
        assertEq(cash, 0);
        assertEq(noteT.balanceOf(bob), 40e6);
        assertEq(wNote.balanceOf(alice), 60e12);
    }

    function test_zero_amounts_revert_and_spendCash_is_internal() public {
        vm.expectRevert(PerpWrapper.ZeroAmount.selector);
        wNote.wrap(0, alice);
        vm.expectRevert(PerpWrapper.ZeroAmount.selector);
        wNote.unwrap(0, alice);
        vm.expectRevert(PerpWrapper.OnlySelf.selector);
        wNote.spendCash();
    }

    function test_compound_turns_released_cash_into_more_tokens() public {
        vm.prank(alice);
        wNote.wrap(100e6, alice);
        _fixing(1, 9000); // releases a * (1 + R) per unit of notional to NOTE
        assertEq(wNote.pendingCash(), 2_345_926);
        (, uint256 cashShare) = wNote.previewUnwrap(100e12);
        assertEq(cashShare, 2_345_926 - 1); // the virtual shares hold a sliver

        uint256 ask = _mid();
        uint256 perToken = s.state().notionalPerToken;
        uint256 expected = uint256(2_345_926) * RAY * 10_000 / (perToken * ask);
        vm.expectEmit(false, false, false, false, address(wNote));
        emit PerpWrapper.Compounded(0, 0);
        assertEq(wNote.compound(), expected);
        assertEq(wNote.totalTokens(), 100e6 + expected);
        assertLt(usdg.balanceOf(address(wNote)), 2); // what one more base unit of token would cost
        assertEq(s.claimable(address(wNote)), 0);
        // a share now stands for more tokens (the virtual shares hold one base unit)
        (uint256 amount,) = wNote.previewUnwrap(100e12);
        assertEq(amount, 100e6 + expected - 1);
        // nothing left to compound
        assertEq(wNote.compound(), 0);
    }

    /// The wrapper does for a holder that never claims exactly what an active holder does
    /// by claiming after every fixing and buying more NOTE at the Desk.
    function test_a_holder_that_never_claims_keeps_its_value_through_the_wrapper() public {
        PassiveHolder pool = new PassiveHolder(); // alice wraps and parks the shares in a pool
        vm.startPrank(alice);
        wNote.wrap(100e6, address(pool));
        noteT.transfer(bob, 100e6); // bob holds NOTE directly
        vm.stopPrank();
        desk.setSpread(address(s), 0, 20, 0); // both pay the Desk's ask
        earnings = T0 + 90 days; // beyond the eight weeks below
        desk.setEarnings(address(s), earnings);
        vm.prank(bob);
        usdg.approve(address(desk), type(uint256).max);

        uint256 claimed;
        for (uint256 n = 1; n <= 8; n++) {
            _fixing(n, 9000 + n * 40);
            wNote.compound(); // any keeper
            // bob does the same by hand
            uint256 perToken = s.state().notionalPerToken;
            (, uint16 ask) = desk.quote(address(s), IPerpDesk.Side.BuyNote, 1e6, 0);
            vm.startPrank(bob);
            claimed += s.claim(bob);
            uint256 cash = usdg.balanceOf(bob);
            desk.buy(address(s), cash * RAY * 10_000 / (perToken * ask), cash, 0, address(0), bob);
            vm.stopPrank();
        }
        assertGt(claimed, 17e6); // eight fixings of ~2.3% of the notional
        // the pool never claimed and never will: through the wrapper nothing is stranded
        assertEq(s.claimable(address(pool)), 0);
        assertEq(s.claimable(address(wNote)), 0);

        pool.give(IERC20(address(wNote)), alice, 100e12);
        vm.prank(alice);
        (uint256 tokens, uint256 cashOut) = wNote.unwrap(100e12, alice);
        assertGt(tokens, 117e6);
        assertApproxEqAbs(tokens, noteT.balanceOf(bob), 20);
        assertLt(cashOut, 20);
        assertLt(usdg.balanceOf(bob), 20);
    }

    function test_wrap_compounds_first_and_refuses_while_cash_is_pending() public {
        vm.prank(alice);
        wNote.wrap(100e6, alice);
        _fixing(1, 9000);
        // the feed goes stale (a weekday): the Desk can't reinvest
        vm.warp(block.timestamp + 27 hours);
        vm.expectRevert(abi.encodeWithSelector(PerpWrapper.CashPending.selector, uint256(2_345_926)));
        vm.prank(alice);
        wNote.wrap(100e6, alice);
        vm.expectRevert(); // compound() itself reports the Desk's reason: FeedStale
        wNote.compound();

        // leaving always works, with the leaver's part of the cash
        vm.prank(alice);
        (uint256 tokens, uint256 cash) = wNote.unwrap(50e12, alice);
        assertEq(tokens, 50e6);
        assertApproxEqAbs(cash, 2_345_926 / 2, 1);

        // the feed is back: a wrap reinvests the rest first, then joins at the new ratio
        _spot(9000);
        vm.prank(alice);
        uint256 shares = wNote.wrap(50e6, alice);
        assertLt(shares, 50e12); // a share stands for more than a millionth of a token base unit now
        assertGt(wNote.totalTokens(), 100e6);
        assertLt(usdg.balanceOf(address(wNote)), 2);
    }

    function test_a_full_cap_at_the_desk_leaves_the_cash_waiting() public {
        vm.prank(alice);
        wNote.wrap(100e6, alice);
        desk.listSeries(address(s), pricer, VOL, earnings, 0); // no room for the Desk to hold more WRITER
        _fixing(1, 9000);
        vm.expectRevert(abi.encodeWithSelector(PerpWrapper.CashPending.selector, uint256(2_345_926)));
        vm.prank(alice);
        wNote.wrap(1e6, alice);
        vm.expectRevert(); // CapExceeded
        wNote.compound();
        desk.listSeries(address(s), pricer, VOL, earnings, CAP);
        assertGt(wNote.compound(), 0);
    }

    function test_never_buys_at_the_weekend_price() public {
        MockWeekendSource source = new MockWeekendSource();
        desk.setWeekend(address(feed), source, 300, 50);
        vm.prank(alice);
        wNote.wrap(100e6, alice);
        _fixing(1, 9000);
        // Friday's close (Saturday 00:00 UTC), then Sunday: the Desk would sell at the weekend price
        uint256 close = (uint256(T0) / 1 days) * 1 days + 7 days + 5 days;
        vm.warp(close);
        _spot(9000);
        vm.warp(close + 34 hours);
        source.set(address(feed), uint256(_price(9000)));
        (, bool weekendPrice) = desk.spotOf(address(s));
        assertTrue(weekendPrice);
        assertGt(_quoteOf(IPerpDesk.Side.BuyNote, 1e6, 0), 0);
        assertEq(wNote.compound(), 0);
        assertEq(usdg.balanceOf(address(wNote)), 2_345_926);
    }

    function test_writer_wrapper_reinvests_what_a_knock_in_pays() public {
        vm.prank(alice);
        wWriter.wrap(100e6, alice);
        _fixing(1, 9000);
        assertEq(wWriter.pendingCash(), 0); // a clean fixing pays WRITER nothing
        _fixing(2, 5000); // knocked in at 50: WRITER gets a * (1 - 0.5) per unit of notional
        uint256 cash = wWriter.pendingCash();
        assertGt(cash, 900_000);
        uint256 bought = wWriter.compound(); // buys cover
        assertGt(bought, 0);
        assertEq(wWriter.totalTokens(), 100e6 + bought);
        assertEq(noteT.balanceOf(address(desk)), bought); // the Desk kept the NOTE of those pairs
    }

    function test_a_closed_series_unwraps_into_its_cash() public {
        vm.prank(alice);
        wNote.wrap(100e6, alice);
        _fixing(1, 9000);
        wNote.compound();
        uint256 tokens = wNote.totalTokens();
        vm.warp(uint256(_fixingTime(s, 5)) + 9 days); // the feed died: closed, everything released
        assertEq(uint8(s.state().phase), uint8(PerpPhase.Closed));
        vm.prank(alice);
        (uint256 amount, uint256 cash) = wNote.unwrap(100e12, alice);
        assertEq(amount, tokens - 1); // worthless now (the virtual shares hold one base unit)
        // clean at the last good fixing: 1 + R per unit of the notional left after one fixing
        assertApproxEqAbs(cash, tokens * (RAY - RAY * uint256(MELT) / WAD) / RAY * 1_235_000 / 1e6, 5);
    }

    function test_a_donation_cannot_take_a_later_deposit() public {
        // the attacker is first, with one base unit, and donates 1,000 tokens to inflate the ratio
        vm.startPrank(alice);
        wNote.wrap(1, alice);
        noteT.transfer(address(wNote), 500e6);
        vm.stopPrank();
        _mintPairs(s, bob, 100e6);
        vm.startPrank(bob);
        noteT.approve(address(wNote), 100e6);
        uint256 shares = wNote.wrap(100e6, bob);
        assertGt(shares, 0);
        (uint256 back,) = wNote.unwrap(shares, bob);
        vm.stopPrank();
        assertGe(back, 100e6 - 300); // rounding costs the victim a few base units
        // the attacker's one unit gets back half of its own donation: the virtual shares keep the rest
        vm.prank(alice);
        (uint256 attackerBack,) = wNote.unwrap(1e6, alice);
        assertLt(attackerBack, 251e6);
    }

    /// One base unit of USDG buys no token; it must not hold up wraps on a small wrapper either.
    function test_a_base_unit_of_usdg_does_not_block_a_small_wrapper() public {
        _mintPairs(s, bob, 100e6);
        vm.prank(alice);
        wNote.wrap(1, alice); // one base unit wrapped: its notional rounds the 1 bps threshold to 0
        usdg.mint(address(wNote), 1);
        vm.startPrank(bob);
        noteT.approve(address(wNote), 100e6);
        assertGt(wNote.wrap(100e6, bob), 0);
        vm.stopPrank();
    }

    /// While USDG is paused, or the wrapper's address is frozen, the cash can't move, but
    /// the tokens can: exitTokens hands them back and touches no USDG.
    function test_exitTokens_works_while_usdg_is_frozen_or_paused() public {
        vm.prank(alice);
        uint256 shares = wNote.wrap(100e6, alice);
        _fixing(1, 9000); // USDG is released to the wrapper's tokens

        usdg.setFrozen(address(wNote), true);
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(MockUSDG.Frozen.selector, address(wNote)));
        wNote.unwrap(shares, alice);
        vm.prank(alice);
        assertEq(wNote.exitTokens(shares / 2, bob), 50e6);
        assertEq(noteT.balanceOf(bob), 50e6);

        usdg.setFrozen(address(wNote), false);
        usdg.setPaused(true);
        vm.prank(alice);
        vm.expectRevert(MockUSDG.Paused.selector);
        wNote.unwrap(shares / 2, alice);
        vm.expectRevert(PerpWrapper.ZeroAmount.selector);
        wNote.exitTokens(0, alice);
        vm.prank(alice);
        assertEq(wNote.exitTokens(shares / 2, bob), 50e6);
        assertEq(wNote.totalSupply(), 0);
        // the USDG the fixing released stays claimable for whoever holds shares later
        assertEq(s.claimable(address(wNote)), 2_345_926);
    }

    function test_usdg_sent_to_an_empty_wrapper_does_not_block_it() public {
        // one base unit buys no token, and with no shares it is nobody's: the first wrap goes through
        usdg.mint(address(wNote), 1);
        vm.prank(alice);
        assertEq(wNote.wrap(100e6, alice), 100e12);
        // a real amount is reinvested by the first wrap and ends up with the first holder
        usdg.mint(address(wWriter), 50e6);
        vm.prank(alice);
        wWriter.wrap(100e6, alice);
        assertGt(wWriter.totalTokens(), 300e6); // 50 USDG of cover at 0.2154 per unit, plus the 100 wrapped
        assertLt(usdg.balanceOf(address(wWriter)), 2);
    }

    function testFuzz_wrap_unwrap_round_trip(uint256 amount, uint256 fixings) public {
        amount = bound(amount, 1, 1_000e6);
        fixings = bound(fixings, 0, 3);
        vm.prank(alice);
        wNote.wrap(1e6, alice); // someone is in already
        for (uint256 n = 1; n <= fixings; n++) {
            _fixing(n, 9000);
        }
        _mintPairs(s, bob, amount);
        vm.startPrank(bob);
        noteT.approve(address(wNote), amount);
        uint256 shares = wNote.previewWrap(amount);
        if (shares == 0) {
            vm.expectRevert(PerpWrapper.ZeroAmount.selector);
            wNote.wrap(amount, bob);
            return;
        }
        shares = wNote.wrap(amount, bob);
        (uint256 back, uint256 cash) = wNote.unwrap(shares, bob);
        vm.stopPrank();
        assertLe(back, amount);
        assertGe(back + 1 + amount / 1e5, amount); // rounding only
        assertLt(cash, 20);
    }
}
