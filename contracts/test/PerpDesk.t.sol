// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Errors} from "@openzeppelin/contracts/interfaces/draft-IERC6093.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {PerpDeskFixture, MockWeekendSource} from "./PerpDeskFixture.t.sol";
import {MockPerpPricer} from "./mocks/MockPerpPricer.sol";
import {MockChainlinkFeed} from "../src/MockChainlinkFeed.sol";
import {MockUSDG} from "../src/MockUSDG.sol";
import {SeriesFactory} from "../src/SeriesFactory.sol";
import {PerpFactory} from "../src/PerpFactory.sol";
import {PerpDesk} from "../src/PerpDesk.sol";
import {PerpFormulaPricer} from "../src/PerpFormulaPricer.sol";
import {PerpSeries} from "../src/PerpSeries.sol";
import {IPerpDesk, IWeekendSource} from "../src/interfaces/IPerpDesk.sol";
import {IDeskQueue} from "../src/interfaces/IDeskQueue.sol";
import {IPerpQuoter} from "../src/interfaces/IPerpQuoter.sol";
import {IPerpPricer, PerpProduct} from "../src/interfaces/IPerpPricer.sol";
import {PerpState, PerpTerms, PerpPhase} from "../src/interfaces/IPerpSeries.sol";
import {SeriesTerms} from "../src/interfaces/INoteSeries.sol";

