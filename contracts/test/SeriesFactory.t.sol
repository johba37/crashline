// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol";
import {Base} from "./Base.t.sol";
import {SeriesFactory} from "../src/SeriesFactory.sol";
import {NoteSeries} from "../src/NoteSeries.sol";
import {SeriesToken} from "../src/SeriesToken.sol";
import {FixingsRecorder} from "../src/FixingsRecorder.sol";
import {ISeriesFactory} from "../src/interfaces/ISeriesFactory.sol";
import {ISeriesToken} from "../src/interfaces/ISeriesToken.sol";
import {INoteSeries, SeriesTerms} from "../src/interfaces/INoteSeries.sol";

contract SixDecimalFeed {
    function decimals() external pure returns (uint8) {
        return 6;
    }
}

contract EighteenDecimalToken {
    function decimals() external pure returns (uint8) {
        return 18;
    }
}

contract SeriesFactoryTest is Base {
    function test_createSeries_matches_prediction_and_registers() public {
        SeriesTerms memory t = _terms(T0 + 1 days);
        (address ps, address pn, address pw) = factory.predictSeries(t);
        bytes32 id = factory.seriesId(t);
        assertEq(id, keccak256(abi.encode(t)));

        vm.expectEmit(true, true, false, true, address(factory));
        emit ISeriesFactory.SeriesCreated(id, address(feed), ps, pn, pw, t);
        NoteSeries s = _create(t);

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
        // 1 + 25 bps * 27 periods
        assertEq(s.maxPayoutPerNote(), 1_067_500);
        assertEq(s.FALLBACK_GRACE(), 1 days);
    }

    function test_createSeries_idempotent() public {
        SeriesTerms memory t = _terms(T0 + 1 days);
        address a = factory.createSeries(t);
        vm.recordLogs();
        address b = factory.createSeries(t);
        assertEq(a, b);
        assertEq(vm.getRecordedLogs().length, 0, "no second deployment");
        assertEq(factory.allSeries().length, 1);

        // different terms -> different series, same recorder
        SeriesTerms memory t2 = _terms(T0 + 2 days);
        address c = factory.createSeries(t2);
        assertTrue(c != a);
        assertEq(address(NoteSeries(c).recorder()), address(NoteSeries(a).recorder()));
        assertEq(factory.allSeries().length, 2);
    }

    function test_tokens_metadata() public {
        NoteSeries s = _create(_terms(T0 + 1 days));
        SeriesToken n = _note(s);
        SeriesToken w = _writer(s);
        assertEq(n.decimals(), 6);
        assertEq(w.decimals(), 6);
        assertTrue(n.isNote());
        assertFalse(w.isNote());
        assertEq(n.series(), address(s));
        assertEq(w.series(), address(s));
        string memory tag = n.symbol();
        assertEq(bytes(tag).length, bytes("NOTE-0x12345678").length);
        assertEq(w.name(), string.concat("Autocall WRITER ", _slice(w.symbol(), 7)));
    }

    function _slice(string memory s, uint256 from) internal pure returns (string memory out) {
        bytes memory b = bytes(s);
        bytes memory o = new bytes(b.length - from);
        for (uint256 i = from; i < b.length; i++) {
            o[i - from] = b[i];
        }
        out = string(o);
    }

    function test_deployRecorder_idempotent_and_deterministic() public {
        address predicted = factory.recorderOf(address(feed));
        assertEq(predicted.code.length, 0);
        vm.expectEmit(true, false, false, true, address(factory));
        emit ISeriesFactory.RecorderDeployed(address(feed), predicted);
        address r = factory.deployRecorder(address(feed));
        assertEq(r, predicted);
        assertEq(address(FixingsRecorder(r).feed()), address(feed));
        vm.recordLogs();
        assertEq(factory.deployRecorder(address(feed)), r);
        assertEq(vm.getRecordedLogs().length, 0);
    }

    function test_revert_BadSchedule() public {
        SeriesTerms memory t = _terms(0);
        vm.expectRevert(ISeriesFactory.BadSchedule.selector);
        factory.createSeries(t);

        t = _terms(T0);
        t.observationInterval = 1 hours - 1;
        vm.expectRevert(ISeriesFactory.BadSchedule.selector);
        factory.createSeries(t);

        t = _terms(T0);
        t.observationCount = 0;
        vm.expectRevert(ISeriesFactory.BadSchedule.selector);
        factory.createSeries(t);

        t = _terms(T0);
        t.observationCount = 105;
        vm.expectRevert(ISeriesFactory.BadSchedule.selector);
        factory.createSeries(t);

        t = _terms(type(uint40).max - 26 * WEEK);
        vm.expectRevert(ISeriesFactory.BadSchedule.selector);
        factory.createSeries(t);

        // edges that are allowed
        t = _terms(1);
        t.observationInterval = 1 hours;
        t.observationCount = 104;
        factory.createSeries(t);
    }

    function test_revert_BadBarriers() public {
        SeriesTerms memory t = _terms(T0);
        t.kiBarrierBps = 0;
        vm.expectRevert(ISeriesFactory.BadBarriers.selector);
        factory.createSeries(t);

        t = _terms(T0);
        t.kiBarrierBps = 10001;
        vm.expectRevert(ISeriesFactory.BadBarriers.selector);
        factory.createSeries(t);

        t = _terms(T0);
        t.acBarrierBps = 20001;
        vm.expectRevert(ISeriesFactory.BadBarriers.selector);
        factory.createSeries(t);

        t = _terms(T0);
        t.kiBarrierBps = 20000;
        t.acBarrierBps = 20000;
        factory.createSeries(t); // ki == ac == max is allowed
    }

    function test_revert_BadCoupon() public {
        SeriesTerms memory t = _terms(T0);
        t.couponBpsPerPeriod = 10001;
        vm.expectRevert(ISeriesFactory.BadCoupon.selector);
        factory.createSeries(t);
        t.couponBpsPerPeriod = 10000;
        factory.createSeries(t);
    }

    function test_revert_BadFeed() public {
        SeriesTerms memory t = _terms(T0);
        t.feed = address(new SixDecimalFeed());
        vm.expectRevert(ISeriesFactory.BadFeed.selector);
        factory.createSeries(t);

        t.feed = makeAddr("eoa");
        vm.expectRevert(ISeriesFactory.BadFeed.selector);
        factory.createSeries(t);

        vm.expectRevert(ISeriesFactory.BadFeed.selector);
        factory.deployRecorder(address(this)); // no decimals() at all
    }

    function test_revert_BadCollateral() public {
        address bad = address(new EighteenDecimalToken());
        vm.expectRevert(ISeriesFactory.BadCollateral.selector);
        new SeriesFactory(bad);
    }

    // --- provenance ---------------------------------------------------------------

    function test_only_factory_initializes_clones() public {
        NoteSeries s = _create(_terms(T0 + 1 days));
        SeriesTerms memory t = _terms(T0 + 1 days);

        vm.expectRevert(INoteSeries.OnlyFactory.selector);
        s.initialize(bytes32(uint256(7)), t, alice, alice, alice, address(usdg));
        vm.prank(address(factory));
        vm.expectRevert(INoteSeries.AlreadyInitialized.selector);
        s.initialize(bytes32(uint256(7)), t, alice, alice, alice, address(usdg));

        SeriesToken n = _note(s);
        vm.expectRevert(ISeriesToken.OnlyFactory.selector);
        n.initialize(alice, true, 6, "x", "x");
        vm.prank(address(factory));
        vm.expectRevert(ISeriesToken.AlreadyInitialized.selector);
        n.initialize(alice, true, 6, "x", "x");
    }

    function test_implementations_are_inert() public {
        NoteSeries impl = NoteSeries(factory.seriesImplementation());
        SeriesToken timpl = SeriesToken(factory.tokenImplementation());
        vm.prank(address(factory));
        vm.expectRevert(INoteSeries.AlreadyInitialized.selector);
        impl.initialize(bytes32(uint256(7)), _terms(T0), alice, alice, alice, address(usdg));
        vm.prank(address(factory));
        vm.expectRevert(ISeriesToken.AlreadyInitialized.selector);
        timpl.initialize(alice, true, 6, "x", "x");
    }

    /// Someone else's clone of the real implementation can never be initialized,
    /// so it can't mint or burn and isn't a series anywhere.
    function test_foreign_clone_is_rejected() public {
        address rogue = Clones.clone(factory.seriesImplementation());
        vm.expectRevert(INoteSeries.OnlyFactory.selector);
        NoteSeries(rogue).initialize(bytes32(uint256(7)), _terms(T0), alice, alice, alice, address(usdg));
        assertFalse(factory.isSeries(rogue));

        address rogueToken = Clones.clone(factory.tokenImplementation());
        vm.expectRevert(ISeriesToken.OnlyFactory.selector);
        SeriesToken(rogueToken).initialize(address(this), true, 6, "x", "x");
    }

    function test_token_mint_burn_only_series() public {
        NoteSeries s = _create(_terms(T0 + 1 days));
        SeriesToken n = _note(s);
        vm.expectRevert(ISeriesToken.OnlySeries.selector);
        n.mint(alice, 1);
        vm.expectRevert(ISeriesToken.OnlySeries.selector);
        n.burn(alice, 1);
        vm.prank(address(factory));
        vm.expectRevert(ISeriesToken.OnlySeries.selector);
        n.mint(alice, 1);
    }

    function testFuzz_seriesId_unique_per_terms(uint40 a, uint40 b) public {
        a = uint40(bound(a, 1, 2_000_000_000));
        b = uint40(bound(b, 1, 2_000_000_000));
        vm.assume(a != b);
        address sa = factory.createSeries(_terms(a));
        address sb = factory.createSeries(_terms(b));
        assertTrue(sa != sb);
        assertEq(factory.createSeries(_terms(a)), sa);
    }
}
