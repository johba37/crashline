// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {ISeriesToken} from "./interfaces/ISeriesToken.sol";

/// NOTE or WRITER of one series. A plain ERC-20 (OpenZeppelin, no hooks, no
/// rebasing, no fees) deployed as an EIP-1167 clone by SeriesFactory, which
/// initializes it in the same transaction. Only its series can mint or burn.
contract SeriesToken is ERC20, ISeriesToken {
    /// The factory that deployed this implementation. Immutable, so every
    /// clone sees the same value through delegatecall: only that factory can
    /// initialize a clone.
    address public immutable factory;

    address public series;
    bool public isNote;
    uint8 private _decimals;
    string private _name;
    string private _symbol;

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
}
