// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol";
import {MockChainlinkFeed} from "../../src/MockChainlinkFeed.sol";
import {MockUSDG} from "../../src/MockUSDG.sol";
import {SeriesFactory} from "../../src/SeriesFactory.sol";
import {NoteSeries} from "../../src/NoteSeries.sol";
import {SeriesToken} from "../../src/SeriesToken.sol";
import {FixingsRecorder} from "../../src/FixingsRecorder.sol";
import {SeriesTerms, SeriesState, Phase} from "../../src/interfaces/INoteSeries.sol";

/// Drives several series (different feeds, intervals, barriers) with random
/// mint / redeemPair / transfer / recordFixing / warp / advance / redeem, plus
/// attempts to smuggle in clones the factory didn't create.
contract CoreHandler is Test {
    MockUSDG public usdg;
    SeriesFactory public factory;
    NoteSeries[] public series;
    MockChainlinkFeed[] public feeds;
    address[] public actors;

    // rounding operations per series: each can leave < 1 base unit in the escrow
    mapping(address => uint256) public roundingOps;
    bool public rogueAccepted;
    uint256 public calls;

    constructor(MockUSDG usdg_, SeriesFactory factory_, uint40 t0) {
        usdg = usdg_;
        factory = factory_;
        actors.push(makeAddr("actor0"));
        actors.push(makeAddr("actor1"));
        actors.push(makeAddr("actor2"));
        // (interval, count, ki, ac, coupon)
        _add(t0, 604_800, 26, 6000, 10000, 25);
        _add(t0, 1 days, 8, 8000, 10500, 333);
        _add(t0, 1 hours, 4, 9000, 9000, 1_000);
    }

    function _add(uint40 t0, uint32 interval, uint8 count, uint16 ki, uint16 ac, uint16 coupon) internal {
        MockChainlinkFeed f = new MockChainlinkFeed("inv");
        SeriesTerms memory t = SeriesTerms({
            feed: address(f),
            strikeTime: t0 + 1 hours,
            observationInterval: interval,
            observationCount: count,
            kiBarrierBps: ki,
            acBarrierBps: ac,
            couponBpsPerPeriod: coupon
        });
        series.push(NoteSeries(factory.createSeries(t)));
        feeds.push(f);
    }

    function seriesCount() external view returns (uint256) {
        return series.length;
    }

    function actorCount() external view returns (uint256) {
        return actors.length;
    }

    function _pick(uint256 seed) internal view returns (NoteSeries s, uint256 i) {
        i = seed % series.length;
        s = series[i];
    }

    function _actor(uint256 seed) internal view returns (address) {
        return actors[seed % actors.length];
    }

    // --- actions ----------------------------------------------------------------------

    function mint(uint256 seriesSeed, uint256 actorSeed, uint256 amount) external {
        calls++;
        (NoteSeries s,) = _pick(seriesSeed);
        if (s.state().phase != Phase.Live) return;
        address a = _actor(actorSeed);
        amount = bound(amount, 1, 1e13);
        uint256 need = s.previewMint(amount);
        usdg.mint(a, need);
        vm.startPrank(a);
        usdg.approve(address(s), need);
        s.mint(amount, a);
        vm.stopPrank();
        roundingOps[address(s)]++;
    }

    function redeemPair(uint256 seriesSeed, uint256 actorSeed, uint256 amount) external {
        calls++;
        (NoteSeries s,) = _pick(seriesSeed);
        if (s.state().phase == Phase.Pending) return;
        address a = _actor(actorSeed);
        uint256 maxAmt = _min(IERC20(s.note()).balanceOf(a), IERC20(s.writer()).balanceOf(a));
        if (maxAmt == 0) return;
        amount = bound(amount, 1, maxAmt);
        vm.prank(a);
        s.redeemPair(amount, a);
        roundingOps[address(s)]++;
    }

    function transfer(uint256 seriesSeed, uint256 fromSeed, uint256 toSeed, bool isNote, uint256 amount) external {
        calls++;
        (NoteSeries s,) = _pick(seriesSeed);
        address from = _actor(fromSeed);
        IERC20 token = IERC20(isNote ? s.note() : s.writer());
        uint256 bal = token.balanceOf(from);
        if (bal == 0) return;
        amount = bound(amount, 1, bal);
        vm.prank(from);
        token.transfer(_actor(toSeed), amount);
    }

    /// Record the series' next fixing (strike, observation or maturity) at a random level.
    function recordFixing(uint256 seriesSeed, uint256 bps) external {
        calls++;
        (NoteSeries s, uint256 i) = _pick(seriesSeed);
        _record(s, feeds[i], bound(bps, 3000, 13000));
    }

    function _record(NoteSeries s, MockChainlinkFeed f, uint256 bps) internal {
        SeriesState memory st = s.state();
        if (st.phase == Phase.Settled) return;
        uint40 t = st.nextObservation;
        if (block.timestamp <= t) vm.warp(uint256(t) + 1);
        uint256 initial = 100e8;
        f.pushRoundAt(int256(initial * bps / 10_000), t);
        FixingsRecorder(address(s.recorder())).recordFixing(t, uint80(f.latestRound()));
    }

    /// Let time pass (up to 10 days: enough for fallback fixings to kick in).
    function warp(uint256 secs) external {
        calls++;
        vm.warp(block.timestamp + bound(secs, 0, 10 days));
    }

    function advance(uint256 seriesSeed) external {
        calls++;
        (NoteSeries s,) = _pick(seriesSeed);
        s.advance();
    }

    function redeem(uint256 seriesSeed, uint256 actorSeed, uint256 n, uint256 w) external {
        calls++;
        (NoteSeries s,) = _pick(seriesSeed);
        if (s.state().phase != Phase.Settled) return;
        address a = _actor(actorSeed);
        n = bound(n, 0, IERC20(s.note()).balanceOf(a));
        w = bound(w, 0, IERC20(s.writer()).balanceOf(a));
        if (n == 0 && w == 0) return;
        vm.prank(a);
        s.redeem(n, w, a);
        roundingOps[address(s)] += (n != 0 ? 1 : 0) + (w != 0 ? 1 : 0);
    }

    /// Clones of the real implementations that the factory didn't create must be
    /// inert: not initializable, not a series, and unable to mint real tokens.
    function rogue(uint256 seriesSeed) external {
        calls++;
        (NoteSeries s,) = _pick(seriesSeed);
        address fake = Clones.clone(factory.seriesImplementation());
        try NoteSeries(fake)
            .initialize(bytes32(uint256(1)), s.terms(), s.note(), s.writer(), address(1), address(usdg)) {
            rogueAccepted = true;
        } catch {}
        if (factory.isSeries(fake)) rogueAccepted = true;
        try SeriesToken(s.note()).mint(address(this), 1) {
            rogueAccepted = true;
        } catch {}
        try NoteSeries(address(s))
            .initialize(bytes32(0), s.terms(), address(this), address(this), address(1), address(usdg)) {
            rogueAccepted = true;
        } catch {}
    }

    // --- end of run: settle everything and redeem every holder ---------------------------

    function settleAndRedeemAll() external {
        for (uint256 i = 0; i < series.length; i++) {
            NoteSeries s = series[i];
            while (s.state().phase != Phase.Settled) {
                _record(s, feeds[i], 9000);
            }
            for (uint256 k = 0; k < actors.length; k++) {
                address a = actors[k];
                uint256 n = IERC20(s.note()).balanceOf(a);
                uint256 w = IERC20(s.writer()).balanceOf(a);
                if (n == 0 && w == 0) continue;
                vm.prank(a);
                s.redeem(n, w, a);
                roundingOps[address(s)] += (n != 0 ? 1 : 0) + (w != 0 ? 1 : 0);
            }
        }
    }

    function _min(uint256 a, uint256 b) internal pure returns (uint256) {
        return a < b ? a : b;
    }
}