contract PerpDeskTest is PerpDeskFixture {
    address hedger = makeAddr("hedger");

    // --- configuration ---------------------------------------------------------------

    function test_constructor_wrong_asset() public {
        MockUSDG other = new MockUSDG();
        vm.expectRevert(PerpDesk.WrongAsset.selector);
        new PerpDesk(IERC20(address(other)), factory, quoter, address(this), 1 hours);
    }

    function test_config_and_the_fixtures_price() public view {
        assertEq(address(desk.factory()), address(factory));
        assertEq(address(desk.quoter()), address(quoter));
        assertEq(desk.asset(), address(usdg));
        assertEq(desk.decimals(), 12); // 6 + the inflation offset
        assertEq(desk.MAX_FEE_BPS(), 200);
        assertEq(desk.MAX_COVER_FEE_BPS(), 1000);
        assertEq(desk.BACKSTOP_SHARE_BPS(), 5000);
        assertEq(desk.MAX_SPREAD_BPS(), 1000);
        assertEq(desk.MAX_WEEKEND_CAP_BPS(), 1000);
        assertEq(desk.minSecsToFixing(), 1 hours);
        assertEq(desk.totalAssets(), LP_CAPITAL);
        // the closed form at spot 90%, vol 55%, plus the coupon reserve
        assertEq(_mid(), NOTE_BPS);
        assertEq(desk.midPriceBps(address(s)), NOTE_BPS);
        assertEq(desk.navPriceBps(address(s)), NOTE_BPS);
        (uint256 spot, bool weekendPrice) = desk.spotOf(address(s));
        assertEq(spot, uint256(_price(9000)));
        assertFalse(weekendPrice);
    }

    function test_listSeries_emits_and_updates_in_place() public {
        IPerpDesk.Listing memory l = desk.listing(address(s));
        assertTrue(l.active);
        assertEq(address(l.pricer), address(pricer));
        assertEq(l.volBpsAnnual, VOL);
        assertEq(l.nextEarnings, earnings);
        assertEq(l.capNotional, CAP);

        vm.expectEmit(address(desk));
        emit IPerpDesk.SeriesListed(address(s), address(pricer), pricer.weightsHash(), 6000, earnings + 1, 5e6);
        desk.listSeries(address(s), pricer, 6000, earnings + 1, 5e6);
        assertEq(desk.listedSeries().length, 1);
        assertEq(desk.listing(address(s)).volBpsAnnual, 6000);
    }

    function test_curator_functions_are_owner_only() public {
        bytes memory err = abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, alice);
        vm.startPrank(alice);
        vm.expectRevert(err);
        desk.listSeries(address(s), pricer, VOL, earnings, CAP);
        vm.expectRevert(err);
        desk.delistSeries(address(s));
        vm.expectRevert(err);
        desk.setEarnings(address(s), earnings);
        vm.expectRevert(err);
        desk.setSpread(address(s), 1, 1, 0);
        vm.expectRevert(err);
        desk.setRiskBudget(address(feed), 1);
        vm.expectRevert(err);
        desk.setWeekend(address(feed), IWeekendSource(address(0)), 0, 0);
        vm.expectRevert(err);
        desk.setMinSecsToFixing(0);
        vm.stopPrank();
    }

    function test_listSeries_refuses_foreign_series_and_other_products() public {
        // a v1 series of the same feed is not a perpetual series
        SeriesTerms memory v1 = SeriesTerms({
            feed: address(feed),
            strikeTime: first,
            observationInterval: WEEK,
            observationCount: 26,
            kiBarrierBps: 6000,
            acBarrierBps: 10000,
            couponBpsPerPeriod: 25
        });
        address v1Series = v1Factory.createSeries(v1);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.NotFactorySeries.selector, v1Series));
        desk.listSeries(v1Series, pricer, VOL, earnings, CAP);

        // a series the model is not pinned to: knock-in, melt share, interval
        PerpTerms memory t = _terms(first);
        t.kiBarrierBps = 7000;
        address other = factory.createSeries(t);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.ModelMismatch.selector, uint8(0)));
        desk.listSeries(other, pricer, VOL, earnings, CAP);
        t = _terms(first);
        t.meltShare = MELT + 1;
        other = factory.createSeries(t);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.ModelMismatch.selector, uint8(1)));
        desk.listSeries(other, pricer, VOL, earnings, CAP);
        t = _terms(first);
        t.fixingInterval = 1 days;
        other = factory.createSeries(t);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.ModelMismatch.selector, uint8(2)));
        desk.listSeries(other, pricer, VOL, earnings, CAP);
        // a vol the model isn't certified for
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.ModelMismatch.selector, uint8(3)));
        desk.listSeries(address(s), pricer, 1999, earnings, CAP);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.ModelMismatch.selector, uint8(3)));
        desk.listSeries(address(s), pricer, 9001, earnings, CAP);
        // any coupon reserve is the same product: the price is linear in it
        t = _terms(first);
        t.couponReserve = 95_000;
        desk.listSeries(factory.createSeries(t), pricer, VOL, earnings, CAP);
        assertEq(desk.listedSeries().length, 2);
    }

    function test_curator_setters_and_their_limits() public {
        vm.expectEmit(address(desk));
        emit IPerpDesk.SpreadSet(address(s), 30, 40, 200);
        desk.setSpread(address(s), 30, 40, 200);
        IPerpDesk.Spread memory sp = desk.spread(address(s));
        assertEq(sp.bidBps, 30);
        assertEq(sp.askBps, 40);
        assertEq(sp.volBandBps, 200);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.SpreadTooWide.selector, uint16(1001)));
        desk.setSpread(address(s), 1001, 0, 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.SpreadTooWide.selector, uint16(1001)));
        desk.setSpread(address(s), 0, 1001, 0);
        // the band must stay inside the model's certified vols (2000..9000), now and when the vol changes
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.ModelMismatch.selector, uint8(3)));
        desk.setSpread(address(s), 0, 0, 3501);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.ModelMismatch.selector, uint8(3)));
        desk.listSeries(address(s), pricer, 8900, earnings, CAP);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.NotListed.selector, address(1)));
        desk.setSpread(address(1), 0, 0, 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.NotListed.selector, address(1)));
        desk.setEarnings(address(1), earnings);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.NotListed.selector, address(1)));
        desk.delistSeries(address(1));

        vm.expectEmit(address(desk));
        emit IPerpDesk.RiskBudgetSet(address(feed), 2500);
        desk.setRiskBudget(address(feed), 2500);
        assertEq(desk.riskBudgetBps(address(feed)), 2500);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.BudgetTooHigh.selector, uint16(10_001)));
        desk.setRiskBudget(address(feed), 10_001);

        vm.expectEmit(address(desk));
        emit IPerpDesk.EarningsSet(address(s), earnings + 91 days);
        desk.setEarnings(address(s), earnings + 91 days);
        assertEq(desk.listing(address(s)).nextEarnings, earnings + 91 days);

        vm.expectEmit(address(desk));
        emit IPerpDesk.MinSecsToFixingSet(2 hours);
        desk.setMinSecsToFixing(2 hours);
        assertEq(desk.minSecsToFixing(), 2 hours);
    }

    // --- NOTE: buy and sell -------------------------------------------------------------

    function test_buy_pays_quote_plus_fee_and_keeps_writer() public {
        (uint256 quoted, uint16 priceBps) = desk.quote(address(s), IPerpDesk.Side.BuyNote, 100e6, 50);
        assertEq(priceBps, NOTE_BPS);
        assertEq(quoted, 101_960_000 + 500_000); // 100 * 1.0196 + 0.5% of the notional

        usdg.mint(alice, quoted);
        vm.startPrank(alice);
        usdg.approve(address(desk), quoted);
        vm.expectEmit(address(desk));
        emit IPerpDesk.Traded(
            address(s),
            alice,
            IPerpDesk.Side.BuyNote,
            alice,
            100e6,
            100e6,
            uint16(NOTE_BPS),
            quoted,
            50,
            integrator,
            pricer.weightsHash()
        );
        assertEq(desk.buy(address(s), 100e6, quoted, 50, integrator, alice), quoted);
        vm.stopPrank();

        assertEq(noteT.balanceOf(alice), 100e6);
        assertEq(writerT.balanceOf(address(desk)), 100e6); // the Desk minted pairs and kept the other leg
        assertEq(noteT.balanceOf(address(desk)), 0);
        assertEq(usdg.balanceOf(integrator), 250_000); // half the fee; the other half stays in the vault
        assertEq(usdg.balanceOf(address(s)), 123_500_000); // 100 pairs at 1.235
        assertEq(desk.heldSeries().length, 1);
        // the vault paid 123.5 for the pairs and holds WRITER worth 100 * (1.235 - 1.0196)
        assertEq(desk.totalAssets(), LP_CAPITAL + quoted - 250_000 - 123_500_000 + 21_540_000);
        assertEq(desk.totalAssets(), LP_CAPITAL + 250_000);
    }

    function test_buy_errors() public {
        vm.expectRevert(IPerpDesk.ZeroAmount.selector);
        desk.buy(address(s), 0, 0, 0, integrator, alice);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.FeeTooHigh.selector, uint16(201)));
        desk.buy(address(s), 1e6, type(uint256).max, 201, integrator, alice);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.NotListed.selector, address(1)));
        desk.buy(address(1), 1e6, type(uint256).max, 0, integrator, alice);
        usdg.mint(alice, 10e6);
        vm.startPrank(alice);
        usdg.approve(address(desk), 10e6);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.Slippage.selector, uint256(1_019_600), uint256(1_019_599)));
        desk.buy(address(s), 1e6, 1_019_599, 0, integrator, alice);
        vm.stopPrank();
    }

    function test_delist_stops_buys_not_sells() public {
        _buy(alice, 10e6, 0);
        vm.expectEmit(address(desk));
        emit IPerpDesk.SeriesDelisted(address(s));
        desk.delistSeries(address(s));
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.NotListed.selector, address(s)));
        desk.quote(address(s), IPerpDesk.Side.BuyNote, 1e6, 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.NotListed.selector, address(s)));
        desk.quote(address(s), IPerpDesk.Side.BuyCover, 1e6, 0);
        assertEq(_sell(alice, 10e6, 0), 10_196_000);
        assertEq(desk.totalAssets(), LP_CAPITAL);
    }

    function test_cap_is_in_notional_of_writer_held() public {
        desk.listSeries(address(s), pricer, VOL, earnings, 50e6);
        _buy(alice, 50e6, 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.CapExceeded.selector, uint256(1), uint256(0)));
        desk.quote(address(s), IPerpDesk.Side.BuyNote, 1, 0);
        // selling back frees the cap
        _sell(alice, 20e6, 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.CapExceeded.selector, uint256(21e6), uint256(20e6)));
        desk.quote(address(s), IPerpDesk.Side.BuyNote, 21e6, 0);
        _buy(alice, 20e6, 0);
        // after a fixing every token stands for less notional: the same cap is more tokens
        _fixing(1, 9000);
        uint256 perToken = s.state().notionalPerToken;
        uint256 capTokens = uint256(50e6) * RAY / perToken;
        assertGt(capTokens, 50e6);
        _buy(alice, capTokens - 50e6, 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.CapExceeded.selector, uint256(1), uint256(0)));
        desk.quote(address(s), IPerpDesk.Side.BuyNote, 1, 0);
    }

    function test_too_close_to_a_fixing() public {
        vm.warp(uint256(first) + WEEK - 1 hours + 1);
        _spot(9000);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.TooCloseToFixing.selector, first + WEEK));
        desk.quote(address(s), IPerpDesk.Side.BuyNote, 1e6, 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.TooCloseToFixing.selector, first + WEEK));
        desk.quote(address(s), IPerpDesk.Side.SellCover, 1e6, 0);
        vm.warp(uint256(first) + WEEK - 1 hours);
        desk.quote(address(s), IPerpDesk.Side.BuyNote, 1e6, 0);
    }

    function test_quote_refusals_bubble() public {
        // the model refuses
        pricer.setRefusal(MockPerpPricer.Mode.UncertifiedErr, 0, 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpPricer.Uncertified.selector, uint8(0)));
        desk.quote(address(s), IPerpDesk.Side.BuyNote, 1e6, 0);
        pricer.setCorrection(0);
        // the earnings date passes: no quote until the curator names the next one
        vm.warp(uint256(earnings) + 1);
        _fixAt(s, _fixingTime(s, 1), _price(9000));
        _fixAt(s, _fixingTime(s, 2), _price(9000));
        _fixAt(s, _fixingTime(s, 3), _price(9000));
        _fixAt(s, _fixingTime(s, 4), _price(9000));
        _spot(9000);
        vm.expectRevert(abi.encodeWithSelector(IPerpQuoter.EarningsDatePassed.selector, earnings));
        desk.quote(address(s), IPerpDesk.Side.BuyNote, 1e6, 0);
        desk.setEarnings(address(s), earnings + 91 days);
        desk.quote(address(s), IPerpDesk.Side.BuyNote, 1e6, 0);
        // a fixing is pending
        vm.warp(uint256(_fixingTime(s, 5)) + 1);
        _spot(9000);
        vm.expectRevert(abi.encodeWithSelector(IPerpQuoter.FixingPending.selector, _fixingTime(s, 5)));
        desk.quote(address(s), IPerpDesk.Side.SellNote, 1e6, 0);
    }

    function test_sell_unwinds_pairs_and_pays_the_bid() public {
        _buy(alice, 100e6, 0);
        (uint256 quoted, uint16 priceBps) = desk.quote(address(s), IPerpDesk.Side.SellNote, 40e6, 50);
        assertEq(priceBps, NOTE_BPS);
        assertEq(quoted, 40_784_000 - 200_000);
        assertEq(_sell(alice, 40e6, 50), quoted);
        assertEq(writerT.balanceOf(address(desk)), 60e6); // 40 pairs redeemed
        assertEq(noteT.balanceOf(address(desk)), 0);
        assertEq(usdg.balanceOf(address(s)), 74_100_000);
        assertEq(usdg.balanceOf(integrator), 100_000);
    }

    function test_sell_slippage_and_fee_capped_at_gross() public {
        _buy(alice, 10e6, 0);
        vm.startPrank(alice);
        noteT.approve(address(desk), 10e6);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.Slippage.selector, uint256(10_196_000), uint256(10_196_001)));
        desk.sell(address(s), 10e6, 10_196_001, 0, integrator, alice);
        // one base unit of NOTE: gross 1, fee ceil(1 * 2%) = 1: the fee takes it all and no more
        assertEq(desk.sell(address(s), 1, 0, 200, integrator, alice), 0);
        vm.stopPrank();
    }

    function test_selling_note_the_desk_cannot_pair_becomes_its_position() public {
        _mintPairs(s, alice, 30e6); // alice mints pairs herself and sells the NOTE
        uint256 proceeds = _sell(alice, 30e6, 0);
        assertEq(proceeds, 30_588_000);
        assertEq(noteT.balanceOf(address(desk)), 30e6);
        assertEq(desk.totalAssets(), LP_CAPITAL); // paid 30.588 for NOTE worth 30.588
        // the next NOTE buyer takes it out of the inventory: no mint
        uint256 escrow = usdg.balanceOf(address(s));
        _buy(bob, 30e6, 0);
        assertEq(usdg.balanceOf(address(s)), escrow);
        assertEq(noteT.balanceOf(address(desk)), 0);
    }

    // --- cover: buy and sell --------------------------------------------------------------

    function test_buyCover_pays_the_premium_and_the_desk_keeps_note() public {
        (uint256 quoted, uint16 priceBps) = desk.quote(address(s), IPerpDesk.Side.BuyCover, 100e6, 1000);
        assertEq(priceBps, PAIR_BPS - NOTE_BPS); // 2154
        assertEq(quoted, 21_540_000 + 2_154_000); // the premium + 10% of it
        assertEq(_buyCover(hedger, 100e6, 1000), quoted);
        assertEq(writerT.balanceOf(hedger), 100e6);
        assertEq(noteT.balanceOf(address(desk)), 100e6);
        assertEq(usdg.balanceOf(integrator), 1_077_000);
        // LP funds put up the rest of the pair: 123.5 - the premium
        assertEq(desk.totalAssets(), LP_CAPITAL + 1_077_000);
    }

    function test_sellCover_unwinds_pairs() public {
        _buyCover(hedger, 100e6, 0);
        uint256 proceeds = _sellCover(hedger, 100e6, 0);
        assertEq(proceeds, 21_540_000);
        assertEq(noteT.balanceOf(address(desk)), 0);
        assertEq(usdg.balanceOf(address(s)), 0);
        assertEq(desk.totalAssets(), LP_CAPITAL);
    }

    function test_sellCover_unpaired_counts_against_the_cap() public {
        _mintPairs(s, hedger, 200e6);
        desk.listSeries(address(s), pricer, VOL, earnings, 150e6);
        _sellCover(hedger, 150e6, 0);
        assertEq(writerT.balanceOf(address(desk)), 150e6);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.CapExceeded.selector, uint256(50e6), uint256(0)));
        desk.quote(address(s), IPerpDesk.Side.SellCover, 50e6, 0);
    }

    function test_matched_legs_leave_the_desk_flat_with_both_spreads() public {
        desk.setSpread(address(s), 30, 40, 0);
        uint256 coverCost = _buyCover(hedger, 100e6, 0); // the Desk holds 100 NOTE
        uint256 noteCost = _buy(alice, 100e6, 0); // and sells them on: no mint
        assertEq(noteT.balanceOf(address(desk)), 0);
        assertEq(writerT.balanceOf(address(desk)), 0);
        // cover at pair - (mid - 30), NOTE at mid + 40: the pair cost 123.5, the legs paid 124.2
        assertEq(coverCost, 21_840_000);
        assertEq(noteCost, 102_360_000);
        assertEq(desk.totalAssets(), LP_CAPITAL + 700_000);
        (uint256 atRisk,) = desk.risk(address(feed));
        assertEq(atRisk, 0);
    }

    // --- the two prices ---------------------------------------------------------------------

    function test_two_prices_per_leg() public {
        desk.setSpread(address(s), 30, 40, 0);
        (, uint16 noteAsk) = desk.quote(address(s), IPerpDesk.Side.BuyNote, 1e6, 0);
        (, uint16 noteBid) = desk.quote(address(s), IPerpDesk.Side.SellNote, 1e6, 0);
        (, uint16 coverAsk) = desk.quote(address(s), IPerpDesk.Side.BuyCover, 1e6, 0);
        (, uint16 coverBid) = desk.quote(address(s), IPerpDesk.Side.SellCover, 1e6, 0);
        assertEq(noteAsk, NOTE_BPS + 40);
        assertEq(noteBid, NOTE_BPS - 30);
        assertEq(coverAsk, PAIR_BPS - noteBid);
        assertEq(coverBid, PAIR_BPS - noteAsk);
        // minting a pair to sell both legs, or buying both to redeem, never pays
        assertLe(uint256(noteBid) + coverBid, PAIR_BPS);
        assertGe(uint256(noteAsk) + coverAsk, PAIR_BPS);
    }

    function test_vol_band_prices_each_side_at_its_end() public {
        desk.setSpread(address(s), 0, 0, 500);
        uint256 lo = quoter.quote(s, pricer, VOL + 500, earnings).priceBps; // the note is worth less at a higher vol
        uint256 hi = quoter.quote(s, pricer, VOL - 500, earnings).priceBps;
        assertLt(lo, hi);
        (, uint16 noteAsk) = desk.quote(address(s), IPerpDesk.Side.BuyNote, 1e6, 0);
        (, uint16 noteBid) = desk.quote(address(s), IPerpDesk.Side.SellNote, 1e6, 0);
        assertEq(noteAsk, hi);
        assertEq(noteBid, lo);
        // marks stay at the listing's vol
        assertEq(desk.midPriceBps(address(s)), NOTE_BPS);
        // a refusal at one end of the band refuses the quote
        pricer.setRange(1, 2000, 5999);
        vm.expectRevert(abi.encodeWithSelector(IPerpPricer.OutOfRange.selector, uint8(1), int64(6000)));
        desk.quote(address(s), IPerpDesk.Side.BuyNote, 1e6, 0);
    }

    function test_vol_band_takes_the_lower_and_the_higher_quote() public {
        // a model whose correction rises steeply with vol: the note is worth more at the higher vol
        pricer.setVolSlope(300);
        desk.setSpread(address(s), 0, 0, 500);
        uint256 atLow = quoter.quote(s, pricer, VOL - 500, earnings).priceBps;
        uint256 atHigh = quoter.quote(s, pricer, VOL + 500, earnings).priceBps;
        assertGt(atHigh, atLow);
        (, uint16 noteAsk) = desk.quote(address(s), IPerpDesk.Side.BuyNote, 1e6, 0);
        (, uint16 noteBid) = desk.quote(address(s), IPerpDesk.Side.SellNote, 1e6, 0);
        assertEq(noteAsk, atHigh);
        assertEq(noteBid, atLow);
    }

    function test_prices_stay_between_zero_and_the_pair() public {
        desk.setSpread(address(s), 1000, 1000, 0);
        pricer.setCorrection(3000); // model says the principal is worth more than 1
        (, uint16 noteAsk) = desk.quote(address(s), IPerpDesk.Side.BuyNote, 1e6, 0);
        (, uint16 coverBid) = desk.quote(address(s), IPerpDesk.Side.SellCover, 1e6, 0);
        assertEq(noteAsk, PAIR_BPS);
        assertEq(coverBid, 0);
        pricer.setCorrection(-30000); // the principal is worth nothing: NOTE = the reserve, bid floored at reserve - spread
        (, uint16 noteBid) = desk.quote(address(s), IPerpDesk.Side.SellNote, 1e6, 0);
        (, uint16 coverAsk) = desk.quote(address(s), IPerpDesk.Side.BuyCover, 1e6, 0);
        assertEq(noteBid, 2350 - 1000);
        assertEq(coverAsk, PAIR_BPS - 1350);
    }

    // --- after fixings: less notional per token, and released cash ------------------------------

    function test_trades_scale_with_the_notional_per_token() public {
        _fixing(1, 9000);
        uint256 perToken = s.state().notionalPerToken;
        uint256 mid = _mid();
        (uint256 cost, uint16 priceBps) = desk.quote(address(s), IPerpDesk.Side.BuyNote, 100e6, 50);
        assertEq(priceBps, mid);
        uint256 notional = 100e6 * perToken / RAY; // 98.1004
        assertEq(notional, 98_100_464);
        // ceil(notional * price) + ceil(floor(notional) * 0.5%)
        uint256 gross = (100e6 * perToken * mid + RAY * 1e4 - 1) / (RAY * 1e4);
        assertEq(cost, gross + (notional * 50 + 9999) / 10_000);

        usdg.mint(alice, cost);
        vm.startPrank(alice);
        usdg.approve(address(desk), cost);
        vm.expectEmit(address(desk));
        emit IPerpDesk.Traded(
            address(s),
            alice,
            IPerpDesk.Side.BuyNote,
            alice,
            100e6,
            notional,
            uint16(mid),
            cost,
            50,
            integrator,
            pricer.weightsHash()
        );
        desk.buy(address(s), 100e6, cost, 50, integrator, alice);
        vm.stopPrank();
        // the pairs cost their melted value
        assertEq(usdg.balanceOf(address(s)), s.previewMint(100e6));
    }

    function test_released_cash_counts_at_once_and_collect_pulls_it() public {
        _buy(alice, 100e6, 0); // the Desk holds 100 WRITER
        uint256 before = desk.totalAssets();
        _fixing(1, 5000); // knocked in: this fixing pays WRITER a * (1 - 0.5)
        (uint256 n, uint256 w, uint256 released) =
            (noteT.balanceOf(address(desk)), writerT.balanceOf(address(desk)), s.claimable(address(desk)));
        assertEq(n, 0);
        assertEq(w, 100e6);
        assertEq(released, 949_767);
        // the knock-in is the Desk's gain: its WRITER is worth more, and part of it is cash already
        uint256 writerValue = (100e6 * s.state().notionalPerToken / RAY) * (PAIR_BPS - _mid()) / 10_000;
        assertEq(desk.totalAssets(), before - 21_540_000 + writerValue + released);
        assertGt(desk.totalAssets(), before);

        uint256 idle = usdg.balanceOf(address(desk));
        vm.expectEmit(address(desk));
        emit IPerpDesk.Collected(address(s), released);
        assertEq(desk.collect(address(s)), released);
        assertEq(usdg.balanceOf(address(desk)), idle + released);
        assertEq(desk.totalAssets(), before - 21_540_000 + writerValue + released); // unchanged by the collect
        assertEq(desk.heldSeries().length, 1); // still holds WRITER
    }

    function test_collect_redeems_pairs_and_forgets_a_flat_series() public {
        _buy(alice, 100e6, 0);
        vm.prank(alice);
        noteT.transfer(address(desk), 100e6); // a donation makes the Desk hold both legs
        uint256 out = desk.collect(address(s));
        assertEq(out, 123_500_000);
        assertEq(desk.heldSeries().length, 0);
        assertEq(noteT.totalSupply(), 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.NotFactorySeries.selector, address(1)));
        desk.collect(address(1));
    }

    function test_a_closed_series_is_worth_its_released_cash() public {
        _buy(alice, 100e6, 0);
        _fixing(1, 5000);
        // the feed dies; four fixings later the series closes at the last good fixing, x = 0.5
        vm.warp(uint256(_fixingTime(s, 5)) + 9 days);
        assertEq(uint8(s.state().phase), uint8(PerpPhase.Closed));
        // nothing to quote any more: the position is cash, and LP flows work without the feed
        uint256 released = s.claimable(address(desk));
        assertApproxEqAbs(released, 50_000_000, 10); // WRITER: 1 - x per unit of notional
        assertEq(desk.totalAssets(), usdg.balanceOf(address(desk)) + released);
        assertGt(desk.maxDeposit(lp), 0);
        (uint256 atRisk,) = desk.risk(address(feed));
        assertEq(atRisk, 0);
        assertEq(desk.collect(address(s)), released);
        assertEq(desk.heldSeries().length, 0);
        assertEq(desk.totalAssets(), usdg.balanceOf(address(desk)));
        // alice's NOTE: x + R per unit of notional
        assertApproxEqAbs(s.claimable(alice), 73_500_000, 10);
    }

    // --- risk budget ----------------------------------------------------------------------------

    function test_risk_budget_is_closed_until_set_and_limits_new_positions() public {
        desk.setRiskBudget(address(feed), 0);
        uint256 quoted = _quoteOf(IPerpDesk.Side.BuyNote, 100e6, 0);
        usdg.mint(alice, quoted);
        vm.startPrank(alice);
        usdg.approve(address(desk), quoted);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.RiskBudgetExceeded.selector, uint256(21_540_000), uint256(0)));
        desk.buy(address(s), 100e6, quoted, 0, integrator, alice);
        vm.stopPrank();

        // 1% of ~1,000,000: room for WRITER marked at 0.2154 per unit, i.e. 46,425 NOTE sold
        desk.setRiskBudget(address(feed), 100);
        desk.listSeries(address(s), pricer, VOL, earnings, type(uint128).max);
        _buy(alice, 46_000e6, 0);
        (uint256 atRisk, uint256 limit) = desk.risk(address(feed));
        assertEq(atRisk, 46_000e6 * 2154 / 10_000);
        assertEq(limit, desk.totalAssets() / 100);
        quoted = _quoteOf(IPerpDesk.Side.BuyNote, 1_000e6, 0);
        usdg.mint(alice, quoted);
        vm.startPrank(alice);
        usdg.approve(address(desk), quoted);
        vm.expectRevert(); // RiskBudgetExceeded(atRisk after the trade, limit)
        desk.buy(address(s), 1_000e6, quoted, 0, integrator, alice);
        vm.stopPrank();
        // a trade that shrinks the position always works
        _sell(alice, 46_000e6, 0);
        (atRisk,) = desk.risk(address(feed));
        assertEq(atRisk, 0);
    }

    function test_risk_counts_note_above_its_reserve() public {
        _buyCover(hedger, 100e6, 0); // the Desk holds 100 NOTE at 1.0196, of which 0.235 is the reserve
        (uint256 atRisk,) = desk.risk(address(feed));
        assertEq(atRisk, 101_960_000 - 23_500_000);
    }

    function test_risk_budget_is_per_feed() public {
        MockChainlinkFeed feed2 = new MockChainlinkFeed("RHNVDA / USD");
        PerpTerms memory t = _terms(first);
        t.feed = address(feed2);
        PerpSeries s2 = _create(t);
        feed2.pushRoundAt(int256(uint256(INITIAL)), first);
        _recorder(s2).recordFixing(first, uint80(feed2.latestRound()));
        feed2.pushRoundAt(_price(9000), uint40(block.timestamp));
        desk.listSeries(address(s2), pricer, VOL, earnings, CAP);
        // no budget for the second stock yet
        usdg.mint(alice, 200e6);
        vm.startPrank(alice);
        usdg.approve(address(desk), 200e6);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.RiskBudgetExceeded.selector, uint256(2_154_000), uint256(0)));
        desk.buy(address(s2), 10e6, 200e6, 0, integrator, alice);
        desk.buy(address(s), 10e6, 200e6, 0, integrator, alice); // the first stock has its own
        vm.stopPrank();
        (uint256 atRisk1,) = desk.risk(address(feed));
        (uint256 atRisk2, uint256 limit2) = desk.risk(address(feed2));
        assertEq(atRisk1, 2_154_000);
        assertEq(atRisk2, 0);
        assertEq(limit2, 0);
    }

    function test_risk_with_an_unquotable_series_counts_the_most() public {
        _buy(alice, 100e6, 0); // 100 WRITER
        _buyCover(hedger, 150e6, 0); // paired: the Desk now holds 50 NOTE
        assertEq(noteT.balanceOf(address(desk)), 50e6);
        vm.warp(block.timestamp + 27 hours); // the feed goes stale (a weekday: no weekend price)
        (uint256 atRisk, uint256 limit) = desk.risk(address(feed));
        assertEq(atRisk, 50e6); // 1 USDG per unit of notional
        // its NOTE counts only its reserve as an asset
        assertEq(limit, usdg.balanceOf(address(desk)) + 50e6 * 235 / 1000);
        vm.expectRevert(); // FeedStale
        desk.totalAssets();
    }

    // --- LP flows ---------------------------------------------------------------------------------

    function test_lp_flows_pause_while_a_held_series_cannot_be_quoted() public {
        _buy(alice, 100e6, 0);
        assertGt(desk.maxDeposit(lp), 0);
        assertGt(desk.maxWithdraw(lp), 0);
        // a pending fixing: the feed has a round at the fixing time, nobody recorded it
        feed.pushRoundAt(_price(9000), first + WEEK);
        vm.warp(uint256(first) + WEEK + 1);
        _spot(9000);
        assertEq(desk.maxDeposit(lp), 0);
        assertEq(desk.maxMint(lp), 0);
        assertEq(desk.maxWithdraw(lp), 0);
        assertEq(desk.maxRedeem(lp), 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpQuoter.FixingPending.selector, first + WEEK));
        desk.totalAssets();
        _recorder(s).recordFixing(first + WEEK, uint80(feed.latestRound() - 1));
        assertGt(desk.maxDeposit(lp), 0);
        // the earnings date passes
        vm.warp(uint256(earnings) + 1);
        assertEq(desk.maxWithdraw(lp), 0);
    }

    function test_without_positions_lp_flows_never_pause() public {
        vm.warp(block.timestamp + 30 days);
        assertEq(desk.totalAssets(), LP_CAPITAL);
        vm.prank(lp);
        desk.withdraw(LP_CAPITAL, lp, lp);
        assertEq(usdg.balanceOf(lp), LP_CAPITAL);
    }

    function test_maxWithdraw_is_capped_by_idle_usdg() public {
        desk.listSeries(address(s), pricer, VOL, earnings, type(uint128).max);
        _buyCover(hedger, 900_000e6, 0); // most of the vault is now locked in pairs
        uint256 idle = usdg.balanceOf(address(desk));
        assertLt(idle, 100_000e6);
        assertEq(desk.maxWithdraw(lp), idle);
        vm.prank(lp);
        vm.expectRevert(); // ERC4626ExceededMaxWithdraw
        desk.withdraw(idle + 1, lp, lp);
        vm.prank(lp);
        desk.withdraw(idle, lp, lp);
    }

    function test_fees_and_spreads_accrue_to_lps() public {
        desk.setSpread(address(s), 30, 40, 0);
        _buy(alice, 100e6, 100);
        _sell(alice, 100e6, 100);
        // spread: 100 * (0.0040 + 0.0030); fees: 2 * 1% of 100, half of each kept
        assertEq(desk.totalAssets(), LP_CAPITAL + 700_000 + 1_000_000);
        assertEq(usdg.balanceOf(integrator), 1_000_000);
        uint256 shares = desk.balanceOf(lp);
        vm.prank(lp);
        desk.redeem(shares, lp, lp);
        assertApproxEqAbs(usdg.balanceOf(lp), LP_CAPITAL + 1_700_000, 2);
    }

    // --- the redemption queue (IDeskQueue, unchanged from v1) ---------------------------------------

    function test_queue_request_fill_claim() public {
        desk.listSeries(address(s), pricer, VOL, earnings, type(uint128).max);
        _buyCover(hedger, 900_000e6, 0); // idle is short
        uint256 shares = desk.balanceOf(lp);
        vm.prank(lp);
        uint256 id = desk.requestRedeem(shares);
        assertEq(desk.queuedShares(), shares);
        assertEq(desk.maxWithdraw(lp), 0);

        // the idle USDG fills part of it
        (uint256 filled, uint256 setAside) = desk.processQueue(10);
        assertGt(filled, 0);
        assertLt(filled, shares);
        assertEq(desk.claimableAssets(lp), setAside);
        assertEq(desk.reservedAssets(), setAside);
        // a trade that would add to the Desk's position can't jump the queue
        uint256 quoted = _quoteOf(IPerpDesk.Side.BuyCover, 10e6, 0);
        usdg.mint(hedger, quoted);
        vm.startPrank(hedger);
        usdg.approve(address(desk), quoted);
        vm.expectRevert(abi.encodeWithSelector(IDeskQueue.QueuePending.selector, shares - filled));
        desk.buyCover(address(s), 10e6, quoted, 0, integrator, hedger);
        vm.stopPrank();
        // the hedger sells the cover back: the pairs unwind, USDG is free, the queue is paid
        _sellCover(hedger, 900_000e6, 0);
        desk.processQueue(10);
        assertEq(desk.queuedShares(), 0);
        (address owner_, uint256 rest) = desk.redeemRequest(id);
        assertEq(owner_, lp);
        assertEq(rest, 0);
        uint256 claimable = desk.claimableAssets(lp);
        vm.prank(lp);
        assertEq(desk.claim(lp), claimable);
        assertApproxEqAbs(usdg.balanceOf(lp), LP_CAPITAL, 3);
        assertEq(desk.totalSupply(), 0);
    }

    function test_queue_request_errors_and_cancel() public {
        vm.startPrank(lp);
        vm.expectRevert(abi.encodeWithSelector(IDeskQueue.RequestTooSmall.selector, uint256(1), uint256(10e12)));
        desk.requestRedeem(1);
        uint256 id = desk.requestRedeem(20e12);
        vm.stopPrank();
        vm.expectRevert(abi.encodeWithSelector(IDeskQueue.NotRequestOwner.selector, id));
        desk.cancelRedeem(id);
        vm.expectRevert(IDeskQueue.NothingToClaim.selector);
        desk.claim(alice);
        vm.prank(lp);
        assertEq(desk.cancelRedeem(id), 20e12);
        assertEq(desk.queuedShares(), 0);
        (uint256 head, uint256 length) = desk.queue();
        assertEq(head, 0);
        assertEq(length, 1);
        desk.processQueue(1);
        (head,) = desk.queue();
        assertEq(head, 1);
    }

    // --- the weekend price ---------------------------------------------------------------------------

    /// Friday's close, 20:00 New York in summer = Saturday 00:00 UTC of the fixture's week:
    /// the last feed round before the weekend, one hour before the Desk's window opens.
    function _fridayClose() internal returns (uint256 close) {
        close = (uint256(T0) / 1 days) * 1 days + 5 days; // T0 is a Monday
        vm.warp(close);
        _spot(9000);
    }

    function test_weekend_price_is_capped_and_carries_a_wider_spread() public {
        MockWeekendSource source = new MockWeekendSource();
        vm.expectEmit(address(desk));
        emit IPerpDesk.WeekendSet(address(feed), address(source), 300, 50);
        desk.setWeekend(address(feed), source, 300, 50);
        desk.setSpread(address(s), 30, 40, 0);
        _buy(alice, 100e6, 0); // a position, so the marks matter

        uint256 close = _fridayClose();
        vm.warp(close + 34 hours); // Sunday 10:00 UTC

        // the pool trades at 92: inside the cap, used as it is
        source.set(address(feed), uint256(_price(9200)));
        (uint256 spot, bool weekendPrice) = desk.spotOf(address(s));
        assertEq(spot, uint256(_price(9200)));
        assertTrue(weekendPrice);
        uint256 mid = quoter.quoteAtSpot(s, pricer, VOL, earnings, spot).priceBps;
        assertEq(desk.midPriceBps(address(s)), mid);
        (, uint16 ask) = desk.quote(address(s), IPerpDesk.Side.BuyNote, 1e6, 0);
        (, uint16 bid) = desk.quote(address(s), IPerpDesk.Side.SellNote, 1e6, 0);
        assertEq(ask, mid + 40 + 50);
        assertEq(bid, mid - 30 - 50);

        // the pool is pushed to 70: the Desk prices at the cap, 3% below the feed's last price
        source.set(address(feed), uint256(_price(7000)));
        (spot,) = desk.spotOf(address(s));
        assertEq(spot, uint256(_price(9000)) * 97 / 100);
        source.set(address(feed), uint256(_price(12000)));
        (spot,) = desk.spotOf(address(s));
        assertEq(spot, uint256(_price(9000)) * 103 / 100);

        // trades work; LP flows stay paused (the share price needs the live feed)
        _sell(alice, 10e6, 0);
        assertEq(desk.maxDeposit(lp), 0);
        assertEq(desk.maxWithdraw(lp), 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpQuoter.FeedStale.selector, uint40(close)));
        desk.totalAssets();
        vm.expectRevert(abi.encodeWithSelector(IPerpQuoter.FeedStale.selector, uint40(close)));
        desk.navPriceBps(address(s));
        // the risk budget marks at the weekend price instead of counting the most
        (uint256 atRisk,) = desk.risk(address(feed));
        assertLt(atRisk, 90e6 / 2);
        assertGt(atRisk, 0);
    }

    /// From the moment the window opens, Friday's close is not a price to trade or to let
    /// LPs in and out at, however recent it is.
    function test_fridays_close_is_blind_from_the_start_of_the_window() public {
        MockWeekendSource source = new MockWeekendSource();
        desk.setWeekend(address(feed), source, 1000, 100);
        _buy(alice, 10_000e6, 0);
        uint256 close = _fridayClose();

        // Saturday 00:59 UTC: before the window, the close is an hour old and live
        vm.warp(close + 1 hours - 60);
        (, bool weekendPrice) = desk.spotOf(address(s));
        assertFalse(weekendPrice);
        assertGt(desk.maxDeposit(lp), 0);
        (, uint16 bidAtClose) = desk.quote(address(s), IPerpDesk.Side.SellNote, 1e6, 0);

        // Saturday 13:00 UTC: 13 hours old, well inside the quoter's 26 hours, but the pool is 9% lower
        vm.warp(close + 13 hours);
        source.set(address(feed), uint256(_price(8190)));
        uint256 spot;
        (spot, weekendPrice) = desk.spotOf(address(s));
        assertTrue(weekendPrice);
        assertEq(spot, uint256(_price(8190)));
        (, uint16 bid) = desk.quote(address(s), IPerpDesk.Side.SellNote, 1e6, 0);
        assertLt(bid, bidAtClose - 300); // nobody sells NOTE to the Desk at Friday's price
        assertEq(desk.maxDeposit(lp), 0); // and no LP enters or leaves at Friday's share price
        assertEq(desk.maxWithdraw(lp), 0);

        // without a source the Desk refuses over the weekend instead of trading at the close
        desk.setWeekend(address(feed), IWeekendSource(address(0)), 0, 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpQuoter.FeedStale.selector, uint40(close)));
        desk.quote(address(s), IPerpDesk.Side.SellNote, 1e6, 0);
    }

    function test_weekend_window_and_a_feed_that_was_alive() public {
        MockWeekendSource source = new MockWeekendSource();
        source.set(address(feed), uint256(_price(9000)));
        desk.setWeekend(address(feed), source, 300, 50);
        uint256 close = _fridayClose();
        bytes memory stale = abi.encodeWithSelector(IPerpQuoter.FeedStale.selector, uint40(close));

        // Saturday 01:00 UTC: the window opens
        vm.warp(close + 1 hours);
        (, bool weekendPrice) = desk.spotOf(address(s));
        assertTrue(weekendPrice);
        // Sunday 23:59 UTC: still inside
        vm.warp(close + 48 hours - 60);
        (, weekendPrice) = desk.spotOf(address(s));
        assertTrue(weekendPrice);
        // Monday 00:00 UTC: the window is over; a feed still silent is a halt, not a weekend
        vm.warp(close + 48 hours);
        vm.expectRevert(stale);
        desk.spotOf(address(s));

        // a feed that does update over the weekend is live: its price, no weekend spread
        vm.warp(close + 7 days);
        _spot(9000);
        vm.warp(close + 7 days + 12 hours); // Saturday noon
        _spot(9100);
        uint256 spot;
        (spot, weekendPrice) = desk.spotOf(address(s));
        assertFalse(weekendPrice);
        assertEq(spot, uint256(_price(9100)));

        // a feed that was halted all Friday is not revived by the weekend: its last round is
        // Thursday's close, more than 4 hours before the window
        uint256 thursdayClose = close + 14 days - 1 days; // Friday 00:00 UTC, two weeks on
        vm.warp(thursdayClose);
        _spot(9000);
        vm.warp(thursdayClose + 26 hours); // Saturday 02:00 UTC
        vm.expectRevert(abi.encodeWithSelector(IPerpQuoter.FeedStale.selector, uint40(thursdayClose)));
        desk.spotOf(address(s));
    }

    function test_weekend_needs_a_source_and_bounded_settings() public {
        uint256 close = _fridayClose();
        vm.warp(close + 34 hours);
        vm.expectRevert(abi.encodeWithSelector(IPerpQuoter.FeedStale.selector, uint40(close)));
        desk.quote(address(s), IPerpDesk.Side.BuyNote, 1e6, 0);
        MockWeekendSource source = new MockWeekendSource();
        desk.setWeekend(address(feed), source, 300, 50);
        vm.expectRevert(MockWeekendSource.NoPrice.selector); // the source has no price: no quote
        desk.quote(address(s), IPerpDesk.Side.BuyNote, 1e6, 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.SpreadTooWide.selector, uint16(1001)));
        desk.setWeekend(address(feed), source, 1001, 50);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.SpreadTooWide.selector, uint16(1001)));
        desk.setWeekend(address(feed), source, 300, 1001);
        IPerpDesk.Weekend memory w = desk.weekend(address(feed));
        assertEq(address(w.source), address(source));
        assertEq(w.capBps, 300);
        assertEq(w.spreadBps, 50);
    }

    // --- around a fixing: nothing to gain by timing it (regressions from the review) --------------

    function _formulaPricer() internal returns (PerpFormulaPricer) {
        return new PerpFormulaPricer(pricer.product(), _bands());
    }

    /// A formula-priced listing: an LP who deposits a minute before a clean fixing and leaves a
    /// minute after it takes nothing from the others. The pricer's accrual carries the
    /// release in the price before the fixing.
    function test_no_lp_gain_from_depositing_around_a_clean_fixing() public {
        desk.listSeries(address(s), _formulaPricer(), VOL, earnings, CAP);
        _buyCover(alice, 100_000e6, 0); // the Desk holds 100,000 NOTE
        uint40 t1 = _fixingTime(s, 1);
        vm.warp(uint256(t1) - 60);
        _spot(9000);
        uint256 navBefore = desk.totalAssets();
        address attacker = makeAddr("attacker");
        uint256 stake = 1_000_000e6;
        uint256 shares = _deposit(attacker, stake);
        _fixing(1, 9000); // the spot does not move
        uint256 navAfter = desk.totalAssets() - stake;
        assertApproxEqAbs(navAfter, navBefore, 15e6); // within rounding to whole bps of 100,000 notional
        desk.collect(address(s));
        vm.startPrank(attacker);
        uint256 out = desk.redeem(desk.maxRedeem(attacker), attacker, attacker);
        uint256 left = desk.balanceOf(attacker);
        vm.stopPrank();
        // (before the accrual the attacker left with 204 USDG of the other LPs' money)
        assertLt(out + desk.convertToAssets(left), stake + 8e6);
        assertEq(shares, shares); // silence the unused variable: the attacker redeems what it can
    }

    /// A clean note below the barrier just before a fixing is about to knock in: the formula
    /// pricer refuses there (its knock-in band), so LP flows pause instead of going at a
    /// price that is about to step.
    function test_lp_flows_pause_in_the_formula_pricers_band_before_a_knock_in() public {
        desk.listSeries(address(s), _formulaPricer(), VOL, earnings, CAP);
        desk.setSpread(address(s), 20, 30, 0);
        _buy(alice, 90_000e6, 0); // the Desk holds 90,000 WRITER
        uint40 t1 = _fixingTime(s, 1);
        vm.warp(uint256(t1) - 60);
        _spot(5950);
        assertEq(desk.maxDeposit(lp), 0);
        assertEq(desk.maxWithdraw(lp), 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpPricer.Uncertified.selector, uint8(0)));
        desk.totalAssets();
        usdg.mint(alice, 100e6);
        vm.startPrank(alice);
        usdg.approve(address(desk), 100e6);
        vm.expectRevert(); // ERC4626ExceededMaxDeposit
        desk.deposit(100e6, alice);
        vm.stopPrank();
    }

    /// A fixing left unrecorded past its fallback deadline: an LP flow settles the held
    /// series first, so a recording made afterwards can no longer move the share price.
    function test_a_late_recording_cannot_move_the_share_price_under_an_lp() public {
        _buy(alice, 50_000e6, 0);
        uint40 t1 = _fixingTime(s, 1);
        uint40 t2 = _fixingTime(s, 2);
        vm.warp(uint256(t1) + 1);
        feed.pushRoundAt(_price(5000), t1); // the round exists, nobody records it
        uint80 round1 = uint80(feed.latestRound());
        _fixAt(s, t2, _price(5000));
        vm.warp(uint256(t1) + 9 days + 1); // past fixing 1's deadline
        _spot(5000);
        assertEq(s.state().fixingsDone, 2); // the views fell back for fixing 1

        address attacker = makeAddr("attacker");
        uint256 stake = 1_000_000e6;
        uint256 shares = _deposit(attacker, stake); // settles the held series: the fallback is stored
        uint256 nav = desk.totalAssets();
        _recorder(s).recordFixing(t1, round1); // too late to matter
        assertEq(desk.totalAssets(), nav);
        vm.prank(attacker);
        uint256 out = desk.redeem(shares, attacker, attacker);
        assertLe(out, stake);
    }

    /// The Desk accepts a listing only at vols its pricer is built for; for the formula-only
    /// pricer those are vols the closed form can carry.
    function test_listing_refuses_a_vol_the_formula_pricer_is_not_built_for() public {
        PerpFormulaPricer fp = _formulaPricer();
        desk.listSeries(address(s), fp, VOL, earnings, CAP);
        _buy(alice, 1_000e6, 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.ModelMismatch.selector, uint8(3)));
        desk.listSeries(address(s), fp, 600, earnings, CAP);
        vm.expectRevert(abi.encodeWithSelector(IPerpDesk.ModelMismatch.selector, uint8(3)));
        desk.setSpread(address(s), 0, 0, 3600); // 55% - 36% is below its 20%
        assertGt(desk.totalAssets(), 0);
    }

    // --- limits and guards ---------------------------------------------------------------------------

    function test_held_series_limit() public {
        desk.setRiskBudget(address(feed), 10_000);
        uint256 max = desk.MAX_HELD_SERIES();
        usdg.mint(alice, 1_000e6);
        vm.prank(alice);
        usdg.approve(address(desk), type(uint256).max);
        for (uint256 i = 0; i <= max; i++) {
            PerpTerms memory t = _terms(first);
            t.couponReserve = uint64(100_000 + 100 * i);
            address other = factory.createSeries(t);
            desk.listSeries(other, pricer, VOL, earnings, CAP);
            if (i == max) vm.expectRevert(PerpDesk.HeldSeriesLimit.selector);
            vm.prank(alice);
            desk.buy(other, 1e6, type(uint256).max, 0, integrator, alice);
        }
        assertEq(desk.heldSeries().length, max);
    }

    function test_usdg_pause_blocks_trading_not_views() public {
        _buy(alice, 10e6, 0);
        usdg.setPaused(true);
        assertGt(desk.totalAssets(), 0);
        desk.quote(address(s), IPerpDesk.Side.SellNote, 1e6, 0);
        vm.startPrank(alice);
        noteT.approve(address(desk), 1e6);
        vm.expectRevert(MockUSDG.Paused.selector);
        desk.sell(address(s), 1e6, 0, 0, integrator, alice);
        vm.stopPrank();
    }

    function test_selling_without_tokens_reverts() public {
        _buy(alice, 10e6, 0);
        vm.startPrank(bob);
        noteT.approve(address(desk), 1e6);
        vm.expectRevert(abi.encodeWithSelector(IERC20Errors.ERC20InsufficientBalance.selector, bob, 0, 1e6));
        desk.sell(address(s), 1e6, 0, 0, integrator, bob);
        vm.stopPrank();
    }

    // --- fuzz ---------------------------------------------------------------------------------------------

    /// Buying and selling straight back costs the trader the spread and the rounding, never the LPs.
    function testFuzz_round_trip_never_costs_lps(uint256 amount, uint16 bid, uint16 ask, uint256 fixings, bool cover)
        public
    {
        fixings = bound(fixings, 0, 3);
        for (uint256 n = 1; n <= fixings; n++) {
            _fixing(n, 9000);
        }
        amount = bound(amount, 1, 50_000e6);
        desk.setSpread(address(s), uint16(bound(bid, 0, 1000)), uint16(bound(ask, 0, 1000)), 0);
        uint256 before = desk.totalAssets();
        uint256 cost = cover ? _buyCover(alice, amount, 0) : _buy(alice, amount, 0);
        uint256 proceeds = cover ? _sellCover(alice, amount, 0) : _sell(alice, amount, 0);
        assertLe(proceeds, cost);
        assertGe(desk.totalAssets(), before);
        assertEq(noteT.balanceOf(address(desk)), 0);
        assertEq(writerT.balanceOf(address(desk)), 0);
    }

    /// Minting a pair to sell both legs, or buying both legs to redeem the pair, never pays.
    function testFuzz_no_pair_arbitrage(uint256 amount, uint16 bid, uint16 ask, uint16 band, int16 correction) public {
        amount = bound(amount, 1e6, 10_000e6);
        pricer.setCorrection(int16(bound(correction, -3000, 3000)));
        desk.setSpread(
            address(s), uint16(bound(bid, 0, 1000)), uint16(bound(ask, 0, 1000)), uint16(bound(band, 0, 1500))
        );
        desk.listSeries(address(s), pricer, VOL, earnings, type(uint128).max);

        // mint and sell both legs
        uint256 pairCost = _mintPairs(s, alice, amount);
        uint256 got = _sell(alice, amount, 0) + _sellCover(alice, amount, 0);
        assertLe(got, pairCost);
        // buy both legs and redeem
        uint256 paid = _buy(bob, amount, 0) + _buyCover(bob, amount, 0);
        vm.prank(bob);
        uint256 redeemed = s.redeemPair(amount, bob);
        assertGe(paid, redeemed);
    }
}
