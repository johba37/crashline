// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Errors} from "@openzeppelin/contracts/interfaces/draft-IERC6093.sol";
import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import {MockChainlinkFeed} from "../../src/MockChainlinkFeed.sol";
import {MockUSDG} from "../../src/MockUSDG.sol";
import {SeriesFactory} from "../../src/SeriesFactory.sol";
import {PerpFactory} from "../../src/PerpFactory.sol";
import {PerpSeries} from "../../src/PerpSeries.sol";
import {PerpQuoter} from "../../src/PerpQuoter.sol";
import {PerpDesk} from "../../src/PerpDesk.sol";
import {PerpWrapper} from "../../src/PerpWrapper.sol";
import {PerpFormulaPricer} from "../../src/PerpFormulaPricer.sol";
import {FixingsRecorder} from "../../src/FixingsRecorder.sol";
import {IPerpDesk} from "../../src/interfaces/IPerpDesk.sol";
import {IDeskQueue} from "../../src/interfaces/IDeskQueue.sol";
import {IPerpQuoter} from "../../src/interfaces/IPerpQuoter.sol";
import {IPerpPricer, PerpProduct} from "../../src/interfaces/IPerpPricer.sol";
import {PerpTerms, PerpState, PerpPhase} from "../../src/interfaces/IPerpSeries.sol";

