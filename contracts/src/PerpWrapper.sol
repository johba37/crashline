// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {IPerpDesk} from "./interfaces/IPerpDesk.sol";
import {IPerpSeries} from "./interfaces/IPerpSeries.sol";

/// L3: auto-compounding wrapper of one leg (NOTE or WRITER) of one perpetual
/// series, for integrations that hold a token and never claim (an AMM pool, a
/// lending market). A perpetual series pays its holders in USDG that must be
/// claimed (IPerpSeries); held by a contract that never claims, that USDG is
/// stranded. The wrapper claims it and buys more of the same leg at the Desk,
/// so a share stands for a growing number of tokens and carries all of its
/// value inside itself, as wstETH does for stETH. Shares are a plain ERC-20:
/// no hooks, no claims, no rebasing.
///
///   wrap(amount)     the caller's tokens in, shares out at the current ratio
///   unwrap(shares)   shares in; the tokens they stand for out, plus their
///                    part of any USDG not yet reinvested
///   compound()       anyone: claim the released USDG and buy tokens with it
///                    at the Desk's ask (no integrator fee)
///   exitTokens(shares)  the tokens alone, touching no USDG: the way out while
///                    USDG is paused or the wrapper's address is frozen
///
/// Fairness. USDG that is claimed but not yet reinvested belongs to the
/// current shares. `wrap` therefore compounds first and refuses
/// (CashPending) while more than DUST_BPS of the holding's notional is still
/// in USDG, which happens only while the Desk can't quote (a weekend, the
/// band before a fixing, a pending fixing) or its cap or risk budget is
/// full. `unwrap` pays the leaver its part of that USDG and works whenever
/// USDG moves; `exitTokens` works even when it doesn't, and leaves the
/// leaver's part of the uninvested USDG to the remaining shares.
/// The wrapper never buys at the Desk's weekend price (a wider spread).
///
/// Inflation: virtual shares (1e6 per token base unit, as in the Desk), so a
/// donation can't round a later depositor's shares away.
contract PerpWrapper is ERC20, ReentrancyGuard {
    using SafeERC20 for IERC20;

    uint256 internal constant BPS = 10_000;
    uint256 internal constant RAY = 1e27;
    uint256 internal constant VIRTUAL_SHARES = 1e6; // per virtual token base unit
    uint256 public constant DUST_BPS = 1; // of the holding's notional: USDG a wrap may leave uninvested
    uint256 public constant DUST_UNITS = 10; // and never less than this many base units: too little to buy a token unit

    IPerpDesk public immutable desk;
    IPerpSeries public immutable series;
    IERC20 public immutable token; // the wrapped leg
    IERC20 public immutable usdg;
    bool public immutable isNote;
    uint8 internal immutable _tokenDecimals;
    string internal _symbol;

    event Wrapped(address indexed caller, address indexed to, uint256 amount, uint256 shares);
    event Unwrapped(address indexed caller, address indexed to, uint256 shares, uint256 amount, uint256 cash);
    event Compounded(uint256 cashIn, uint256 tokensBought);

    error NotFactorySeries(address series);
    error ZeroAmount();
    error CashPending(uint256 cash); // the Desk can't reinvest right now: wrap again later, or call compound()
    error OnlySelf();

    constructor(IPerpDesk desk_, IPerpSeries series_, bool isNote_)
        ERC20(isNote_ ? "Wrapped Perpetual NOTE" : "Wrapped Perpetual WRITER", "")
    {
        if (!desk_.factory().isSeries(address(series_))) revert NotFactorySeries(address(series_));
        desk = desk_;
        series = series_;
        isNote = isNote_;
        address leg = isNote_ ? series_.note() : series_.writer();
        token = IERC20(leg);
        usdg = IERC20(series_.collateral());
        _tokenDecimals = ERC20(leg).decimals();
        _symbol = string.concat("w", ERC20(leg).symbol());
    }

    /// "w" + the wrapped token's symbol.
    function symbol() public view override returns (string memory) {
        return _symbol;
    }

    /// The token's decimals plus six: a share starts at a millionth of a token base unit.
    function decimals() public view override returns (uint8) {
        return _tokenDecimals + 6;
    }

    // --- views ------------------------------------------------------------------

    /// Tokens the wrapper holds for its shares.
    function totalTokens() public view returns (uint256) {
        return token.balanceOf(address(this));
    }

    /// USDG that belongs to the shares and isn't reinvested yet: claimed, or still at the series.
    function pendingCash() public view returns (uint256) {
        return usdg.balanceOf(address(this)) + series.claimable(address(this));
    }

    /// Shares for `amount` tokens at the current ratio (as if nothing were pending).
    function previewWrap(uint256 amount) public view returns (uint256 shares) {
        return Math.mulDiv(amount, totalSupply() + VIRTUAL_SHARES, totalTokens() + 1);
    }

    /// Tokens and USDG `shares` stand for now.
    function previewUnwrap(uint256 shares) public view returns (uint256 amount, uint256 cash) {
        uint256 supply = totalSupply() + VIRTUAL_SHARES;
        amount = Math.mulDiv(shares, totalTokens() + 1, supply);
        cash = Math.mulDiv(shares, pendingCash(), supply);
    }

    // --- state changes ----------------------------------------------------------

    /// Pulls `amount` tokens from the caller (approve the wrapper) and mints shares to `to`.
    function wrap(uint256 amount, address to) external nonReentrant returns (uint256 shares) {
        if (amount == 0) revert ZeroAmount();
        _tryCompound();
        // while nobody holds a share, USDG in the wrapper belongs to nobody (a donation): no reason to wait
        uint256 cash = usdg.balanceOf(address(this));
        if (totalSupply() != 0 && cash > DUST_UNITS && cash * BPS > series.notionalOf(totalTokens()) * DUST_BPS) {
            revert CashPending(cash);
        }
        shares = previewWrap(amount);
        if (shares == 0) revert ZeroAmount();
        token.safeTransferFrom(msg.sender, address(this), amount);
        _mint(to, shares);
        emit Wrapped(msg.sender, to, amount, shares);
    }

    /// Burns the caller's `shares` and sends the tokens they stand for, and their part of
    /// the USDG not yet reinvested, to `to`.
    function unwrap(uint256 shares, address to) external nonReentrant returns (uint256 amount, uint256 cash) {
        if (shares == 0) revert ZeroAmount();
        _tryCompound();
        (amount, cash) = previewUnwrap(shares);
        _burn(msg.sender, shares);
        if (amount != 0) token.safeTransfer(to, amount);
        if (cash != 0) usdg.safeTransfer(to, cash);
        emit Unwrapped(msg.sender, to, shares, amount, cash);
    }

    /// Burns the caller's `shares` and sends only the tokens they stand for to `to`. No USDG
    /// is claimed, spent or paid, so this works while USDG is paused or the wrapper is
    /// frozen; the leaver's part of the uninvested USDG stays with the remaining shares.
    function exitTokens(uint256 shares, address to) external nonReentrant returns (uint256 amount) {
        if (shares == 0) revert ZeroAmount();
        amount = Math.mulDiv(shares, totalTokens() + 1, totalSupply() + VIRTUAL_SHARES);
        _burn(msg.sender, shares);
        if (amount != 0) token.safeTransfer(to, amount);
        emit Unwrapped(msg.sender, to, shares, amount, 0);
    }

    /// Permissionless: claims the released USDG and buys tokens with it at the Desk.
    /// Reverts with the Desk's error while it can't sell (see the contract comment).
    function compound() external nonReentrant returns (uint256 tokensBought) {
        // forge-lint: disable-next-line(unused-return)
        series.claim(address(this));
        return _spend();
    }

    /// Only the wrapper itself: `_tryCompound` calls it externally to catch a refusal of the Desk.
    function spendCash() external returns (uint256 tokensBought) {
        if (msg.sender != address(this)) revert OnlySelf();
        return _spend();
    }

    // --- internals --------------------------------------------------------------

    /// Claim, then reinvest if the Desk sells right now; if it doesn't, the USDG waits.
    function _tryCompound() internal {
        // forge-lint: disable-next-line(unused-return)
        series.claim(address(this));
        try this.spendCash() returns (uint256) {} catch {}
    }

    /// Spends the wrapper's USDG on the wrapped leg at the Desk's ask: the most tokens the
    /// USDG pays for. Nothing is bought at the Desk's weekend price.
    function _spend() internal returns (uint256 bought) {
        uint256 cash = usdg.balanceOf(address(this));
        if (cash == 0) return 0;
        // forge-lint: disable-next-line(unused-return)
        (, bool weekendPrice) = desk.spotOf(address(series));
        if (weekendPrice) return 0;
        // the ask per unit of notional, from a one-token quote
        IPerpDesk.Side side = isNote ? IPerpDesk.Side.BuyNote : IPerpDesk.Side.BuyCover;
        // forge-lint: disable-next-line(unused-return)
        (, uint16 priceBps) = desk.quote(address(series), side, 10 ** _tokenDecimals, 0);
        uint256 perToken = series.state().notionalPerToken;
        if (priceBps == 0 || perToken == 0) return 0;
        // the most tokens whose cost, ceil(amount * perToken * priceBps / 1e31), fits the cash
        bought = Math.mulDiv(cash, RAY * BPS, perToken * priceBps);
        if (bought == 0) return 0;

        usdg.forceApprove(address(desk), cash);
        uint256 cost = isNote
            ? desk.buy(address(series), bought, cash, 0, address(0), address(this))
            : desk.buyCover(address(series), bought, cash, 0, address(0), address(this));
        usdg.forceApprove(address(desk), 0);
        emit Compounded(cost, bought);
    }
}
