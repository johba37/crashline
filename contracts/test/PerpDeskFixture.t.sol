// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {PerpBase} from "./PerpBase.t.sol";
import {MockPerpPricer} from "./mocks/MockPerpPricer.sol";
import {PerpDesk} from "../src/PerpDesk.sol";
import {PerpQuoter} from "../src/PerpQuoter.sol";
import {PerpSeries} from "../src/PerpSeries.sol";
import {IPerpDesk, IWeekendSource} from "../src/interfaces/IPerpDesk.sol";
import {PerpQuote} from "../src/interfaces/IPerpQuoter.sol";

/// A settable time-averaged price (feed decimals); 0 = the source has none and reverts.
contract MockWeekendSource is IWeekendSource {
    mapping(address feed => uint256) public price;

    error NoPrice();

    function set(address feed, uint256 price_) external {
        price[feed] = price_;
    }

    function averagePrice(address feed) external view returns (uint256) {
        if (price[feed] == 0) revert NoPrice();
        return price[feed];
    }
}

/// Shared Desk fixture: one listed, struck series of the P1 product a few
/// minutes after its first fixing (a Monday, 15:13 UTC), spot 90% of the
/// reference, the mock model at correction 0 (so the price is the closed
/// form: 7846 + 2350 = 10196 bps per unit of notional at vol 55%), earnings
/// in 30 days, 1,000,000 USDG of LP capital and a risk budget of the whole vault.
abstract contract PerpDeskFixture is PerpBase {
    uint16 internal constant VOL = 5500;
    uint128 internal constant CAP = 100_000e6;
    uint256 internal constant LP_CAPITAL = 1_000_000e6;
    uint256 internal constant PAIR_BPS = 12_350; // (1e6 + RESERVE) / 100
    uint256 internal constant NOTE_BPS = 10_196; // the closed form at spot 90%, vol 55%, plus the reserve

    PerpQuoter internal quoter;
    MockPerpPricer internal pricer;
    PerpDesk internal desk;
    PerpSeries internal s;
    IERC20 internal noteT;
    IERC20 internal writerT;
    uint40 internal first;
    uint40 internal earnings;
    address internal lp = makeAddr("lp");
    address internal integrator = makeAddr("integrator");

    function setUp() public virtual override {
        super.setUp();
        quoter = new PerpQuoter(26 hours);
        pricer = new MockPerpPricer();
        desk = new PerpDesk(IERC20(address(usdg)), factory, quoter, address(this), 1 hours);
        first = T0 + 1 hours;
        earnings = T0 + 30 days;
        s = _create(_terms(first));
        noteT = IERC20(s.note());
        writerT = IERC20(s.writer());
        _fixAt(s, first, int256(uint256(INITIAL)));
        _spot(9000); // fresh round, one second into week 1
        desk.listSeries(address(s), pricer, VOL, earnings, CAP);
        desk.setRiskBudget(address(feed), 10_000); // the whole vault may be at risk in this stock
        _deposit(lp, LP_CAPITAL);
    }

    // --- helpers ------------------------------------------------------------------

    function _spot(uint256 bps) internal {
        feed.pushRoundAt(_price(bps), uint40(block.timestamp));
    }

    /// Move time forward and push a fresh round.
    function _later(uint256 secs, uint256 bps) internal {
        vm.warp(block.timestamp + secs);
        _spot(bps);
    }

    /// Record fixing n at `bps` of INITIAL, one minute after its time, with a fresh round at the same level.
    function _fixing(uint256 n, uint256 bps) internal {
        _fixAt(s, _fixingTime(s, n), _price(bps));
        vm.warp(uint256(_fixingTime(s, n)) + 60);
        _spot(bps);
    }

    function _mid() internal view returns (uint256) {
        return quoter.quote(s, pricer, VOL, earnings).priceBps;
    }

    function _deposit(address who, uint256 amount) internal returns (uint256 shares) {
        usdg.mint(who, amount);
        vm.startPrank(who);
        usdg.approve(address(desk), amount);
        shares = desk.deposit(amount, who);
        vm.stopPrank();
    }

    function _quoteOf(IPerpDesk.Side side, uint256 n, uint16 feeBps) internal view returns (uint256 paid) {
        (paid,) = desk.quote(address(s), side, n, feeBps);
    }

    function _buy(address who, uint256 n, uint16 feeBps) internal returns (uint256 cost) {
        uint256 quoted = _quoteOf(IPerpDesk.Side.BuyNote, n, feeBps);
        usdg.mint(who, quoted);
        vm.startPrank(who);
        usdg.approve(address(desk), quoted);
        cost = desk.buy(address(s), n, quoted, feeBps, integrator, who);
        vm.stopPrank();
    }

    function _sell(address who, uint256 n, uint16 feeBps) internal returns (uint256 proceeds) {
        vm.startPrank(who);
        noteT.approve(address(desk), n);
        proceeds = desk.sell(address(s), n, 0, feeBps, integrator, who);
        vm.stopPrank();
    }

    function _buyCover(address who, uint256 n, uint16 feeBps) internal returns (uint256 cost) {
        uint256 quoted = _quoteOf(IPerpDesk.Side.BuyCover, n, feeBps);
        usdg.mint(who, quoted);
        vm.startPrank(who);
        usdg.approve(address(desk), quoted);
        cost = desk.buyCover(address(s), n, quoted, feeBps, integrator, who);
        vm.stopPrank();
    }

    function _sellCover(address who, uint256 n, uint16 feeBps) internal returns (uint256 proceeds) {
        vm.startPrank(who);
        writerT.approve(address(desk), n);
        proceeds = desk.sellCover(address(s), n, 0, feeBps, integrator, who);
        vm.stopPrank();
    }
}