contract CoreInvariantsTest is Test {
    CoreHandler handler;
    MockUSDG usdg;
    SeriesFactory factory;

    function setUp() public {
        vm.warp(1_790_000_000);
        usdg = new MockUSDG();
        factory = new SeriesFactory(address(usdg));
        handler = new CoreHandler(usdg, factory, uint40(block.timestamp));
        targetContract(address(handler));
        bytes4[] memory selectors = new bytes4[](9);
        selectors[0] = CoreHandler.mint.selector;
        selectors[1] = CoreHandler.redeemPair.selector;
        selectors[2] = CoreHandler.transfer.selector;
        selectors[3] = CoreHandler.recordFixing.selector;
        selectors[4] = CoreHandler.warp.selector;
        selectors[5] = CoreHandler.advance.selector;
        selectors[6] = CoreHandler.redeem.selector;
        selectors[7] = CoreHandler.rogue.selector;
        selectors[8] = CoreHandler.recordFixing.selector; // fixings twice as likely
        targetSelector(FuzzSelector({addr: address(handler), selectors: selectors}));
    }

    /// A series' USDG covers what its outstanding NOTE and WRITER can claim, at
    /// every step (claims in exact arithmetic, before the per-holder floors).
    function invariant_escrow_covers_claims() public view {
        for (uint256 i = 0; i < handler.seriesCount(); i++) {
            NoteSeries s = handler.series(i);
            uint256 bal = usdg.balanceOf(address(s)) * 1e6;
            uint256 noteSupply = IERC20(s.note()).totalSupply();
            uint256 writerSupply = IERC20(s.writer()).totalSupply();
            uint256 maxPayout = s.maxPayoutPerNote();
            SeriesState memory st = s.state();
            if (st.phase == Phase.Settled) {
                assertGe(bal, noteSupply * st.payoutPerNote + writerSupply * (maxPayout - st.payoutPerNote));
            } else {
                assertEq(noteSupply, writerSupply, "pairs only before settlement");
                assertGe(bal, noteSupply * maxPayout);
            }
        }
    }

    function invariant_no_foreign_clone_accepted() public view {
        assertFalse(handler.rogueAccepted());
        address[] memory all = factory.allSeries();
        assertEq(all.length, handler.seriesCount());
        for (uint256 i = 0; i < all.length; i++) {
            NoteSeries s = NoteSeries(all[i]);
            assertTrue(factory.isSeries(all[i]));
            assertEq(SeriesToken(s.note()).series(), all[i]);
            assertEq(SeriesToken(s.writer()).series(), all[i]);
            assertEq(factory.seriesOf(s.id()), all[i]);
        }
    }

    /// After every holder redeems everything, only rounding dust remains: less
    /// than one base unit per rounding operation (each mint, pair redemption and
    /// redeemed leg), and never a claim left unpaid.
    function afterInvariant() public {
        handler.settleAndRedeemAll();
        for (uint256 i = 0; i < handler.seriesCount(); i++) {
            NoteSeries s = handler.series(i);
            assertEq(IERC20(s.note()).totalSupply(), 0);
            assertEq(IERC20(s.writer()).totalSupply(), 0);
            assertLe(usdg.balanceOf(address(s)), handler.roundingOps(address(s)), "dust <= 1 unit per rounding op");
        }
    }
}
