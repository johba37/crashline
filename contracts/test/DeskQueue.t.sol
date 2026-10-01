// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import {DeskFixture} from "./DeskFixture.t.sol";
import {MockPricer} from "./mocks/MockPricer.sol";
import {IDeskQueue} from "../src/interfaces/IDeskQueue.sol";
import {INoteQuoter} from "../src/interfaces/INoteQuoter.sol";

/// The LP redemption queue (IDeskQueue). Fixture: the LP holds all shares of a
/// vault with 1,000,000 USDG; one USDG base unit = 1e6 shares at the start.
contract DeskQueueTest is DeskFixture {
    uint256 constant SHARE = 1e6; // shares per USDG base unit at the first deposit
    address lp2 = makeAddr("lp2");

    function _request(address who, uint256 shares) internal returns (uint256 id) {
        vm.prank(who);
        id = desk.requestRedeem(shares);
    }

    function _claim(address who) internal returns (uint256 assets) {
        vm.prank(who);
        assets = desk.claim(who);
    }

    function _sharePrice() internal view returns (uint256) {
        return desk.convertToAssets(1e6 * SHARE); // USDG base units per share-of-one-USDG
    }

    // --- request, fill, claim -------------------------------------------------------

    function test_request_fill_claim() public {
        uint256 shares = 100_000e6 * SHARE;
        vm.expectEmit(address(desk));
        emit IDeskQueue.RedeemRequested(0, lp, shares);
        uint256 id = _request(lp, shares);
        assertEq(id, 0);
        assertEq(desk.queuedShares(), shares);
        assertEq(desk.balanceOf(address(desk)), shares, "the shares wait in the Desk");
        assertEq(desk.balanceOf(lp), 900_000e6 * SHARE);
        assertEq(desk.totalAssets(), LP_CAPITAL, "queued shares are still shares");
        (address owner_, uint256 left) = desk.redeemRequest(0);
        assertEq(owner_, lp);
        assertEq(left, shares);

        vm.expectEmit(address(desk));
        emit IDeskQueue.RedeemFilled(0, lp, shares, 100_000e6);
        vm.prank(bob); // anyone
        (uint256 filled, uint256 setAside) = desk.processQueue(10);
        assertEq(filled, shares);
        assertEq(setAside, 100_000e6);
        assertEq(desk.queuedShares(), 0);
        assertEq(desk.totalSupply(), 900_000e6 * SHARE, "filled shares are burned");
        assertEq(desk.reservedAssets(), 100_000e6);
        assertEq(desk.claimableAssets(lp), 100_000e6);
        assertEq(desk.totalAssets(), 900_000e6, "USDG set aside is no longer the vault's");
        (uint256 head, uint256 length) = desk.queue();
        assertEq(head, 1);
        assertEq(length, 1);

        vm.expectEmit(address(desk));
        emit IDeskQueue.RedeemClaimed(lp, alice, 100_000e6);
        vm.prank(lp);
        assertEq(desk.claim(alice), 100_000e6); // to any address: USDG can freeze one account
        assertEq(usdg.balanceOf(alice), 100_000e6);
        assertEq(desk.reservedAssets(), 0);
        vm.prank(lp);
        vm.expectRevert(IDeskQueue.NothingToClaim.selector);
        desk.claim(lp);
    }

    function test_request_errors_and_cancel() public {
        assertEq(desk.MIN_REQUEST_SHARES(), 10e6 * SHARE);
        vm.prank(lp);
        vm.expectRevert(abi.encodeWithSelector(IDeskQueue.RequestTooSmall.selector, 10e6 * SHARE - 1, 10e6 * SHARE));
        desk.requestRedeem(10e6 * SHARE - 1);

        uint256 id = _request(lp, 50e6 * SHARE);
        vm.prank(bob);
        vm.expectRevert(abi.encodeWithSelector(IDeskQueue.NotRequestOwner.selector, id));
        desk.cancelRedeem(id);
        vm.prank(lp);
        vm.expectRevert(abi.encodeWithSelector(IDeskQueue.NotRequestOwner.selector, uint256(7)));
        desk.cancelRedeem(7);

        uint256 before = desk.balanceOf(lp);
        vm.expectEmit(address(desk));
        emit IDeskQueue.RedeemCancelled(id, lp, 50e6 * SHARE);
        vm.prank(lp);
        assertEq(desk.cancelRedeem(id), 50e6 * SHARE);
        assertEq(desk.balanceOf(lp), before + 50e6 * SHARE);
        assertEq(desk.queuedShares(), 0);
        // a cancelled request is skipped and the head moves past it
        (uint256 filled,) = desk.processQueue(10);
        assertEq(filled, 0);
        (uint256 head,) = desk.queue();
        assertEq(head, 1);
    }

    // --- the states where ERC-4626 withdrawals stop ---------------------------------

    function test_request_on_a_weekend_is_paid_when_the_market_reopens() public {
        _buyCover(bob, 100_000e6, 0);
        vm.warp(block.timestamp + 27 hours); // feed stale: no share price
        assertEq(desk.maxRedeem(lp), 0);
        uint256 shares = 50_000e6 * SHARE;
        _request(lp, shares); // accepted anyway

        vm.expectRevert(abi.encodeWithSelector(INoteQuoter.FeedStale.selector, uint40(block.timestamp - 27 hours)));
        desk.processQueue(10);

        // Monday: the stock opened lower and the model marks the NOTE down. The request
        // is filled at that price, not at Friday's.
        _spot(8000);
        pricer.setPrice(9000);
        uint256 nav = desk.totalAssets();
        assertEq(nav, LP_CAPITAL - 100_000e6 * (9500 - 9004) / 10_000); // 9000 + 4 bps accrued over 27 h
        (, uint256 setAside) = desk.processQueue(10);
        assertEq(setAside, 50_000e6 * nav / LP_CAPITAL);
        assertEq(_claim(lp), setAside);
    }

    function test_request_in_a_band_or_with_a_pending_fixing() public {
        _buyCover(bob, 1_000e6, 0);
        pricer.setRefusal(MockPricer.Mode.UncertifiedErr, 0, 0); // observation-day band
        _request(lp, 1_000e6 * SHARE);
        vm.expectRevert();
        desk.processQueue(10);
        pricer.setPrice(9500);
        uint80 r1 = _pendingObs1(); // observation 1 passed, fixing not recorded
        _request(lp, 1_000e6 * SHARE);
        vm.expectRevert(abi.encodeWithSelector(INoteQuoter.FixingPending.selector, _obsTime(s, 1)));
        desk.processQueue(10);
        _recorder(s).recordFixing(_obsTime(s, 1), r1);
        (uint256 filled,) = desk.processQueue(10);
        assertEq(filled, 2_000e6 * SHARE);
    }

    // --- first in, first out; partial fills ------------------------------------------

    function test_fifo_and_partial_fill_when_usdg_is_locked() public {
        _deposit(lp2, 100_000e6);
        _buyCover(bob, 1_100_000e6, 0); // locks 1,045,000 of the 1,100,000: idle 55,000
        uint256 idle = usdg.balanceOf(address(desk));
        assertEq(idle, 55_000e6);

        _request(lp, 40_000e6 * SHARE);
        _request(lp2, 40_000e6 * SHARE);
        (uint256 filled, uint256 setAside) = desk.processQueue(10);
        assertEq(setAside, 55_000e6, "all idle USDG goes to the queue");
        assertEq(filled, 55_000e6 * SHARE);
        assertEq(desk.claimableAssets(lp), 40_000e6, "first in, first out");
        assertEq(desk.claimableAssets(lp2), 15_000e6, "the second request is filled in part");
        (, uint256 left) = desk.redeemRequest(1);
        assertEq(left, 25_000e6 * SHARE);
        (uint256 head,) = desk.queue();
        assertEq(head, 1);
        assertEq(desk.queuedShares(), 25_000e6 * SHARE);

        // nothing free: another call fills nothing
        (filled,) = desk.processQueue(10);
        assertEq(filled, 0);

        // the note autocalls; its collateral comes back and pays the rest
        _fix(s, 1, 10_200);
        desk.collect(address(s));
        uint256 worth = desk.convertToAssets(25_000e6 * SHARE);
        assertGt(worth, 25_000e6, "the shares earned the premium while they waited");
        (, setAside) = desk.processQueue(10);
        assertEq(setAside, worth);
        assertEq(desk.queuedShares(), 0);
        assertEq(_claim(lp), 40_000e6);
        assertEq(_claim(lp2), 15_000e6 + setAside);
    }

    // --- the queue comes first -------------------------------------------------------

    function test_no_sync_withdrawal_ahead_of_the_queue() public {
        _deposit(lp2, 1_000e6);
        _request(lp, 10_000e6 * SHARE);
        assertEq(desk.maxWithdraw(lp2), 0);
        assertEq(desk.maxRedeem(lp2), 0);
        vm.prank(lp2);
        vm.expectRevert(abi.encodeWithSelector(ERC4626.ERC4626ExceededMaxWithdraw.selector, lp2, 1e6, 0));
        desk.withdraw(1e6, lp2, lp2);
        desk.processQueue(10);
        assertEq(desk.maxWithdraw(lp2), 1_000e6);
        vm.prank(lp2);
        desk.withdraw(1_000e6, lp2, lp2);
    }

    function test_a_trade_that_adds_risk_pays_the_queue_first() public {
        _request(lp, 10_000e6 * SHARE);
        _buyCover(bob, 1_000e6, 0);
        assertEq(desk.queuedShares(), 0);
        assertEq(desk.claimableAssets(lp), 10_000e6);
    }

    function test_no_new_risk_while_the_queue_cannot_be_paid() public {
        _buyCover(bob, 1_000_000e6, 0); // idle 50,000
        _request(lp, 200_000e6 * SHARE);

        // selling more cover would need the USDG the queue is waiting for
        usdg.mint(bob, 1_000e6);
        vm.startPrank(bob);
        usdg.approve(address(desk), 1_000e6);
        vm.expectRevert(abi.encodeWithSelector(IDeskQueue.QueuePending.selector, 150_000e6 * SHARE));
        desk.buyCover(address(s), 1_000e6, 1_000e6, 0, integrator, bob);
        vm.stopPrank();
        assertEq(desk.claimableAssets(lp), 0, "the revert undid the partial fill");

        // trades that shrink the position still work and free USDG for the queue
        _buy(alice, 100_000e6, 0); // a NOTE buyer takes 100,000 NOTE for 95,000 USDG
        _sellCover(bob, 100_000e6, 0); // cover back: 100,000 pairs redeemed
        (uint256 filled,) = desk.processQueue(10);
        assertEq(filled, 200_000e6 * SHARE);
        _buyCover(bob, 1_000e6, 0); // open again
    }

    function test_dust_requests_beyond_the_batch_need_a_keeper() public {
        for (uint256 i = 0; i < 9; i++) {
            _request(lp, 10e6 * SHARE);
        }
        assertEq(desk.QUEUE_BATCH(), 8);
        usdg.mint(bob, 1_000e6);
        vm.startPrank(bob);
        usdg.approve(address(desk), 1_000e6);
        vm.expectRevert(abi.encodeWithSelector(IDeskQueue.QueuePending.selector, 10e6 * SHARE));
        desk.buyCover(address(s), 1_000e6, 1_000e6, 0, integrator, bob);
        vm.stopPrank();
        desk.processQueue(1);
        _buyCover(bob, 1_000e6, 0); // fills the other 8 itself
        assertEq(desk.claimableAssets(lp), 90e6);
    }

    // --- USDG set aside is off limits -------------------------------------------------

    function test_reserved_usdg_is_not_idle() public {
        _request(lp, 900_000e6 * SHARE);
        desk.processQueue(10);
        assertEq(usdg.balanceOf(address(desk)), LP_CAPITAL);
        assertEq(desk.totalAssets(), 100_000e6);
        assertEq(desk.maxWithdraw(lp), 100_000e6);
        (, uint256 limit) = desk.risk(address(feed));
        assertEq(limit, 100_000e6, "the risk budget is measured on the vault's own assets");

        // 200,000 of cover needs 190,000 of the vault's USDG: only 100,000 is its own
        usdg.mint(bob, 200_000e6);
        vm.startPrank(bob);
        usdg.approve(address(desk), 200_000e6);
        vm.expectRevert(IDeskQueue.ReservedForClaims.selector);
        desk.buyCover(address(s), 200_000e6, 200_000e6, 0, integrator, bob);
        vm.stopPrank();

        // a hedger's NOTE sold to the Desk is paid from the vault's USDG, not from the claims
        _mintPairs(s, alice, 110_000e6);
        address noteToken = s.note();
        vm.startPrank(alice);
        IERC20(noteToken).approve(address(desk), 110_000e6);
        vm.expectRevert(IDeskQueue.ReservedForClaims.selector);
        desk.sell(address(s), 110_000e6, 0, 0, integrator, alice); // 104,500 > 100,000
        vm.stopPrank();

        assertEq(_claim(lp), 900_000e6);
    }

    // --- fuzz ---------------------------------------------------------------------

    /// Filling the queue never lowers the share price of the LPs who stay, and
    /// pays a leaver no more than their shares were worth.
    function testFuzz_fill_is_fair_to_both_sides(uint256 cover, uint256 req1, uint256 req2, uint16 price) public {
        cover = bound(cover, 0, 900_000e6);
        req1 = bound(req1, 10e6 * SHARE, 600_000e6 * SHARE);
        req2 = bound(req2, 10e6 * SHARE, 100_000e6 * SHARE);
        _deposit(lp2, 100_000e6);
        if (cover != 0) _buyCover(bob, cover, 0);
        pricer.setPrice(uint16(bound(price, 3_000, 10_675))); // the NOTE inventory gains or loses

        _request(lp, req1);
        _request(lp2, req2);
        uint256 priceBefore = _sharePrice();
        uint256 worth = desk.convertToAssets(req1 + req2);
        (uint256 filled, uint256 setAside) = desk.processQueue(10);
        assertLe(setAside, worth, "never more than the shares were worth");
        assertLe(setAside, usdg.balanceOf(address(desk)), "paid from USDG the Desk has");
        assertGe(_sharePrice() + 1, priceBefore, "the stayers' share price doesn't fall");
        assertApproxEqAbs(_sharePrice(), priceBefore, 1, "nor does it jump");
        assertEq(desk.queuedShares(), req1 + req2 - filled);
        assertEq(desk.reservedAssets(), setAside);
        assertEq(desk.claimableAssets(lp) + desk.claimableAssets(lp2), setAside);
    }
}