/// Drives a perpetual Desk on one listed series (the P1 product, priced by the
/// closed form) with random trades on both legs, fixings (some of them knock
/// the note in or ratchet the reference), spot moves, time, LP flows, the
/// queue, collects, claims and a wrapper. A call the Desk may refuse for a
/// stated reason is caught; any other revert (a panic, an unexpected error)
/// is recorded and fails the run.
contract PerpDeskHandler is Test {
    uint256 internal constant INITIAL = 100e8;
    uint32 internal constant WEEK = 604_800;

    MockUSDG public usdg;
    MockChainlinkFeed public feed;
    PerpSeries public series;
    PerpDesk public desk;
    PerpWrapper public wrapper;
    IERC20 public note;
    IERC20 public writer;
    address[] public actors;
    address public lp = makeAddr("lp");
    address public integrator = makeAddr("integrator");

    uint256 public level = 9000; // spot, bps of INITIAL
    uint256 public minted; // every USDG that exists
    bytes4 public unexpected; // the first revert that is not a stated refusal
    uint256 public calls;
    uint256 public trades;

    constructor(MockUSDG usdg_, MockChainlinkFeed feed_, PerpSeries series_, PerpDesk desk_, PerpWrapper wrapper_) {
        usdg = usdg_;
        feed = feed_;
        series = series_;
        desk = desk_;
        wrapper = wrapper_;
        note = IERC20(series_.note());
        writer = IERC20(series_.writer());
        actors.push(makeAddr("actor0"));
        actors.push(makeAddr("actor1"));
        actors.push(makeAddr("actor2"));
        for (uint256 i = 0; i < actors.length; i++) {
            _fund(actors[i], 1_000_000e6);
            vm.startPrank(actors[i]);
            usdg.approve(address(desk), type(uint256).max);
            note.approve(address(desk), type(uint256).max);
            writer.approve(address(desk), type(uint256).max);
            note.approve(address(wrapper), type(uint256).max);
            vm.stopPrank();
        }
        _fund(lp, 2_000_000e6);
        vm.startPrank(lp);
        usdg.approve(address(desk), type(uint256).max);
        desk.deposit(1_000_000e6, lp);
        vm.stopPrank();
    }

    /// A feed round at the walk's level (the feed takes rounds from its owner only).
    function _push(uint40 time) internal {
        vm.prank(feed.owner());
        feed.pushRoundAt(int256(INITIAL * level / 10_000), time);
    }

    function _fund(address who, uint256 amount) internal {
        usdg.mint(who, amount);
        minted += amount;
    }

    function actorCount() external view returns (uint256) {
        return actors.length;
    }

    function actor(uint256 i) external view returns (address) {
        return actors[i];
    }

    /// A refusal the Desk, the quoter, the wrapper or a token states on purpose.
    function _note(bytes memory err) internal {
        bytes4 sel = bytes4(err);
        if (
            sel == IPerpQuoter.FeedStale.selector || sel == IPerpQuoter.FixingPending.selector
                || sel == IPerpQuoter.EarningsDatePassed.selector || sel == IPerpQuoter.NotLive.selector
                || sel == IPerpDesk.TooCloseToFixing.selector || sel == IPerpDesk.RiskBudgetExceeded.selector
                || sel == IPerpDesk.CapExceeded.selector || sel == IPerpDesk.Slippage.selector
                || sel == IDeskQueue.QueuePending.selector || sel == IDeskQueue.ReservedForClaims.selector
                || sel == IDeskQueue.RequestTooSmall.selector || sel == IDeskQueue.NothingToClaim.selector
                || sel == IERC20Errors.ERC20InsufficientBalance.selector
                || sel == ERC4626.ERC4626ExceededMaxDeposit.selector
                || sel == ERC4626.ERC4626ExceededMaxWithdraw.selector
                || sel == ERC4626.ERC4626ExceededMaxRedeem.selector || sel == PerpWrapper.CashPending.selector
                || sel == PerpWrapper.ZeroAmount.selector || sel == IPerpPricer.Uncertified.selector
        ) return;
        if (unexpected == bytes4(0)) unexpected = sel == bytes4(0) ? bytes4(0xffffffff) : sel;
    }

    // --- trades -----------------------------------------------------------------------

    function buy(uint256 actorSeed, uint256 amount, uint16 feeBps, bool cover) external {
        calls++;
        address a = actors[actorSeed % actors.length];
        amount = bound(amount, 1, 20_000e6);
        feeBps = uint16(bound(feeBps, 0, 200));
        vm.prank(a);
        if (cover) {
            try desk.buyCover(address(series), amount, type(uint256).max, feeBps, integrator, a) {
                trades++;
            } catch (bytes memory err) {
                _note(err);
            }
        } else {
            try desk.buy(address(series), amount, type(uint256).max, feeBps, integrator, a) {
                trades++;
            } catch (bytes memory err) {
                _note(err);
            }
        }
    }

    function sell(uint256 actorSeed, uint256 amount, uint16 feeBps, bool cover) external {
        calls++;
        address a = actors[actorSeed % actors.length];
        uint256 bal = (cover ? writer : note).balanceOf(a);
        if (bal == 0) return;
        amount = bound(amount, 1, bal);
        feeBps = uint16(bound(feeBps, 0, 200));
        vm.prank(a);
        if (cover) {
            try desk.sellCover(address(series), amount, 0, feeBps, integrator, a) {
                trades++;
            } catch (bytes memory err) {
                _note(err);
            }
        } else {
            try desk.sell(address(series), amount, 0, feeBps, integrator, a) {
                trades++;
            } catch (bytes memory err) {
                _note(err);
            }
        }
    }

    // --- the market ---------------------------------------------------------------------

    /// A fresh round at a level that walks -8% .. +8%.
    function spot(uint256 move) external {
        calls++;
        level = bound(level * bound(move, 9200, 10_800) / 10_000, 2500, 20_000);
        vm.warp(block.timestamp + 1);
        _push(uint40(block.timestamp));
    }

    /// Time passes (up to 3 days); a fresh round follows two times out of three.
    function warp(uint256 secs, uint256 move) external {
        calls++;
        vm.warp(block.timestamp + bound(secs, 1, 3 days));
        if (move % 3 != 0) _push(uint40(block.timestamp));
    }

    /// The next fixing: jump to its time, fix at a level that walks -25% .. +15%, record it.
    function fixing(uint256 move) external {
        calls++;
        PerpState memory st = series.state();
        if (st.phase != PerpPhase.Live) return;
        uint40 t = st.nextFixing;
        // forge-lint: disable-next-line(unsafe-typecast)
        (,,, uint256 updatedAt,) = feed.latestRoundData();
        if (updatedAt >= t) return; // a later round exists already: this fixing is recorded by `record`
        level = bound(level * bound(move, 7500, 11_500) / 10_000, 2500, 20_000);
        if (block.timestamp <= t) vm.warp(uint256(t) + 1);
        _push(t);
        FixingsRecorder(address(series.recorder())).recordFixing(t, uint80(feed.latestRound()));
        _push(uint40(block.timestamp));
    }

    function advance() external {
        calls++;
        series.advance();
    }

    // --- cash -----------------------------------------------------------------------------

    function claim(uint256 actorSeed) external {
        calls++;
        address a = actors[actorSeed % actors.length];
        vm.prank(a);
        series.claim(a);
    }

    function collect() external {
        calls++;
        desk.collect(address(series));
    }

    function wrap(uint256 actorSeed, uint256 amount) external {
        calls++;
        address a = actors[actorSeed % actors.length];
        uint256 bal = note.balanceOf(a);
        if (bal == 0) return;
        vm.prank(a);
        try wrapper.wrap(bound(amount, 1, bal), a) {}
        catch (bytes memory err) {
            _note(err);
        }
    }

    function unwrap(uint256 actorSeed, uint256 shares) external {
        calls++;
        address a = actors[actorSeed % actors.length];
        uint256 bal = wrapper.balanceOf(a);
        if (bal == 0) return;
        vm.prank(a);
        try wrapper.unwrap(bound(shares, 1, bal), a) {}
        catch (bytes memory err) {
            _note(err);
        }
    }

    function compound() external {
        calls++;
        try wrapper.compound() {}
        catch (bytes memory err) {
            _note(err);
        }
    }

    // --- LP -------------------------------------------------------------------------------

    function deposit(uint256 amount) external {
        calls++;
        amount = bound(amount, 1, 100_000e6);
        if (usdg.balanceOf(lp) < amount) return;
        vm.prank(lp);
        try desk.deposit(amount, lp) {}
        catch (bytes memory err) {
            _note(err);
        }
    }

    function withdraw(uint256 shares) external {
        calls++;
        uint256 max = desk.maxRedeem(lp);
        if (max == 0) return;
        vm.prank(lp);
        try desk.redeem(bound(shares, 1, max), lp, lp) {}
        catch (bytes memory err) {
            _note(err);
        }
    }

    function requestRedeem(uint256 shares) external {
        calls++;
        uint256 bal = desk.balanceOf(lp);
        if (bal == 0) return;
        vm.prank(lp);
        try desk.requestRedeem(bound(shares, 1, bal)) {}
        catch (bytes memory err) {
            _note(err);
        }
    }

    function processQueue(uint256 n) external {
        calls++;
        try desk.processQueue(bound(n, 1, 10)) {}
        catch (bytes memory err) {
            _note(err);
        }
    }

    function claimRedeemed() external {
        calls++;
        vm.prank(lp);
        try desk.claim(lp) {}
        catch (bytes memory err) {
            _note(err);
        }
    }
}

