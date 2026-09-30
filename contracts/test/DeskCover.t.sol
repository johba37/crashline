// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {DeskFixture} from "./DeskFixture.t.sol";
import {MockPricer} from "./mocks/MockPricer.sol";
import {MockChainlinkFeed} from "../src/MockChainlinkFeed.sol";
import {FixingsRecorder} from "../src/FixingsRecorder.sol";
import {NoteSeries} from "../src/NoteSeries.sol";
import {IDesk} from "../src/interfaces/IDesk.sol";
import {IDeskCover} from "../src/interfaces/IDeskCover.sol";
import {INoteSeries, SeriesTerms} from "../src/interfaces/INoteSeries.sol";
import {ISurrogatePricer} from "../src/interfaces/ISurrogatePricer.sol";

/// The Desk's second leg (IDeskCover): cover trades, the two prices, the
/// WRITER cap and the risk budget. Fixture numbers: maxPayout 10675 bps, the
/// model's NOTE quote 9500 bps, so cover is worth 1175 bps at the mid.
contract DeskCoverTest is DeskFixture {
    uint256 constant MAX = 1_067_500; // maxPayoutPerNote
    uint256 constant COUPONS = 67_500; // the least an unsettled NOTE pays

    function _approxNav(uint256 expected) internal view {
        assertApproxEqAbs(desk.totalAssets(), expected, 2, "NAV (mint rounding and mark floors)");
    }

    /// A second struck series on the same feed, listed with its own mock model.
    function _secondSeries() internal returns (NoteSeries s2, MockPricer pricer2) {
        uint40 strike2 = uint40(block.timestamp) + 10;
        s2 = _create(_terms(strike2));
        _fixAt(s2, strike2, int256(uint256(INITIAL)));
        _spot(9000);
        pricer2 = new MockPricer();
        desk.listSeries(address(s2), pricer2, VOL, CAP);
    }

    function _buyCoverOn(NoteSeries x, address who, uint256 n) internal {
        usdg.mint(who, n);
        vm.startPrank(who);
        usdg.approve(address(desk), n);
        desk.buyCover(address(x), n, n, 0, address(0), who);
        vm.stopPrank();
    }

    // --- buyCover -----------------------------------------------------------------

    function test_buyCover_pays_the_premium_and_desk_keeps_note() public {
        uint256 n = 1_000e6 + 3; // odd amount to exercise rounding
        uint16 feeBps = 500; // 5% of the premium
        uint256 navBefore = desk.totalAssets();
        (uint256 quoted, uint16 priceBps) = desk.quoteBuyCover(address(s), n, feeBps);
        assertEq(priceBps, 10_675 - 9500);
        uint256 premium = (n * 1175 + 9999) / 10_000;
        uint256 fee = (premium * feeBps + 9999) / 10_000;
        assertEq(quoted, premium + fee);

        usdg.mint(bob, quoted);
        vm.startPrank(bob);
        usdg.approve(address(desk), quoted);
        vm.expectEmit(address(desk));
        emit IDeskCover.CoverBought(address(s), bob, alice, n, 1175, quoted, feeBps, integrator, pricer.weightsHash());
        uint256 cost = desk.buyCover(address(s), n, quoted, feeBps, integrator, alice);
        vm.stopPrank();

        assertEq(cost, quoted);
        assertEq(usdg.balanceOf(bob), 0, "the hedger paid the premium, not the pair's collateral");
        assertEq(_writer(s).balanceOf(alice), n);
        assertEq(_note(s).balanceOf(address(desk)), n, "the Desk keeps the NOTE");
        assertEq(_writer(s).balanceOf(address(desk)), 0);
        assertEq(desk.listing(address(s)).soldNotional, 0, "no WRITER held");
        assertEq(desk.heldSeries().length, 1);
        uint256 slice = (fee * 5000 + 9999) / 10_000;
        assertEq(usdg.balanceOf(integrator), fee - slice);
        assertEq(usdg.balanceOf(address(desk)), LP_CAPITAL + cost - s.previewMint(n) - (fee - slice));
        _approxNav(navBefore + slice); // sold at the mark: only the fee slice moves the NAV
    }

    function test_buyCover_errors() public {
        vm.expectRevert(INoteSeries.ZeroAmount.selector);
        desk.quoteBuyCover(address(s), 0, 0);
        assertEq(desk.MAX_COVER_FEE_BPS(), 1_000);
        vm.expectRevert(abi.encodeWithSelector(IDesk.FeeTooHigh.selector, uint16(1_001)));
        desk.quoteBuyCover(address(s), 1e6, 1_001);
        vm.expectRevert(abi.encodeWithSelector(IDesk.FeeTooHigh.selector, uint16(1_001)));
        desk.quoteSellCover(address(s), 1e6, 1_001);
        desk.quoteBuyCover(address(s), 1e6, 1_000); // the cover cap, 5x the NOTE cap
        vm.expectRevert(abi.encodeWithSelector(IDesk.FeeTooHigh.selector, uint16(201)));
        desk.quoteBuy(address(s), 1e6, 201);
        vm.expectRevert(abi.encodeWithSelector(IDesk.NotListed.selector, alice));
        desk.quoteBuyCover(alice, 1e6, 0);

        (uint256 quoted,) = desk.quoteBuyCover(address(s), 1e6, 0);
        usdg.mint(bob, quoted);
        vm.startPrank(bob);
        usdg.approve(address(desk), quoted);
        vm.expectRevert(abi.encodeWithSelector(IDesk.Slippage.selector, quoted, quoted - 1));
        desk.buyCover(address(s), 1e6, quoted - 1, 0, integrator, bob);
        vm.stopPrank();

        uint40 obs1 = _obsTime(s, 1);
        vm.warp(uint256(obs1) - 1 hours + 1);
        _spot(9000);
        vm.expectRevert(abi.encodeWithSelector(IDesk.TooCloseToObservation.selector, obs1));
        desk.quoteBuyCover(address(s), 1e6, 0);
        vm.expectRevert(abi.encodeWithSelector(IDesk.TooCloseToObservation.selector, obs1));
        desk.quoteSellCover(address(s), 1e6, 0);
    }

    /// The integrator fee on cover is a share of the premium; half of it stays with the LPs.
    function test_cover_fee_is_taken_on_the_premium() public {
        (uint256 cost,) = desk.quoteBuyCover(address(s), 1_000e6, 1_000);
        assertEq(cost, 117.5e6 + 11.75e6, "10% of the 117.50 premium, not of the 1,000 notional");
        _buyCover(bob, 1_000e6, 1_000);
        assertEq(usdg.balanceOf(integrator), 5.875e6);
        assertEq(desk.totalAssets(), LP_CAPITAL + 5.875e6);

        (uint256 proceeds,) = desk.quoteSellCover(address(s), 1_000e6, 1_000);
        assertEq(proceeds, 117.5e6 - 11.75e6);
        // the same 1,000 of NOTE at the NOTE cap: 2% of notional
        (uint256 noteCost,) = desk.quoteBuy(address(s), 1_000e6, 200);
        assertEq(noteCost, 950e6 + 20e6);
    }

    function test_delist_stops_cover_buys_not_sells() public {
        _buyCover(bob, 1_000e6, 0);
        desk.delistSeries(address(s));
        vm.expectRevert(abi.encodeWithSelector(IDesk.NotListed.selector, address(s)));
        desk.quoteBuyCover(address(s), 1e6, 0);
        _sellCover(bob, 1_000e6, 0);
        assertEq(_note(s).balanceOf(address(desk)), 0);
    }

    // --- sellCover ----------------------------------------------------------------

    function test_sellCover_unwinds_pairs() public {
        _buyCover(bob, 1_000e6, 0);
        _later(3 days, 8500);
        pricer.setPrice(9000);
        uint16 mid = uint16(9000 + 25 * 3 days / WEEK); // the quoter adds the accrued coupon
        uint256 idleBefore = usdg.balanceOf(address(desk));

        (uint256 quoted, uint16 priceBps) = desk.quoteSellCover(address(s), 400e6, 100);
        assertEq(priceBps, 10_675 - mid);
        address writerToken = s.writer();
        vm.startPrank(bob);
        IERC20(writerToken).approve(address(desk), 400e6);
        vm.expectRevert(abi.encodeWithSelector(IDesk.Slippage.selector, quoted, quoted + 1));
        desk.sellCover(address(s), 400e6, quoted + 1, 100, integrator, bob);
        vm.expectEmit(address(desk));
        emit IDeskCover.CoverSold(address(s), bob, bob, 400e6, priceBps, quoted, 100, integrator, pricer.weightsHash());
        uint256 proceeds = desk.sellCover(address(s), 400e6, quoted, 100, integrator, bob);
        vm.stopPrank();

        uint256 gross = 400e6 * uint256(priceBps) / 10_000;
        uint256 fee = (gross + 99) / 100; // 1% of what the cover fetches, not of its notional
        assertEq(proceeds, gross - fee);
        assertEq(_note(s).balanceOf(address(desk)), 600e6);
        assertEq(_writer(s).balanceOf(address(desk)), 0);
        // the Desk got 400 pairs' collateral back and paid the proceeds + the integrator's half of the fee
        assertEq(usdg.balanceOf(address(desk)), idleBefore + 400 * MAX - proceeds - (fee - (fee + 1) / 2));
    }

    function test_sellCover_unpaired_counts_against_the_cap() public {
        _mintPairs(s, bob, 50e6); // a hedger who minted directly and now exits the WRITER
        desk.listSeries(address(s), pricer, VOL, 30e6);
        vm.expectRevert(abi.encodeWithSelector(IDesk.CapExceeded.selector, uint256(50e6), uint256(30e6)));
        desk.quoteSellCover(address(s), 50e6, 0);

        desk.listSeries(address(s), pricer, VOL, 50e6);
        uint256 proceeds = _sellCover(bob, 50e6, 0);
        assertEq(proceeds, 50e6 * 1175 / 10_000);
        assertEq(_writer(s).balanceOf(address(desk)), 50e6);
        assertEq(desk.listing(address(s)).soldNotional, 50e6, "soldNotional reports the WRITER held");
        assertEq(desk.heldSeries().length, 1);
        // the cap is full: no more WRITER, neither bought back nor from a NOTE buyer
        vm.expectRevert(abi.encodeWithSelector(IDesk.CapExceeded.selector, uint256(1), uint256(0)));
        desk.quoteSellCover(address(s), 1, 0);
        vm.expectRevert(abi.encodeWithSelector(IDesk.CapExceeded.selector, uint256(1), uint256(0)));
        desk.quoteBuy(address(s), 1, 0);

        // his NOTE pairs with the Desk's WRITER and both are redeemed
        _sell(bob, 50e6, 0);
        assertEq(_writer(s).balanceOf(address(desk)), 0);
        assertEq(_note(s).balanceOf(address(desk)), 0);
        _approxNav(LP_CAPITAL);
    }

    /// Part of a sale pairs with the Desk's NOTE, the rest becomes WRITER inventory.
    function test_sellCover_partly_paired() public {
        _buyCover(bob, 30e6, 0); // Desk: 30 NOTE
        _mintPairs(s, bob, 20e6); // bob: 50 WRITER, 20 NOTE
        _sellCover(bob, 50e6, 0);
        assertEq(_note(s).balanceOf(address(desk)), 0);
        assertEq(_writer(s).balanceOf(address(desk)), 20e6);
        vm.expectRevert(abi.encodeWithSelector(IDesk.CapExceeded.selector, uint256(CAP), uint256(CAP) - 20e6));
        desk.quoteBuy(address(s), CAP, 0);
    }

    // --- both legs through the Desk ---------------------------------------------------

    /// A NOTE buyer and a cover buyer of the same size leave the Desk with no
    /// position and both spreads.
    function test_matched_legs_leave_the_desk_flat_with_both_spreads() public {
        desk.setSpread(address(s), 20, 30, 0);
        uint256 noteCost = _buy(alice, 1_000e6, 0); // mints 1,000 pairs, keeps the WRITER
        assertEq(noteCost, 1_000e6 * 9530 / 10_000);
        assertEq(_writer(s).balanceOf(address(desk)), 1_000e6);
        uint256 coverCost = _buyCover(bob, 1_000e6, 0); // served from that WRITER: no mint
        assertEq(coverCost, 1_000e6 * (10_675 - 9480) / 10_000);

        assertEq(_note(s).balanceOf(address(desk)), 0);
        assertEq(_writer(s).balanceOf(address(desk)), 0);
        assertEq(usdg.balanceOf(address(s)), 1_000 * MAX, "the pair is funded by its two buyers");
        assertEq(desk.totalAssets(), LP_CAPITAL + 1_000e6 * (20 + 30) / 10_000);
        (uint256 atRisk,) = desk.risk(address(feed));
        assertEq(atRisk, 0);
    }

    /// The other order: cover first leaves NOTE inventory, which a NOTE buyer takes over.
    function test_note_buyer_takes_the_risk_off_the_desk() public {
        desk.listSeries(address(s), pricer, VOL, 0); // no WRITER budget: NOTE from inventory only
        vm.expectRevert(abi.encodeWithSelector(IDesk.CapExceeded.selector, uint256(1e6), uint256(0)));
        desk.quoteBuy(address(s), 1e6, 0);

        _buyCover(bob, 1_000e6, 0);
        (uint256 atRisk,) = desk.risk(address(feed));
        assertEq(atRisk, 1_000 * (950_000 - COUPONS));
        vm.expectRevert(abi.encodeWithSelector(IDesk.CapExceeded.selector, uint256(1_000e6) + 1, uint256(1_000e6)));
        desk.quoteBuy(address(s), 1_000e6 + 1, 0);
        _buy(alice, 1_000e6, 0);
        assertEq(_note(s).balanceOf(alice), 1_000e6);
        assertEq(_note(s).balanceOf(address(desk)), 0);
        (atRisk,) = desk.risk(address(feed));
        assertEq(atRisk, 0);
    }

    // --- two prices ---------------------------------------------------------------

    function test_two_prices_per_leg() public {
        vm.expectEmit(address(desk));
        emit IDeskCover.SpreadSet(address(s), 20, 30, 0);
        desk.setSpread(address(s), 20, 30, 0);
        IDeskCover.Spread memory sp = desk.spread(address(s));
        assertEq(sp.bidBps, 20);
        assertEq(sp.askBps, 30);

        (, uint16 noteAsk) = desk.quoteBuy(address(s), 1e6, 0);
        (, uint16 noteBid) = desk.quoteSell(address(s), 1e6, 0);
        (, uint16 coverAsk) = desk.quoteBuyCover(address(s), 1e6, 0);
        (, uint16 coverBid) = desk.quoteSellCover(address(s), 1e6, 0);
        assertEq(noteAsk, 9530);
        assertEq(noteBid, 9480);
        assertEq(coverAsk, 10_675 - 9480);
        assertEq(coverBid, 10_675 - 9530);
    }

    function test_prices_stay_inside_zero_and_maxPayout() public {
        desk.setSpread(address(s), 1_000, 1_000, 0);
        pricer.setPrice(10_670); // mid + ask would pass maxPayout
        (, uint16 noteAsk) = desk.quoteBuy(address(s), 1e6, 0);
        (uint256 coverProceeds, uint16 coverBid) = desk.quoteSellCover(address(s), 1e6, 0);
        assertEq(noteAsk, 10_675);
        assertEq(coverBid, 0);
        assertEq(coverProceeds, 0);

        pricer.setPrice(400); // mid - bid would pass zero
        (uint256 noteProceeds, uint16 noteBid) = desk.quoteSell(address(s), 1e6, 0);
        (, uint16 coverAsk) = desk.quoteBuyCover(address(s), 1e6, 0);
        assertEq(noteBid, 0);
        assertEq(noteProceeds, 0);
        assertEq(coverAsk, 10_675);

        pricer.setPrice(11_000); // a model quote above maxPayout is capped
        desk.setSpread(address(s), 0, 0, 0);
        (, noteAsk) = desk.quoteBuy(address(s), 1e6, 0);
        (, coverAsk) = desk.quoteBuyCover(address(s), 1e6, 0);
        assertEq(noteAsk, 10_675);
        assertEq(coverAsk, 0);
    }

    function test_round_trip_costs_the_trader_both_spreads() public {
        desk.setSpread(address(s), 20, 30, 0);
        uint256 cost = _buy(alice, 1_000e6, 0);
        uint256 proceeds = _sell(alice, 1_000e6, 0);
        assertEq(cost - proceeds, 1_000e6 * 50 / 10_000);
        assertEq(desk.totalAssets(), LP_CAPITAL + 5e6, "the spread stays in the vault");

        cost = _buyCover(bob, 1_000e6, 0);
        proceeds = _sellCover(bob, 1_000e6, 0);
        assertEq(cost - proceeds, 1_000e6 * 50 / 10_000);
        assertEq(desk.totalAssets(), LP_CAPITAL + 10e6);
    }

    // --- vol band -----------------------------------------------------------------

    /// The mock becomes a vol-input model certified for 40-70%: `slope` bps of
    /// price per vol point, so 9500 at the listing's 55%.
    function _volModel(int16 slope) internal {
        pricer.setRange(2, 4000, 7000);
        pricer.setVolSlope(slope);
    }

    function test_vol_band_prices_each_side_at_its_end() public {
        _volModel(-3); // 9509 at 52%, 9500 at 55%, 9491 at 58%
        vm.expectEmit(address(desk));
        emit IDeskCover.SpreadSet(address(s), 20, 30, 300);
        desk.setSpread(address(s), 20, 30, 300);
        assertEq(desk.spread(address(s)).volBandBps, 300);

        (, uint16 noteAsk) = desk.quoteBuy(address(s), 1e6, 0);
        (, uint16 noteBid) = desk.quoteSell(address(s), 1e6, 0);
        (, uint16 coverAsk) = desk.quoteBuyCover(address(s), 1e6, 0);
        (, uint16 coverBid) = desk.quoteSellCover(address(s), 1e6, 0);
        assertEq(noteAsk, 9509 + 30, "the Desk sells NOTE at the low vol");
        assertEq(noteBid, 9491 - 20, "and buys it at the high vol");
        assertEq(coverAsk, 10_675 - 9471);
        assertEq(coverBid, 10_675 - 9539);

        // positions are marked at the listing's vol itself
        uint256 cost = _buyCover(bob, 1_000e6, 0);
        assertEq(cost, 1_000e6 * (10_675 - 9471) / 10_000);
        assertEq(desk.totalAssets(), LP_CAPITAL + 1_000e6 * (9500 - 9471) / 10_000);
        (uint256 atRisk,) = desk.risk(address(feed));
        assertEq(atRisk, 1_000 * (950_000 - COUPONS));
    }

    /// Where the note gains with vol, the ends of the band swap roles: the
    /// Desk still buys at the lower quote and sells at the higher one.
    function test_vol_band_takes_the_lower_and_higher_quote() public {
        _volModel(3); // 9491 at 52%, 9509 at 58%
        desk.setSpread(address(s), 0, 0, 300);
        (, uint16 noteAsk) = desk.quoteBuy(address(s), 1e6, 0);
        (, uint16 noteBid) = desk.quoteSell(address(s), 1e6, 0);
        assertEq(noteAsk, 9509);
        assertEq(noteBid, 9491);
    }

    function test_vol_band_widens_with_the_notes_vol_sensitivity() public {
        _volModel(-10);
        desk.setSpread(address(s), 0, 0, 300);
        (, uint16 noteAsk) = desk.quoteBuy(address(s), 1e6, 0);
        (, uint16 noteBid) = desk.quoteSell(address(s), 1e6, 0);
        assertEq(noteAsk - noteBid, 60);
        pricer.setVolSlope(-1); // e.g. close to a certain payout
        (, noteAsk) = desk.quoteBuy(address(s), 1e6, 0);
        (, noteBid) = desk.quoteSell(address(s), 1e6, 0);
        assertEq(noteAsk - noteBid, 6);
    }

    function test_vol_band_needs_a_model_certified_for_both_ends() public {
        // the fixture's model pins vol at 55%, like k2
        vm.expectRevert(abi.encodeWithSelector(IDesk.ModelMismatch.selector, uint8(2)));
        desk.setSpread(address(s), 0, 0, 1);

        pricer.setRange(2, 5000, 5800);
        vm.expectRevert(abi.encodeWithSelector(IDesk.ModelMismatch.selector, uint8(2)));
        desk.setSpread(address(s), 0, 0, 301); // 58.01% is outside
        desk.setSpread(address(s), 0, 0, 300);
        // moving the listing's vol takes the band along
        vm.expectRevert(abi.encodeWithSelector(IDesk.ModelMismatch.selector, uint8(2)));
        desk.listSeries(address(s), pricer, 5600, CAP);
        desk.listSeries(address(s), pricer, 5400, CAP);

        pricer.setRange(2, 0, 70_000);
        vm.expectRevert(abi.encodeWithSelector(IDesk.ModelMismatch.selector, uint8(2)));
        desk.setSpread(address(s), 0, 0, 5401); // wider than the vol itself
        desk.listSeries(address(s), pricer, 60_000, CAP);
        vm.expectRevert(abi.encodeWithSelector(IDesk.ModelMismatch.selector, uint8(2)));
        desk.setSpread(address(s), 0, 0, 6000); // 660% doesn't fit the input
    }

    function test_no_band_asks_the_model_once() public {
        vm.expectCall(address(pricer), abi.encodeWithSelector(ISurrogatePricer.priceBps.selector), 1);
        desk.quoteBuyCover(address(s), 1e6, 0);
    }

    function test_band_asks_the_model_twice() public {
        _volModel(-3);
        desk.setSpread(address(s), 0, 0, 300);
        vm.expectCall(address(pricer), abi.encodeWithSelector(ISurrogatePricer.priceBps.selector), 2);
        desk.quoteBuyCover(address(s), 1e6, 0);
    }

    function test_band_refusal_at_one_end_bubbles() public {
        _volModel(-3);
        desk.setSpread(address(s), 0, 0, 300);
        pricer.setRange(2, 5500, 7000); // the mock now refuses 52%
        vm.expectRevert(abi.encodeWithSelector(ISurrogatePricer.OutOfRange.selector, uint8(2), int64(5200)));
        desk.quoteSell(address(s), 1e6, 0);
        desk.totalAssets(); // marks only need the listing's vol
    }

    function test_curator_setters() public {
        vm.startPrank(alice);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, alice));
        desk.setSpread(address(s), 1, 1, 0);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, alice));
        desk.setRiskBudget(address(feed), 1);
        vm.stopPrank();

        assertEq(desk.MAX_SPREAD_BPS(), 1_000);
        vm.expectRevert(abi.encodeWithSelector(IDeskCover.SpreadTooWide.selector, uint16(1_001)));
        desk.setSpread(address(s), 1_001, 0, 0);
        vm.expectRevert(abi.encodeWithSelector(IDeskCover.SpreadTooWide.selector, uint16(1_001)));
        desk.setSpread(address(s), 0, 1_001, 0);
        vm.expectRevert(abi.encodeWithSelector(IDesk.NotListed.selector, alice));
        desk.setSpread(alice, 1, 1, 0);

        vm.expectRevert(abi.encodeWithSelector(IDeskCover.BudgetTooHigh.selector, uint16(10_001)));
        desk.setRiskBudget(address(feed), 10_001);
        vm.expectEmit(address(desk));
        emit IDeskCover.RiskBudgetSet(address(feed), 2_500);
        desk.setRiskBudget(address(feed), 2_500);
        assertEq(desk.riskBudgetBps(address(feed)), 2_500);

        IDesk.Listing memory unlisted = desk.listing(alice);
        assertEq(address(unlisted.pricer), address(0));
        assertEq(unlisted.soldNotional, 0);
    }

    // --- risk budget ----------------------------------------------------------------

    function test_risk_budget_limits_new_positions() public {
        desk.setRiskBudget(address(feed), 1_000); // 10% of the vault
        _buyCover(bob, 100_000e6, 0);
        // 100,000 NOTE at 0.95, of which the coupons (0.0675) can't be lost
        (uint256 atRisk, uint256 limit) = desk.risk(address(feed));
        assertEq(atRisk, 88_250e6);
        assertEq(limit, 100_000e6, "sold at the mark: vault assets unchanged");

        // 20,000 more would put 105,900 at risk
        usdg.mint(bob, 20_000e6);
        vm.startPrank(bob);
        usdg.approve(address(desk), 20_000e6);
        vm.expectRevert(
            abi.encodeWithSelector(IDeskCover.RiskBudgetExceeded.selector, uint256(105_900e6), uint256(100_000e6))
        );
        desk.buyCover(address(s), 20_000e6, 20_000e6, 0, integrator, bob);
        vm.stopPrank();
        // a hedger's NOTE sold to the Desk is the same new position
        _mintPairs(s, alice, 20_000e6);
        address noteToken = s.note();
        vm.startPrank(alice);
        IERC20(noteToken).approve(address(desk), 20_000e6);
        vm.expectRevert(
            abi.encodeWithSelector(IDeskCover.RiskBudgetExceeded.selector, uint256(105_900e6), uint256(100_000e6))
        );
        desk.sell(address(s), 20_000e6, 0, 0, integrator, alice);
        vm.stopPrank();

        // trades that shrink the position pass even with the budget closed
        desk.setRiskBudget(address(feed), 0);
        _buy(alice, 10_000e6, 0); // NOTE out of inventory
        _sellCover(bob, 10_000e6, 0); // cover back: pairs redeemed
        assertEq(_note(s).balanceOf(address(desk)), 80_000e6);
        (atRisk, limit) = desk.risk(address(feed));
        assertEq(atRisk, 80_000 * (950_000 - COUPONS));
        assertEq(limit, 0);
    }

    function test_risk_budget_is_closed_until_set() public {
        desk.setRiskBudget(address(feed), 0);
        usdg.mint(bob, 2e6);
        vm.startPrank(bob);
        usdg.approve(address(desk), 2e6);
        vm.expectRevert(abi.encodeWithSelector(IDeskCover.RiskBudgetExceeded.selector, uint256(882_500), uint256(0)));
        desk.buyCover(address(s), 1e6, 1e6, 0, integrator, bob);
        // WRITER from a NOTE buy is a position too: 1 WRITER at 0.1175
        vm.expectRevert(abi.encodeWithSelector(IDeskCover.RiskBudgetExceeded.selector, uint256(117_500), uint256(0)));
        desk.buy(address(s), 1e6, 1e6, 0, integrator, bob);
        vm.stopPrank();
    }

    function test_risk_counts_writer_at_its_mark() public {
        _buy(alice, 1_000e6, 0);
        (uint256 atRisk, uint256 limit) = desk.risk(address(feed));
        assertEq(atRisk, 1_000 * (MAX - 950_000));
        assertEq(limit, LP_CAPITAL);
    }

    /// A budget is per feed; vault assets are shared.
    function test_risk_budget_is_per_feed() public {
        MockChainlinkFeed feed2 = new MockChainlinkFeed("RHNVDA / USD");
        uint40 strike2 = uint40(block.timestamp) + 10;
        SeriesTerms memory t = _terms(strike2);
        t.feed = address(feed2);
        NoteSeries s2 = _create(t);
        vm.warp(uint256(strike2) + 1);
        feed2.pushRoundAt(int256(uint256(INITIAL)), strike2);
        FixingsRecorder(address(s2.recorder())).recordFixing(strike2, uint80(feed2.latestRound()));
        feed2.pushRoundAt(_price(9000), uint40(block.timestamp));
        desk.listSeries(address(s2), pricer, VOL, CAP);

        usdg.mint(bob, 1e6);
        vm.startPrank(bob);
        usdg.approve(address(desk), 1e6);
        vm.expectRevert(abi.encodeWithSelector(IDeskCover.RiskBudgetExceeded.selector, uint256(882_500), uint256(0)));
        desk.buyCover(address(s2), 1e6, 1e6, 0, integrator, bob);
        vm.stopPrank();

        desk.setRiskBudget(address(feed2), 500);
        _buyCoverOn(s2, bob, 10_000e6);
        _buyCover(bob, 1_000e6, 0);
        (uint256 risk1, uint256 limit1) = desk.risk(address(feed));
        (uint256 risk2, uint256 limit2) = desk.risk(address(feed2));
        assertEq(risk1, 1_000 * (950_000 - COUPONS));
        assertEq(risk2, 10_000 * (950_000 - COUPONS));
        assertEq(limit1, LP_CAPITAL);
        assertEq(limit2, LP_CAPITAL * 500 / 10_000);
        assertEq(desk.heldSeries().length, 2);
    }

    /// While a held series can't be quoted, the budget counts it at its worst
    /// instead of reverting, and other series keep trading if that still fits.
    function test_risk_budget_with_an_unquotable_series() public {
        (NoteSeries s2,) = _secondSeries();
        _buyCover(bob, 100_000e6, 0);
        uint256 idle = usdg.balanceOf(address(desk));
        assertEq(idle, LP_CAPITAL + 11_750e6 - 106_750e6);

        pricer.setRefusal(MockPricer.Mode.UncertifiedErr, 0, 0); // s is in an observation-day band
        vm.expectRevert();
        desk.totalAssets();
        (uint256 atRisk, uint256 limit) = desk.risk(address(feed));
        assertEq(atRisk, 100_000e6, "1 USDG per NOTE");
        assertEq(limit, idle + 100_000 * COUPONS, "the NOTE counts as its coupons only");

        _buyCoverOn(s2, bob, 1_000e6); // fits: 100,882.5 at risk of 905,000 + 950 + ...
        desk.setRiskBudget(address(feed), 1_000);
        usdg.mint(bob, 1e6);
        vm.startPrank(bob);
        usdg.approve(address(desk), 1e6);
        vm.expectPartialRevert(IDeskCover.RiskBudgetExceeded.selector);
        desk.buyCover(address(s2), 1e6, 1e6, 0, integrator, bob);
        vm.stopPrank();

        pricer.setPrice(9500); // quotable again: 101,000 NOTE at risk 0.8825 each fits 10%
        _buyCoverOn(s2, bob, 1e6);
    }

    function test_certain_payout_is_not_at_risk() public {
        _buyCover(bob, 1_000e6, 0);
        for (uint256 i = 1; i <= 26; i++) {
            _fix(s, i, 9000);
        }
        _spot(9000);
        (uint256 atRisk,) = desk.risk(address(feed));
        assertEq(atRisk, 0, "final period, not knocked in");
        assertEq(desk.totalAssets(), usdg.balanceOf(address(desk)) + 1_000 * MAX);
        _fix(s, 27, 8000);
        (atRisk,) = desk.risk(address(feed));
        assertEq(atRisk, 0, "settled");
    }

    // --- settlement with NOTE inventory ----------------------------------------------

    function test_autocall_pays_the_desk_its_note() public {
        uint256 premium = _buyCover(bob, 1_000e6, 0);
        assertEq(premium, 117.5e6);
        _fix(s, 1, 10_200); // autocall: NOTE pays 1.0025, WRITER 0.065
        assertEq(desk.totalAssets(), usdg.balanceOf(address(desk)) + 1_000 * 1_002_500);
        vm.expectEmit(address(desk));
        emit IDesk.Collected(address(s), 1_000 * 1_002_500);
        desk.collect(address(s));
        assertEq(desk.heldSeries().length, 0);
        // the LPs put up 950 and got 1,002.50
        assertEq(desk.totalAssets(), LP_CAPITAL + 52.5e6);
        vm.prank(bob);
        assertEq(s.redeem(0, 1_000e6, bob), 65e6);
    }

    function test_knock_in_costs_the_desk_and_pays_the_hedger() public {
        _buyCover(bob, 1_000e6, 0);
        for (uint256 i = 1; i <= 26; i++) {
            _fix(s, i, i == 2 ? 5000 : 9000);
        }
        _fix(s, 27, 7000); // knocked in, final 70%: NOTE pays 0.70 + coupons
        desk.collect(address(s));
        assertEq(desk.totalAssets(), LP_CAPITAL - 950e6 + 767.5e6);
        vm.prank(bob);
        assertEq(s.redeem(0, 1_000e6, bob), 300e6, "the hedger's cover pays the 30% fall");
    }

    function test_maxWithdraw_capped_by_idle_with_note_inventory() public {
        _buyCover(bob, 900_000e6, 0); // locks most capital as the NOTE's share of the collateral
        uint256 idle = usdg.balanceOf(address(desk));
        assertEq(idle, LP_CAPITAL - 900_000 * 950_000);
        assertEq(desk.maxWithdraw(lp), idle);
    }

    // --- fuzz ---------------------------------------------------------------------

    /// Cover bought and sold back at the same quote never lowers the LPs'
    /// assets by more than rounding, and with a spread it raises them.
    function testFuzz_cover_round_trip_never_costs_lps(
        uint256 n,
        uint16 feeBps,
        uint16 price,
        uint16 bidBps,
        uint16 askBps,
        uint16 band,
        int16 slope
    ) public {
        n = bound(n, 1, 50_000e6);
        feeBps = uint16(bound(feeBps, 0, 1_000));
        price = uint16(bound(price, 1, 10_675));
        _volModel(int16(bound(slope, -30, 30)));
        desk.setSpread(
            address(s), uint16(bound(bidBps, 0, 1_000)), uint16(bound(askBps, 0, 1_000)), uint16(bound(band, 0, 1_500))
        );
        pricer.setPrice(price);
        uint256 before = desk.totalAssets();
        uint256 cost = _buyCover(bob, n, feeBps);
        assertGe(desk.totalAssets() + 2, before);
        uint256 proceeds = _sellCover(bob, n, feeBps);
        assertGe(desk.totalAssets() + 2, before);
        assertGe(cost, proceeds, "a round trip never pays the trader");
        assertEq(_note(s).balanceOf(address(desk)), 0);
    }

    /// Minting a pair to sell both legs to the Desk, or buying both legs from
    /// it to redeem the pair, never pays: the two prices of the legs straddle
    /// maxPayout.
    function testFuzz_no_pair_arbitrage(uint256 n, uint16 price, uint16 bidBps, uint16 askBps, uint16 band, int16 slope)
        public
    {
        n = bound(n, 1, 50_000e6);
        price = uint16(bound(price, 1, 11_500)); // also above maxPayout
        _volModel(int16(bound(slope, -30, 30)));
        desk.setSpread(
            address(s), uint16(bound(bidBps, 0, 1_000)), uint16(bound(askBps, 0, 1_000)), uint16(bound(band, 0, 1_500))
        );
        pricer.setPrice(price);

        uint256 collateral = _mintPairs(s, bob, n);
        uint256 got = _sell(bob, n, 0) + _sellCover(bob, n, 0);
        assertLe(got, collateral, "sell both legs");
        assertEq(_note(s).balanceOf(address(desk)) + _writer(s).balanceOf(address(desk)), 0);

        uint256 paid = _buy(alice, n, 0) + _buyCover(alice, n, 0);
        vm.prank(alice);
        uint256 back = s.redeemPair(n, alice);
        assertGe(paid, back, "buy both legs");
        assertEq(_note(s).balanceOf(address(desk)) + _writer(s).balanceOf(address(desk)), 0);
    }
}
