// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {ISeriesToken} from "./interfaces/ISeriesToken.sol";
import {IPerpSeries} from "./interfaces/IPerpSeries.sol";

/// NOTE or WRITER of one perpetual series: an OpenZeppelin ERC-20 clone like
/// SeriesToken, with one addition. Before a transfer changes balances it tells
/// its series, which settles the USDG released so far to both holders (the
/// cumulative index of IPerpSeries). The series is the only contract called;
/// receivers get no hook. Balances never rebase: one token stands for less
/// notional after each fixing (series.state().notionalPerToken), and prices
/// are quoted per token. Only its series can mint or burn, and the series
/// settles the holder itself on those paths.
contract PerpToken is ERC20, ISeriesToken {
    /// The factory that deployed this implementation. Immutable, so every
    /// clone sees the same value through delegatecall: only that factory can
    /// initialize a clone.
    address public immutable factory;

    address public series;
    bool public isNote;
    uint8 private _decimals;
    string private _name;
    string private _symbol;

    error AlreadyInitialized();
    error OnlyFactory();

    constructor() ERC20("", "") {
        factory = msg.sender;
        series = address(this); // the implementation itself is never initialized
    }

    /// Called once by the factory right after cloning.
    function initialize(address series_, bool isNote_, uint8 decimals_, string calldata name_, string calldata symbol_)
        external
    {
        if (msg.sender != factory) revert OnlyFactory();
        if (series != address(0)) revert AlreadyInitialized();
        series = series_;
        isNote = isNote_;
        _decimals = decimals_;
        _name = name_;
        _symbol = symbol_;
    }

    function mint(address to, uint256 amount) external {
        if (msg.sender != series) revert OnlySeries();
        _mint(to, amount);
    }

    /// No allowance: the series only ever burns its own caller's tokens.
    function burn(address from, uint256 amount) external {
        if (msg.sender != series) revert OnlySeries();
        _burn(from, amount);
    }

    function name() public view override(ERC20, IERC20Metadata) returns (string memory) {
        return _name;
    }

    function symbol() public view override(ERC20, IERC20Metadata) returns (string memory) {
        return _symbol;
    }

    function decimals() public view override(ERC20, IERC20Metadata) returns (uint8) {
        return _decimals;
    }

    /// Transfers settle both holders at the series first; mint and burn are
    /// series calls, which settle the holder before they get here.
    function _update(address from, address to, uint256 value) internal override {
        if (from != address(0) && to != address(0)) {
            IPerpSeries(series).checkpoint(from, balanceOf(from), to, balanceOf(to));
        }
        super._update(from, to, value);
    }
}