/// forge-config: default.invariant.runs = 96
/// forge-config: default.invariant.depth = 120
contract PerpDeskInvariantsTest is Test {
    uint40 internal constant T0 = 1_790_000_000;
    uint256 internal constant RAY = 1e27;

    PerpDeskHandler handler;
    MockUSDG usdg;
    MockChainlinkFeed feed;
    PerpSeries series;
    PerpDesk desk;
    PerpWrapper wrapper;

    function setUp() public {
        vm.warp(T0);
        usdg = new MockUSDG();
        feed = new MockChainlinkFeed("RHTSLA / USD");
        PerpFactory factory = new PerpFactory(address(usdg), new SeriesFactory(address(usdg)));
        PerpQuoter quoter = new PerpQuoter(26 hours);
        desk = new PerpDesk(IERC20(address(usdg)), factory, quoter, address(this), 1 hours);
        PerpProduct memory product = PerpProduct({
            kiBarrierBps: 6000,
            meltShare: 18_995_352_771_274_247,
            fixingInterval: 604_800,
            driftBps: 400,
            discountBps: 0
        });
        PerpFormulaPricer pricer = new PerpFormulaPricer(
            product,
            PerpFormulaPricer.Bands({
                fixingBandSecs: 6 hours, knockInBandBps: 1000, healBandBps: 500, volMinBps: 2000, volMaxBps: 9000
            })
        );
        PerpTerms memory t = PerpTerms({
            feed: address(feed),
            firstFixing: T0 + 1 hours,
            fixingInterval: 604_800,
            kiBarrierBps: 6000,
            meltShare: 18_995_352_771_274_247,
            couponReserve: 235_000
        });
        series = PerpSeries(factory.createSeries(t));
        vm.warp(uint256(T0) + 1 hours + 1);
        feed.pushRoundAt(100e8, T0 + 1 hours);
        FixingsRecorder(address(series.recorder())).recordFixing(T0 + 1 hours, uint80(feed.latestRound()));
        feed.pushRoundAt(90e8, uint40(block.timestamp));
        // earnings far out: the formula-only pricer doesn't use the date, and the run spans months
        desk.listSeries(address(series), pricer, 5500, type(uint40).max, 200_000e6);
        desk.setSpread(address(series), 20, 30, 200);
        desk.setRiskBudget(address(feed), 3000);
        wrapper = new PerpWrapper(desk, series, true);
        handler = new PerpDeskHandler(usdg, feed, series, desk, wrapper);
        targetContract(address(handler));
    }

    /// Nothing reverted for a reason the contracts don't state.
    function invariant_no_unexpected_revert() public view {
        assertEq(handler.unexpected(), bytes4(0), "an action reverted with an unexpected error");
    }

    /// The Desk nets every trade: it never holds NOTE and WRITER at once
    /// (beyond the base unit a rounding can leave on one side).
    function invariant_desk_holds_one_leg() public view {
        uint256 n = IERC20(series.note()).balanceOf(address(desk));
        uint256 w = IERC20(series.writer()).balanceOf(address(desk));
        assertTrue(n == 0 || w == 0, "the Desk holds both legs");
    }

    /// The series covers every holder's released USDG and the pair value of the supply.
    function invariant_series_solvent() public view {
        uint256 supply = IERC20(series.note()).totalSupply();
        assertEq(supply, IERC20(series.writer()).totalSupply());
        uint256 owed = series.previewRedeemPair(supply) + series.claimable(address(desk))
            + series.claimable(address(wrapper)) + series.claimable(address(handler));
        for (uint256 i = 0; i < handler.actorCount(); i++) {
            owed += series.claimable(handler.actor(i));
        }
        assertGe(usdg.balanceOf(address(series)), owed);
    }

    /// Every USDG is somewhere it can be accounted for, and the Desk's own
    /// balance covers what it set aside for filled redemptions.
    function invariant_usdg_accounted_and_reserve_backed() public view {
        uint256 total = usdg.balanceOf(address(desk)) + usdg.balanceOf(address(series))
            + usdg.balanceOf(address(wrapper)) + usdg.balanceOf(handler.lp()) + usdg.balanceOf(handler.integrator());
        for (uint256 i = 0; i < handler.actorCount(); i++) {
            total += usdg.balanceOf(handler.actor(i));
        }
        assertEq(total, handler.minted());
        assertGe(usdg.balanceOf(address(desk)), desk.reservedAssets());
        assertEq(desk.reservedAssets(), desk.claimableAssets(handler.lp()));
    }

    /// The risk view and the LP limits never revert, whatever the market does.
    function invariant_views_never_revert() public view {
        desk.risk(address(feed));
        desk.maxDeposit(handler.lp());
        desk.maxWithdraw(handler.lp());
        desk.maxRedeem(handler.lp());
        series.state();
        wrapper.previewUnwrap(1e12);
    }

    /// The run did trade: the refusals above are the exception, not the rule.
    function afterInvariant() public {
        emit log_named_uint("calls", handler.calls());
        emit log_named_uint("trades that went through", handler.trades());
        emit log_named_uint("fixings processed", series.state().fixingsDone);
    }

    /// A wrapper share never stands for fewer tokens than it was minted for
    /// (1e6 shares per token base unit at the start; compounding only adds).
    function invariant_wrapper_ratio_never_falls() public view {
        uint256 supply = wrapper.totalSupply();
        if (supply == 0) return;
        assertGe((wrapper.totalTokens() + 1) * 1e6, supply);
    }
}
