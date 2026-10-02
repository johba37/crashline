// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol";
import {MockChainlinkFeed} from "../../src/MockChainlinkFeed.sol";
import {MockUSDG} from "../../src/MockUSDG.sol";
import {SeriesFactory} from "../../src/SeriesFactory.sol";
import {PerpFactory} from "../../src/PerpFactory.sol";
import {PerpSeries} from "../../src/PerpSeries.sol";
import {PerpToken} from "../../src/PerpToken.sol";
import {FixingsRecorder} from "../../src/FixingsRecorder.sol";
import {PerpTerms, PerpState, PerpPhase} from "../../src/interfaces/IPerpSeries.sol";

/// Drives several perpetual series (different feeds, intervals, barriers,
/// melt shares, reserves) with random mint / redeemPair / transfer / claim /
/// recordFixing / warp / advance, plus attempts to smuggle in clones the
/// factory didn't create. Fixings are sometimes left unrecorded, so missed
/// fixings and closed series occur.
contract PerpHandler is Test {
    MockUSDG public usdg;
    PerpFactory public factory;
    PerpSeries[] public series;
    MockChainlinkFeed[] public feeds;
    address[] public actors;

    mapping(address => uint256) public lockedIn; // USDG paid into a series
    mapping(address => uint256) public paidOut; // USDG a series paid
    mapping(address => uint256) public roundingOps; // each can leave < 1 base unit in the escrow
    mapping(address => uint256) internal level; // the walk's level in bps of $100
    bool public rogueAccepted;
    uint256 public calls;

    constructor(MockUSDG usdg_, PerpFactory factory_, uint40 t0) {
        usdg = usdg_;
        factory = factory_;
        actors.push(makeAddr("actor0"));
        actors.push(makeAddr("actor1"));
        actors.push(makeAddr("actor2"));
        // (interval, ki, melt share, coupon reserve)
        _add(t0, 604_800, 6000, 18_995_352_771_274_247, 235_000);
        _add(t0, 1 days, 8000, 2_735_976_403_140_714, 95_000);
        _add(t0, 1 hours, 9000, 2.5e17, 5e6);
        _add(t0, 604_800, 10_000, 1, 0);
    }

    function _add(uint40 t0, uint32 interval, uint16 ki, uint64 melt, uint64 reserve) internal {
        MockChainlinkFeed f = new MockChainlinkFeed("inv");
        PerpTerms memory t = PerpTerms({
            feed: address(f),
            firstFixing: t0 + 1 hours,
            fixingInterval: interval,
            kiBarrierBps: ki,
            meltShare: melt,
            couponReserve: reserve
        });
        PerpSeries s = PerpSeries(factory.createSeries(t));
        series.push(s);
        feeds.push(f);
        level[address(s)] = 10_000;
    }

    function seriesCount() external view returns (uint256) {
        return series.length;
    }

    function actorCount() external view returns (uint256) {
        return actors.length;
    }

    function actor(uint256 i) external view returns (address) {
        return actors[i];
    }

    function _pick(uint256 seed) internal view returns (PerpSeries s, uint256 i) {
        i = seed % series.length;
        s = series[i];
    }

    function _actor(uint256 seed) internal view returns (address) {
        return actors[seed % actors.length];
    }

    // --- actions ----------------------------------------------------------------------

    function mint(uint256 seriesSeed, uint256 actorSeed, uint256 toSeed, uint256 amount) external {
        calls++;
        (PerpSeries s,) = _pick(seriesSeed);
        if (s.state().phase != PerpPhase.Live) return;
        address a = _actor(actorSeed);
        amount = bound(amount, 1, 1e13);
        uint256 need = s.previewMint(amount);
        usdg.mint(a, need);
        vm.startPrank(a);
        usdg.approve(address(s), need);
        s.mint(amount, _actor(toSeed));
        vm.stopPrank();
        lockedIn[address(s)] += need;
        roundingOps[address(s)] += 3; // the mint, and the receiver's two legs settled
    }

    function redeemPair(uint256 seriesSeed, uint256 actorSeed, uint256 amount) external {
        calls++;
        (PerpSeries s,) = _pick(seriesSeed);
        if (s.state().phase == PerpPhase.Pending) return;
        address a = _actor(actorSeed);
        uint256 maxAmt = _min(IERC20(s.note()).balanceOf(a), IERC20(s.writer()).balanceOf(a));
        if (maxAmt == 0) return;
        amount = bound(amount, 1, maxAmt);
        vm.prank(a);
        paidOut[address(s)] += s.redeemPair(amount, a);
        roundingOps[address(s)] += 3;
    }

    function transfer(uint256 seriesSeed, uint256 fromSeed, uint256 toSeed, bool isNote, uint256 amount) external {
        calls++;
        (PerpSeries s,) = _pick(seriesSeed);
        address from = _actor(fromSeed);
        IERC20 token = IERC20(isNote ? s.note() : s.writer());
        uint256 bal = token.balanceOf(from);
        if (bal == 0) return;
        amount = bound(amount, 1, bal);
        vm.prank(from);
        token.transfer(_actor(toSeed), amount);
        roundingOps[address(s)] += 2;
    }

    function claim(uint256 seriesSeed, uint256 actorSeed, uint256 toSeed) external {
        calls++;
        (PerpSeries s,) = _pick(seriesSeed);
        address a = _actor(actorSeed);
        uint256 expected = s.claimable(a);
        vm.prank(a);
        uint256 got = s.claim(_actor(toSeed));
        assertEq(got, expected, "claim pays what the view promised");
        paidOut[address(s)] += got;
        roundingOps[address(s)] += 2;
    }

    /// Record the series' next fixing at a level that walks: -30% .. +35% per step.
    function recordFixing(uint256 seriesSeed, uint256 move) external {
        calls++;
        (PerpSeries s, uint256 i) = _pick(seriesSeed);
        uint256 l = level[address(s)] * bound(move, 7000, 13500) / 10_000;
        level[address(s)] = l = bound(l, 50, 500_000);
        _record(s, feeds[i], l);
    }

    function _record(PerpSeries s, MockChainlinkFeed f, uint256 bps) internal {
        PerpState memory st = s.state();
        if (st.phase == PerpPhase.Closed) return;
        uint40 t = st.nextFixing;
        if (block.timestamp <= t) vm.warp(uint256(t) + 1);
        uint256 initial = 100e8;
        f.pushRoundAt(int256(initial * bps / 10_000), t);
        FixingsRecorder(address(s.recorder())).recordFixing(t, uint80(f.latestRound()));
    }

    /// Let time pass (up to 10 days: enough for missed fixings, and for the hourly series to close).
    function warp(uint256 secs) external {
        calls++;
        vm.warp(block.timestamp + bound(secs, 0, 10 days));
    }

    function advance(uint256 seriesSeed, uint256 steps) external {
        calls++;
        (PerpSeries s,) = _pick(seriesSeed);
        if (steps % 3 == 0) s.advance();
        else s.advanceBy(steps % 5);
    }

    /// Clones of the real implementations that the factory didn't create must be
    /// inert: not initializable, not a series, and unable to mint real tokens
    /// or to checkpoint a holder.
    function rogue(uint256 seriesSeed) external {
        calls++;
        (PerpSeries s,) = _pick(seriesSeed);
        address fake = Clones.clone(factory.seriesImplementation());
        try PerpSeries(fake)
            .initialize(bytes32(uint256(1)), s.terms(), s.note(), s.writer(), address(1), address(usdg)) {
            rogueAccepted = true;
        } catch {}
        if (factory.isSeries(fake)) rogueAccepted = true;
        try PerpToken(s.note()).mint(address(this), 1) {
            rogueAccepted = true;
        } catch {}
        try s.checkpoint(actors[0], 1e18, actors[1], 0) {
            rogueAccepted = true;
        } catch {}
        try PerpSeries(address(s))
            .initialize(bytes32(0), s.terms(), address(this), address(this), address(1), address(usdg)) {
            rogueAccepted = true;
        } catch {}
    }

    // --- end of run: every holder claims, then all pairs are redeemed -------------------

    function claimAndRedeemAll() external {
        for (uint256 i = 0; i < series.length; i++) {
            PerpSeries s = series[i];
            if (s.state().phase == PerpPhase.Pending) continue;
            IERC20 note = IERC20(s.note());
            IERC20 writer = IERC20(s.writer());
            address sink = actors[0];
            for (uint256 k = 0; k < actors.length; k++) {
                address a = actors[k];
                vm.startPrank(a);
                paidOut[address(s)] += s.claim(a);
                if (k != 0) {
                    note.transfer(sink, note.balanceOf(a));
                    writer.transfer(sink, writer.balanceOf(a));
                }
                vm.stopPrank();
                roundingOps[address(s)] += 4;
            }
            uint256 supply = note.totalSupply();
            assertEq(note.balanceOf(sink), supply);
            assertEq(writer.balanceOf(sink), supply);
            if (supply == 0) continue;
            vm.startPrank(sink);
            paidOut[address(s)] += s.redeemPair(supply, sink);
            paidOut[address(s)] += s.claim(sink);
            vm.stopPrank();
            roundingOps[address(s)] += 3;
        }
    }

    function _min(uint256 a, uint256 b) internal pure returns (uint256) {
        return a < b ? a : b;
    }
}

