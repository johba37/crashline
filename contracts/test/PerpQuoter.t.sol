// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {PerpBase} from "./PerpBase.t.sol";
import {MockPerpPricer} from "./mocks/MockPerpPricer.sol";
import {PerpQuoter} from "../src/PerpQuoter.sol";
import {PerpSeries} from "../src/PerpSeries.sol";
import {PerpFormula} from "../src/PerpFormula.sol";
import {PerpFormulaPricer} from "../src/PerpFormulaPricer.sol";
import {IPerpQuoter, PerpQuote} from "../src/interfaces/IPerpQuoter.sol";
import {IPerpSeries, PerpTerms} from "../src/interfaces/IPerpSeries.sol";
import {IPerpPricer, PerpPricerInputs, PerpProduct} from "../src/interfaces/IPerpPricer.sol";

contract PerpQuoterTest is PerpBase {
    uint16 constant VOL = 5500;
    PerpQuoter quoter;
    MockPerpPricer pricer;
    PerpFormulaPricer formulaPricer;
    PerpSeries s;
    uint40 first;

    function setUp() public override {
        super.setUp();
        quoter = new PerpQuoter(26 hours);
        pricer = new MockPerpPricer();
        formulaPricer = new PerpFormulaPricer(pricer.product(), _bands());
        first = T0 + 1 hours;
        s = _create(_terms(first));
    }

    struct Vector {
        string label;
        uint96 first;
        uint256 referenceFixing;
        uint256 spot;
        uint256 fixingsDone;
        bool knockedIn;
        uint256 secsSinceFixing;
        uint256 secsToEarnings;
        uint16 vol;
        uint64 couponReserve;
        uint256 result;
        uint256 a;
        int256 b;
    }

    /// First fixing at `first`; then `done` fixings: the reference (when it is above the
    /// first fixing, the ratchet) at fixing 1, 50% of it at fixing 2 when knocked in, 80% of
    /// it otherwise; then `now` = last fixing + secsSinceFixing with a fresh round at `spot`.
    function _stage(PerpSeries series, Vector memory v) internal {
        uint40 t0 = series.terms().firstFixing;
        _fixAt(series, t0, int256(uint256(v.first)));
        bool ratchet = v.referenceFixing > v.first;
        for (uint256 n = 1; n <= v.fixingsDone; n++) {
            uint256 price = v.referenceFixing * 8000 / 10_000;
            if (ratchet && n == 1) price = v.referenceFixing;
            else if (v.knockedIn && n == 2) price = v.referenceFixing * 5000 / 10_000;
            _fixAt(series, uint40(t0 + n * WEEK), int256(price));
        }
        vm.warp(uint256(t0) + v.fixingsDone * WEEK + v.secsSinceFixing);
        feed.pushRoundAt(int256(v.spot), uint40(block.timestamp));
    }

    function _inputs(uint256[] memory v) internal pure returns (PerpPricerInputs memory in_) {
        in_.spotBpsOfReference = uint16(v[0]);
        in_.volBpsAnnual = uint16(v[1]);
        in_.timeToNextFixingSecs = uint32(v[2]);
        in_.fixingsBeforeEarnings = uint8(v[3]);
        in_.flags = uint8(v[4]);
    }

    // --- conformity with tools/perp_quoter_vectors.py -------------------------------

    /// The formula-only pricer: PerpFormulaPricer itself (the fixing accrual, its vol range and its two bands).
    function test_quoter_vectors_formula() public {
        _quoterVectors("perp_quoter_vectors.json", true);
    }

    /// model/synthetic-p (tools/perp_quoter_vectors.py --model model/synthetic-p).
    function test_quoter_vectors_synthetic() public {
        _quoterVectors("perp_quoter_vectors_synthetic.json", false);
    }

    /// model/p1, the certified student; skipped until its vectors exist.
    function test_quoter_vectors_p1() public {
        if (!vm.exists(string.concat(vm.projectRoot(), "/test/vectors/perp_quoter_vectors_p1.json"))) {
            vm.skip(true);
            return;
        }
        _quoterVectors("perp_quoter_vectors_p1.json", false);
    }

    function _quoterVectors(string memory file, bool formulaOnly) internal {
        string memory json = vm.readFile(string.concat(vm.projectRoot(), "/test/vectors/", file));
        uint256 count = vm.parseJsonUint(json, ".count");
        assertGe(count, 50);
        assertEq(vm.parseJsonUint(json, ".product.kiBarrierBps"), 6000);
        assertEq(vm.parseJsonUint(json, ".product.meltShareWad"), MELT);
        pricer.setWeightsHash(vm.parseJsonBytes32(json, ".weightsHash"));
        for (uint256 i = 0; i < count; i++) {
            uint256 snap = vm.snapshotState();
            _quoterVector(json, string.concat(".vectors[", vm.toString(i), "]"), formulaOnly);
            vm.revertToState(snap);
        }
    }

    function _load(string memory json, string memory k) internal pure returns (Vector memory v) {
        v.label = vm.parseJsonString(json, string.concat(k, ".label"));
        v.first = uint96(vm.parseJsonUint(json, string.concat(k, ".first")));
        v.referenceFixing = vm.parseJsonUint(json, string.concat(k, ".reference"));
        v.spot = vm.parseJsonUint(json, string.concat(k, ".spot"));
        v.fixingsDone = vm.parseJsonUint(json, string.concat(k, ".fixingsDone"));
        v.knockedIn = vm.parseJsonUint(json, string.concat(k, ".knockedIn")) == 1;
        v.secsSinceFixing = vm.parseJsonUint(json, string.concat(k, ".secsSinceFixing"));
        v.secsToEarnings = vm.parseJsonUint(json, string.concat(k, ".secsToEarnings"));
        v.vol = uint16(vm.parseJsonUint(json, string.concat(k, ".vol")));
        v.couponReserve = uint64(vm.parseJsonUint(json, string.concat(k, ".couponReserve")));
        v.result = vm.parseJsonUint(json, string.concat(k, ".result"));
        v.a = vm.parseJsonUint(json, string.concat(k, ".a"));
        v.b = vm.parseJsonInt(json, string.concat(k, ".b"));
    }

    function _quoterVector(string memory json, string memory k, bool formulaOnly) internal {
        Vector memory v = _load(json, k);
        PerpTerms memory t = _terms(first);
        t.couponReserve = v.couponReserve;
        PerpSeries series = _create(t);
        _stage(series, v);
        uint40 nextEarnings = uint40(block.timestamp + v.secsToEarnings);
        IPerpPricer p = formulaOnly ? IPerpPricer(address(formulaPricer)) : IPerpPricer(address(pricer));

        uint256[] memory raw = vm.parseJsonUintArray(json, string.concat(k, ".inputs"));
        if (raw.length == 0) {
            // the spot doesn't fit the struct: the quoter refuses before any model is asked
            vm.expectRevert(abi.encodeWithSelector(IPerpPricer.OutOfRange.selector, uint8(0), int64(v.b)));
            quoter.quote(series, p, v.vol, nextEarnings);
            return;
        }
        PerpPricerInputs memory want = _inputs(raw);
        PerpPricerInputs memory got = quoter.inputs(series, v.vol, nextEarnings);
        assertEq(keccak256(abi.encode(got)), keccak256(abi.encode(want)), v.label);

        if (formulaOnly) {
            // the real PerpFormulaPricer refuses by itself
            if (v.result == 1) {
                vm.expectRevert(abi.encodeWithSelector(IPerpPricer.OutOfRange.selector, uint8(v.a), int64(v.b)));
                quoter.quote(series, p, v.vol, nextEarnings);
                return;
            }
            if (v.result == 3) {
                vm.expectRevert(abi.encodeWithSelector(IPerpPricer.Uncertified.selector, uint8(v.a)));
                quoter.quote(series, p, v.vol, nextEarnings);
                return;
            }
        } else {
            pricer.expect(keccak256(abi.encode(want)));
            if (v.result == 0) {
                pricer.setCorrection(int16(vm.parseJsonInt(json, string.concat(k, ".correctionBps"))));
            } else if (v.result == 1) {
                pricer.setRefusal(MockPerpPricer.Mode.OutOfRangeErr, uint8(v.a), int64(v.b));
                vm.expectRevert(abi.encodeWithSelector(IPerpPricer.OutOfRange.selector, uint8(v.a), int64(v.b)));
                quoter.quote(series, p, v.vol, nextEarnings);
                return;
            } else {
                pricer.setRefusal(MockPerpPricer.Mode.UncertifiedErr, uint8(v.a), 0);
                vm.expectRevert(abi.encodeWithSelector(IPerpPricer.Uncertified.selector, uint8(v.a)));
                quoter.quote(series, p, v.vol, nextEarnings);
                return;
            }
        }
        assertEq(v.result, 0, v.label);
        _checkQuote(json, k, series, p, v, nextEarnings);
    }

    function _checkQuote(
        string memory json,
        string memory k,
        PerpSeries series,
        IPerpPricer p,
        Vector memory v,
        uint40 nextEarnings
    ) internal view {
        PerpQuote memory q = quoter.quote(series, p, v.vol, nextEarnings);
        assertEq(q.formulaBps, vm.parseJsonUint(json, string.concat(k, ".formulaBps")), v.label);
        assertEq(q.correctionBps, vm.parseJsonInt(json, string.concat(k, ".correctionBps")), v.label);
        assertEq(q.couponBps, vm.parseJsonUint(json, string.concat(k, ".couponBps")), v.label);
        assertEq(q.priceBps, vm.parseJsonUint(json, string.concat(k, ".priceBps")), v.label);
        assertEq(q.weightsHash, p.weightsHash(), v.label);
        (uint16 priceBps, bytes32 hash) = quoter.notePriceBps(series, p, v.vol, nextEarnings);
        assertEq(priceBps, q.priceBps, v.label);
        assertEq(hash, q.weightsHash, v.label);
        // the same quote from a spot the caller supplies
        PerpQuote memory atSpot = quoter.quoteAtSpot(series, p, v.vol, nextEarnings, v.spot);
        assertEq(keccak256(abi.encode(atSpot)), keccak256(abi.encode(q)), v.label);
    }

    // --- refusals ---------------------------------------------------------------------

    function _live(uint256 spotBps) internal returns (uint40 nextEarnings) {
        _fix(s, 0, 10000);
        _fix(s, 1, 9000);
        vm.warp(uint256(first) + WEEK + 2 days);
        feed.pushRound(_price(spotBps));
        nextEarnings = uint40(block.timestamp + 30 days);
    }

    function test_not_live_while_pending_and_after_close() public {
        vm.expectRevert(IPerpQuoter.NotLive.selector);
        quoter.quote(s, pricer, VOL, uint40(block.timestamp + 30 days));
        _fix(s, 0, 10000);
        // four fixings missed: closed
        vm.warp(uint256(first) + 4 * WEEK + 9 days);
        feed.pushRound(_price(9000));
        vm.expectRevert(IPerpQuoter.NotLive.selector);
        quoter.quote(s, pricer, VOL, uint40(block.timestamp + 30 days));
    }

    function test_fixing_pending_refuses() public {
        uint40 nextEarnings = _live(9000);
        vm.warp(uint256(first) + 2 * WEEK); // exactly at fixing 2: still quotable, tNext = 0
        feed.pushRound(_price(9000));
        assertEq(quoter.inputs(s, VOL, nextEarnings).timeToNextFixingSecs, 0);
        vm.warp(uint256(first) + 2 * WEEK + 1);
        vm.expectRevert(abi.encodeWithSelector(IPerpQuoter.FixingPending.selector, first + 2 * WEEK));
        quoter.quote(s, pricer, VOL, nextEarnings);
        // recorded (the round pushed at the fixing time is the fixing): quotable again, one fixing later
        _recorder(s).recordFixing(first + 2 * WEEK, uint80(feed.latestRound()));
        assertEq(quoter.inputs(s, VOL, nextEarnings).timeToNextFixingSecs, WEEK - 1);
        assertEq(s.state().fixingsDone, 2);
    }

    function test_stale_feed_refuses_but_a_supplied_spot_quotes() public {
        uint40 nextEarnings = _live(9000);
        uint256 updatedAt = block.timestamp;
        vm.warp(block.timestamp + 26 hours); // exactly at the limit: fresh
        quoter.quote(s, pricer, VOL, nextEarnings);
        vm.warp(block.timestamp + 1);
        vm.expectRevert(abi.encodeWithSelector(IPerpQuoter.FeedStale.selector, uint40(updatedAt)));
        quoter.quote(s, pricer, VOL, nextEarnings);
        vm.expectRevert(abi.encodeWithSelector(IPerpQuoter.FeedStale.selector, uint40(updatedAt)));
        quoter.spot(s);
        // the feed is not read here: the caller vouches for the spot
        PerpQuote memory q = quoter.quoteAtSpot(s, pricer, VOL, nextEarnings, uint256(_price(9000)));
        assertGt(q.priceBps, 0);
        vm.expectRevert(IPerpQuoter.BadFeedAnswer.selector);
        quoter.quoteAtSpot(s, pricer, VOL, nextEarnings, 0);
    }

    function test_bad_feed_answer_refuses() public {
        uint40 nextEarnings = _live(9000);
        vm.warp(block.timestamp + 1);
        feed.pushRound(0);
        vm.expectRevert(IPerpQuoter.BadFeedAnswer.selector);
        quoter.quote(s, pricer, VOL, nextEarnings);
    }

    function test_earnings_date_must_not_have_passed() public {
        _live(9000);
        uint40 passed = uint40(block.timestamp - 1);
        vm.expectRevert(abi.encodeWithSelector(IPerpQuoter.EarningsDatePassed.selector, passed));
        quoter.quote(s, pricer, VOL, passed);
        // earnings right now are still ahead, and before the next fixing: 0 fixings come first
        assertEq(quoter.inputs(s, VOL, uint40(block.timestamp)).fixingsBeforeEarnings, 0);
        // a release exactly at a fixing time counts as before that fixing
        uint40 next = s.state().nextFixing;
        assertEq(quoter.inputs(s, VOL, next).fixingsBeforeEarnings, 0);
        assertEq(quoter.inputs(s, VOL, next + 1).fixingsBeforeEarnings, 1);
        assertEq(quoter.inputs(s, VOL, next + WEEK).fixingsBeforeEarnings, 1);
        assertEq(quoter.inputs(s, VOL, next + WEEK + 1).fixingsBeforeEarnings, 2);
        // far beyond what the struct holds: capped, so a model's range check refuses it
        assertEq(quoter.inputs(s, VOL, type(uint40).max).fixingsBeforeEarnings, type(uint8).max);
    }

    function test_series_must_be_the_models_product() public {
        uint40 nextEarnings = _live(9000);
        PerpProduct memory p = pricer.product();
        p.kiBarrierBps = 7000;
        pricer.setProduct(p);
        vm.expectRevert(abi.encodeWithSelector(IPerpQuoter.ProductMismatch.selector, uint8(0)));
        quoter.quote(s, pricer, VOL, nextEarnings);
        p.kiBarrierBps = 6000;
        p.meltShare += 1;
        pricer.setProduct(p);
        vm.expectRevert(abi.encodeWithSelector(IPerpQuoter.ProductMismatch.selector, uint8(1)));
        quoter.quote(s, pricer, VOL, nextEarnings);
        p.meltShare -= 1;
        p.fixingInterval = 1 days;
        pricer.setProduct(p);
        vm.expectRevert(abi.encodeWithSelector(IPerpQuoter.ProductMismatch.selector, uint8(2)));
        quoter.quote(s, pricer, VOL, nextEarnings);
    }

    function test_model_refusals_and_formula_refusals_bubble_up() public {
        uint40 nextEarnings = _live(9000);
        pricer.setRefusal(MockPerpPricer.Mode.UncertifiedErr, 1, 0);
        vm.expectRevert(abi.encodeWithSelector(IPerpPricer.Uncertified.selector, uint8(1)));
        quoter.quote(s, pricer, VOL, nextEarnings);
        pricer.setRefusal(MockPerpPricer.Mode.OutOfRangeErr, 1, 99);
        vm.expectRevert(abi.encodeWithSelector(IPerpPricer.OutOfRange.selector, uint8(1), int64(99)));
        quoter.quote(s, pricer, VOL, nextEarnings);
        // a model that accepts a vol the closed form can't carry: the formula refuses
        pricer.setCorrection(0);
        pricer.setRange(1, 0, 9000);
        vm.expectRevert(abi.encodeWithSelector(PerpFormula.VolOutOfRange.selector, uint256(0.05e18)));
        quoter.quote(s, pricer, 500, nextEarnings);
        // the formula-only pricer is built for a vol range the formula carries, and refuses outside it
        vm.expectRevert(abi.encodeWithSelector(IPerpPricer.OutOfRange.selector, uint8(1), int64(500)));
        quoter.quote(s, formulaPricer, 500, nextEarnings);
    }

    // --- assembly ---------------------------------------------------------------------

    function test_quote_parts_add_up_and_floor_at_the_coupon() public {
        uint40 nextEarnings = _live(9000);
        PerpQuote memory base = quoter.quote(s, formulaPricer, VOL, nextEarnings);
        // two of seven days into the week: the accrual is a * 2/7 * (1 - formula), a clean note
        int256 accrual =
            int256(uint256(MELT) * 2 days) * (10_000 - int256(uint256(base.formulaBps))) / int256(WAD * uint256(WEEK));
        assertGt(accrual, 0);
        assertEq(base.correctionBps, accrual);
        assertEq(base.couponBps, 2350); // R = 0.235 at discount 0
        assertEq(base.priceBps, uint256(int256(uint256(base.formulaBps)) + accrual) + 2350);
        assertEq(base.weightsHash, formulaPricer.weightsHash());

        pricer.setCorrection(-37);
        PerpQuote memory q = quoter.quote(s, pricer, VOL, nextEarnings);
        assertEq(q.formulaBps, base.formulaBps);
        assertEq(q.correctionBps, -37);
        assertEq(q.priceBps, base.formulaBps + 2350 - 37);

        // a correction below minus the formula: the principal floors at 0, the coupon stays
        pricer.setCorrection(-30000);
        q = quoter.quote(s, pricer, VOL, nextEarnings);
        assertEq(q.priceBps, 2350);
    }

    function test_discount_rate_shrinks_the_coupon_value() public {
        uint40 nextEarnings = _live(9000);
        PerpProduct memory p = pricer.product();
        p.discountBps = 400; // the doc's world: rho = r = 4%
        pricer.setProduct(p);
        PerpQuote memory q = quoter.quote(s, pricer, VOL, nextEarnings);
        // R * a e^(-rho tau) / (1 - (1 - a) e^(-rho dt)): about R / 1.04, a little more mid-week
        assertGt(q.couponBps, 2255);
        assertLt(q.couponBps, 2265);
        PerpQuote memory undiscounted = quoter.quote(s, formulaPricer, VOL, nextEarnings);
        assertLt(q.formulaBps, undiscounted.formulaBps);
    }

    function test_formula_pricer_views() public view {
        assertEq(formulaPricer.featureSpecVersion(), 2);
        (int64 lo, int64 hi) = formulaPricer.certifiedRange(1);
        assertEq(lo, 2000);
        assertEq(hi, 9000);
        (lo, hi) = formulaPricer.certifiedRange(0);
        assertEq(hi, 65535);
        (lo, hi) = formulaPricer.certifiedRange(2);
        assertEq(hi, int64(uint64(WEEK)));
        (lo, hi) = formulaPricer.certifiedRange(3);
        assertEq(hi, 255);
        (lo, hi) = formulaPricer.certifiedRange(4);
        assertEq(hi, 1);
        PerpProduct memory p = formulaPricer.product();
        assertEq(p.kiBarrierBps, 6000);
        assertEq(p.driftBps, 400);
        assertEq(formulaPricer.weightsHash(), keccak256(abi.encode("PerpFormulaPricer", p, _bands())));
        assertEq(formulaPricer.bands().fixingBandSecs, 6 hours);
    }

    /// The formula-only pricer's price does not step at a fixing when the spot is unchanged:
    /// just before it, the accrual is the release a * p; just after, it starts again from 0.
    function test_formula_pricer_accrual_makes_the_price_continuous_across_a_fixing() public {
        _fix(s, 0, 10000);
        uint40 t1 = first + WEEK;
        uint40 nextEarnings = t1 + 30 days;
        for (uint256 i = 0; i < 2; i++) {
            uint256 snap = vm.snapshotState();
            bool knockedIn = i == 1;
            if (knockedIn) _fix(s, 1, 5000); // knocked in from here on
            uint40 t = knockedIn ? t1 + WEEK : t1;
            // one second before the fixing, spot 80% of the reference: outside both bands
            vm.warp(uint256(t) - 1);
            feed.pushRound(_price(8000));
            PerpQuote memory before_ = quoter.quote(s, formulaPricer, VOL, nextEarnings);
            _fixAt(s, t, _price(8000));
            vm.warp(uint256(t) + 1);
            feed.pushRound(_price(8000));
            PerpQuote memory after_ = quoter.quote(s, formulaPricer, VOL, nextEarnings);
            assertEq(after_.formulaBps, before_.formulaBps);
            assertEq(after_.correctionBps, 0);
            // per unit of notional before the fixing: the release a * (p + R), and 1 - a of the price after
            uint256 payBps = knockedIn ? 8000 : 10_000;
            uint256 wantWad = uint256(MELT) * (payBps + 2350) + (WAD - uint256(MELT)) * after_.priceBps;
            assertApproxEqAbs(uint256(before_.priceBps) * WAD, wantWad, WAD); // within 1 bps of rounding
            vm.revertToState(snap);
        }
    }

    function test_formula_pricer_refuses_next_to_the_barriers_before_a_fixing() public {
        _fix(s, 0, 10000);
        uint40 t1 = first + WEEK;
        uint40 nextEarnings = t1 + 30 days;
        // clean, spot 65% of the reference: inside the knock-in band (60% +- 10%)
        vm.warp(uint256(t1) - 6 hours - 1);
        feed.pushRound(_price(6500));
        quoter.quote(s, formulaPricer, VOL, nextEarnings); // one second before the band: fine
        vm.warp(uint256(t1) - 6 hours);
        vm.expectRevert(abi.encodeWithSelector(IPerpPricer.Uncertified.selector, uint8(0)));
        quoter.quote(s, formulaPricer, VOL, nextEarnings);
        feed.pushRound(_price(7001)); // just outside the band
        quoter.quote(s, formulaPricer, VOL, nextEarnings);
        feed.pushRoundAt(_price(4999), uint40(block.timestamp + 1));
        vm.warp(block.timestamp + 1);
        quoter.quote(s, formulaPricer, VOL, nextEarnings);

        // knocked in, spot 97% of the reference: inside the heal band (100% +- 5%)
        _fix(s, 1, 5000);
        vm.warp(uint256(t1) + WEEK - 1 hours);
        feed.pushRound(_price(9700));
        vm.expectRevert(abi.encodeWithSelector(IPerpPricer.Uncertified.selector, uint8(1)));
        quoter.quote(s, formulaPricer, VOL, nextEarnings);
        feed.pushRoundAt(_price(6500), uint40(block.timestamp + 1)); // the knock-in band is for clean notes only
        vm.warp(block.timestamp + 1);
        quoter.quote(s, formulaPricer, VOL, nextEarnings);
    }

    function test_formula_pricer_is_built_only_for_vols_the_formula_carries() public {
        PerpProduct memory p = pricer.product();
        PerpFormulaPricer.Bands memory b = _bands();
        b.volMinBps = 600; // the closed form refuses 6% vol in this world
        vm.expectRevert(abi.encodeWithSelector(PerpFormula.VolOutOfRange.selector, uint256(0.06e18)));
        new PerpFormulaPricer(p, b);
        b = _bands();
        b.volMinBps = 9001;
        vm.expectRevert(PerpFormulaPricer.BadBands.selector);
        new PerpFormulaPricer(p, b);
        b = _bands();
        b.fixingBandSecs = WEEK + 1;
        vm.expectRevert(PerpFormulaPricer.BadBands.selector);
        new PerpFormulaPricer(p, b);
    }

    function test_formula_pricer_unknown_field_reverts() public {
        vm.expectRevert(abi.encodeWithSelector(IPerpPricer.OutOfRange.selector, uint8(5), int64(5)));
        formulaPricer.certifiedRange(5);
    }

    /// One quote through the formula-only pricer: what the Desk pays per price (gas report).
    function test_gas_of_one_quote() public {
        uint40 nextEarnings = _live(9000);
        uint256 g = gasleft();
        quoter.quote(s, formulaPricer, VOL, nextEarnings);
        emit log_named_uint("gas, quote with the formula-only pricer (cold)", g - gasleft());
        g = gasleft();
        quoter.quote(s, formulaPricer, VOL, nextEarnings);
        emit log_named_uint("gas, the same quote again (warm)", g - gasleft());
    }
}
