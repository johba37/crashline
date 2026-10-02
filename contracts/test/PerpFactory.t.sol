// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol";
import {PerpBase} from "./PerpBase.t.sol";
import {MockUSDG} from "../src/MockUSDG.sol";
import {SeriesFactory} from "../src/SeriesFactory.sol";
import {PerpFactory} from "../src/PerpFactory.sol";
import {PerpSeries} from "../src/PerpSeries.sol";
import {PerpToken} from "../src/PerpToken.sol";
import {IPerpFactory} from "../src/interfaces/IPerpFactory.sol";
import {ISeriesFactory} from "../src/interfaces/ISeriesFactory.sol";
import {PerpTerms} from "../src/interfaces/IPerpSeries.sol";
import {SeriesTerms} from "../src/interfaces/INoteSeries.sol";

contract PerpSixDecimalFeed {
    function decimals() external pure returns (uint8) {
        return 6;
    }
}

contract PerpEighteenDecimalToken {
    function decimals() external pure returns (uint8) {
        return 18;
    }
}

contract PerpFactoryTest is PerpBase {
    function test_createSeries_matches_prediction_and_registers() public {
        PerpTerms memory t = _terms(T0 + 1 days);
        (address ps, address pn, address pw) = factory.predictSeries(t);
        bytes32 id = factory.seriesId(t);
        assertEq(id, keccak256(abi.encode(t)));

        vm.expectEmit(true, true, false, true, address(factory));
        emit IPerpFactory.PerpSeriesCreated(id, address(feed), ps, pn, pw, t);
        PerpSeries s = _create(t);

        assertEq(address(s), ps);
        assertEq(s.note(), pn);
        assertEq(s.writer(), pw);
        assertEq(s.id(), id);
        assertEq(s.factory(), address(factory));
        assertEq(s.collateral(), address(usdg));
        assertEq(address(s.recorder()), factory.recorderOf(address(feed)));
        assertEq(factory.seriesOf(id), ps);
        assertTrue(factory.isSeries(ps));
        assertEq(factory.allSeries().length, 1);
        assertEq(keccak256(abi.encode(s.terms())), keccak256(abi.encode(t)));
        assertEq(s.pairValuePerNotional(), 1_235_000);
        assertEq(s.FALLBACK_GRACE(), 1 days);
        assertEq(s.CLOSE_AFTER_MISSED(), 4);
    }

    function test_createSeries_idempotent_and_vintages_differ() public {
        PerpTerms memory t = _terms(T0 + 1 days);
        address a = factory.createSeries(t);
        vm.recordLogs();
        address b = factory.createSeries(t);
        assertEq(a, b);
        assertEq(vm.getRecordedLogs().length, 0, "no second deployment");
        assertEq(factory.allSeries().length, 1);

        // the same terms from a later first fixing: a fresh vintage on the same recorder
        address c = factory.createSeries(_terms(T0 + 1 days + WEEK));
        assertTrue(c != a);
        assertEq(address(PerpSeries(c).recorder()), address(PerpSeries(a).recorder()));
        assertEq(factory.allSeries().length, 2);
    }

    function test_recorder_is_shared_with_v1() public {
        PerpSeries s = _create(_terms(T0 + 1 days));
        SeriesTerms memory v1 = SeriesTerms({
            feed: address(feed),
            strikeTime: T0 + 1 days,
            observationInterval: WEEK,
            observationCount: 26,
            kiBarrierBps: 6000,
            acBarrierBps: 10000,
            couponBpsPerPeriod: 25
        });
        address v1Series = v1Factory.createSeries(v1);
        assertEq(address(s.recorder()), v1Factory.recorderOf(address(feed)));
        assertFalse(factory.isSeries(v1Series));
        assertFalse(v1Factory.isSeries(address(s)));
        assertEq(factory.recorders(), address(v1Factory));
    }

    function test_tokens_metadata() public {
        PerpSeries s = _create(_terms(T0 + 1 days));
        PerpToken n = _note(s);
        PerpToken w = _writer(s);
        assertEq(n.decimals(), 6);
        assertEq(w.decimals(), 6);
        assertTrue(n.isNote());
        assertFalse(w.isNote());
        assertEq(n.series(), address(s));
        assertEq(w.series(), address(s));
        assertEq(bytes(n.symbol()).length, bytes("pNOTE-0x00000000").length);
        assertEq(bytes(w.symbol()).length, bytes("pWRITER-0x00000000").length);
        assertEq(bytes(n.name()).length, bytes("Perpetual NOTE 0x00000000").length);
    }

    function test_bad_terms_revert() public {
        PerpTerms memory t = _terms(T0 + 1 days);

        t.firstFixing = 0;
        vm.expectRevert(IPerpFactory.BadSchedule.selector);
        factory.createSeries(t);
        t = _terms(T0 + 1 days);
        t.fixingInterval = 1 hours - 1;
        vm.expectRevert(IPerpFactory.BadSchedule.selector);
        factory.createSeries(t);

        t = _terms(T0 + 1 days);
        t.kiBarrierBps = 0;
        vm.expectRevert(IPerpFactory.BadBarrier.selector);
        factory.createSeries(t);
        t.kiBarrierBps = 10_001;
        vm.expectRevert(IPerpFactory.BadBarrier.selector);
        factory.createSeries(t);

        t = _terms(T0 + 1 days);
        t.meltShare = 0;
        vm.expectRevert(IPerpFactory.BadMeltShare.selector);
        factory.createSeries(t);
        t.meltShare = 1e18;
        vm.expectRevert(IPerpFactory.BadMeltShare.selector);
        factory.createSeries(t);

        t = _terms(T0 + 1 days);
        t.couponReserve = 5e6 + 100;
        vm.expectRevert(IPerpFactory.BadCouponReserve.selector);
        factory.createSeries(t);
        // a whole number of bps only: the pair's value must be exact in the bps prices are quoted in,
        // or buying both legs at the Desk would cost less than the pair redeems for
        t.couponReserve = 235_099;
        vm.expectRevert(IPerpFactory.BadCouponReserve.selector);
        factory.createSeries(t);

        t = _terms(T0 + 1 days);
        t.feed = address(new PerpSixDecimalFeed());
        vm.expectRevert(ISeriesFactory.BadFeed.selector);
        factory.createSeries(t);
        t.feed = address(0xdead);
        vm.expectRevert(ISeriesFactory.BadFeed.selector);
        factory.createSeries(t);
        assertEq(factory.allSeries().length, 0);
    }

    function test_edge_terms_are_accepted() public {
        PerpTerms memory t = _terms(T0 + 1 days);
        t.fixingInterval = 1 hours;
        t.kiBarrierBps = 10_000;
        t.meltShare = 1e18 - 1;
        t.couponReserve = 5e6;
        PerpSeries s = _create(t);
        assertEq(s.pairValuePerNotional(), 6e6);
        t.kiBarrierBps = 1;
        t.meltShare = 1;
        t.couponReserve = 0;
        assertTrue(address(_create(t)) != address(s));
    }

    function test_collateral_must_match() public {
        address bad = address(new PerpEighteenDecimalToken());
        vm.expectRevert(PerpFactory.BadCollateral.selector);
        new PerpFactory(bad, v1Factory);
        // 6 decimals, but not the v1 factory's collateral
        MockUSDG other = new MockUSDG();
        vm.expectRevert(PerpFactory.BadCollateral.selector);
        new PerpFactory(address(other), v1Factory);
    }

    function test_foreign_clones_are_inert() public {
        PerpSeries s = _create(_terms(T0 + 1 days));
        address fake = Clones.clone(factory.seriesImplementation());
        PerpTerms memory t = s.terms();
        (address n, address w) = (s.note(), s.writer());
        vm.expectRevert(PerpSeries.OnlyFactory.selector);
        PerpSeries(fake).initialize(bytes32(uint256(1)), t, n, w, address(1), address(usdg));
        assertFalse(factory.isSeries(fake));
        address fakeToken = Clones.clone(factory.tokenImplementation());
        vm.expectRevert(PerpToken.OnlyFactory.selector);
        PerpToken(fakeToken).initialize(address(this), true, 6, "x", "x");
    }
}