/// forge-config: default.invariant.runs = 256
contract PerpInvariantsTest is Test {
    PerpHandler handler;
    MockUSDG usdg;
    PerpFactory factory;

    function setUp() public {
        vm.warp(1_790_000_000);
        usdg = new MockUSDG();
        factory = new PerpFactory(address(usdg), new SeriesFactory(address(usdg)));
        handler = new PerpHandler(usdg, factory, uint40(block.timestamp));
        targetContract(address(handler));
        bytes4[] memory selectors = new bytes4[](9);
        selectors[0] = PerpHandler.mint.selector;
        selectors[1] = PerpHandler.redeemPair.selector;
        selectors[2] = PerpHandler.transfer.selector;
        selectors[3] = PerpHandler.recordFixing.selector;
        selectors[4] = PerpHandler.warp.selector;
        selectors[5] = PerpHandler.advance.selector;
        selectors[6] = PerpHandler.claim.selector;
        selectors[7] = PerpHandler.rogue.selector;
        selectors[8] = PerpHandler.recordFixing.selector; // fixings twice as likely
        targetSelector(FuzzSelector({addr: address(handler), selectors: selectors}));
    }

    /// A series' USDG covers what every holder can claim plus the pair value
    /// of the supply, at every step; NOTE and WRITER supplies stay equal; and
    /// the escrow is exactly what came in minus what went out.
    function invariant_escrow_covers_claims_and_pairs() public view {
        for (uint256 i = 0; i < handler.seriesCount(); i++) {
            PerpSeries s = handler.series(i);
            uint256 supply = IERC20(s.note()).totalSupply();
            assertEq(supply, IERC20(s.writer()).totalSupply(), "pairs only");
            uint256 owed = s.previewRedeemPair(supply);
            for (uint256 k = 0; k < handler.actorCount(); k++) {
                owed += s.claimable(handler.actor(k));
            }
            uint256 bal = usdg.balanceOf(address(s));
            assertGe(bal, owed, "escrow < claims + pair value");
            assertEq(bal, handler.lockedIn(address(s)) - handler.paidOut(address(s)));
        }
    }

    function invariant_no_foreign_clone_accepted() public view {
        assertFalse(handler.rogueAccepted());
        address[] memory all = factory.allSeries();
        assertEq(all.length, handler.seriesCount());
        for (uint256 i = 0; i < all.length; i++) {
            PerpSeries s = PerpSeries(all[i]);
            assertTrue(factory.isSeries(all[i]));
            assertEq(PerpToken(s.note()).series(), all[i]);
            assertEq(PerpToken(s.writer()).series(), all[i]);
            assertEq(factory.seriesOf(s.id()), all[i]);
        }
    }

    /// The notional per token only falls, the indexes only rise, and together
    /// they never exceed what a token's melted notional was worth.
    function invariant_indexes_bounded_by_melted_pair_value() public view {
        for (uint256 i = 0; i < handler.seriesCount(); i++) {
            PerpSeries s = handler.series(i);
            PerpState memory st = s.state();
            if (st.phase == PerpPhase.Pending) continue;
            assertLe(st.notionalPerToken, 1e27);
            uint256 melted = 1e27 - st.notionalPerToken;
            assertLe(st.noteIndex + st.writerIndex, melted * s.pairValuePerNotional() / 1e6);
            if (st.phase == PerpPhase.Closed) assertEq(st.notionalPerToken, 0);
        }
    }

    /// After every holder claims and all pairs are redeemed, only rounding
    /// dust remains: less than one base unit per rounding operation, and
    /// never a claim left unpaid.
    function afterInvariant() public {
        handler.claimAndRedeemAll();
        for (uint256 i = 0; i < handler.seriesCount(); i++) {
            PerpSeries s = handler.series(i);
            assertEq(IERC20(s.note()).totalSupply(), 0);
            assertEq(IERC20(s.writer()).totalSupply(), 0);
            for (uint256 k = 0; k < handler.actorCount(); k++) {
                assertEq(s.claimable(handler.actor(k)), 0);
            }
            assertLe(usdg.balanceOf(address(s)), handler.roundingOps(address(s)), "dust <= 1 unit per rounding op");
        }
    }
}
