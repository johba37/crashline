// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// L3, LP side of the Desk: a redemption queue next to its ERC-4626 functions.
///
/// Why. ERC-4626 `withdraw` / `redeem` need the share price now and idle USDG
/// now, so they stop on weekends, while a fixing is pending, in the model's
/// observation-day bands, and whenever the idle USDG is locked in series. A
/// request needs neither: it can be made at any time and is paid as soon as
/// the share price is known and USDG is free.
///
/// Lifecycle of a request:
///   requestRedeem(shares)   any time. The shares move into the Desk and keep
///                           their share of gains and losses until they are filled.
///   processQueue(n)         anyone, while every held series can be quoted
///                           (otherwise it reverts like totalAssets). Fills
///                           requests first in, first out at the share price of
///                           that moment, out of idle USDG; the last one
///                           partially if the USDG runs out. Filled shares are
///                           burned and their USDG is set aside.
///   claim(to)               the owner pulls the USDG set aside, to any address
///                           (USDG can freeze a single account).
///   cancelRedeem(id)        the owner takes the unfilled shares back.
///
/// The queue comes first:
///  - A trade that adds to the Desk's position (IDeskCover) first fills up to
///    QUEUE_BATCH requests itself and reverts QueuePending if shares are still
///    queued afterwards: no new risk while LPs wait for their money. Trades
///    that shrink a position, `collect` and deposits keep working; they are
///    what frees USDG for the queue.
///  - `maxWithdraw` / `maxRedeem` are 0 while shares are queued, so nobody
///    withdraws ahead of the queue.
///  - USDG set aside for claims is not part of totalAssets, of the idle USDG
///    or of what a trade may spend (ReservedForClaims).
///
/// FRONTENDS: quotes don't look at the queue. Read `queuedShares()` before
/// offering a trade that adds to the Desk's position, as with `risk(feed)`.
interface IDeskQueue {
    event RedeemRequested(uint256 indexed id, address indexed owner, uint256 shares);
    /// `shares` burned for `assets` USDG, now claimable by `owner`; emitted per fill (a request can fill in parts).
    event RedeemFilled(uint256 indexed id, address indexed owner, uint256 shares, uint256 assets);
    event RedeemCancelled(uint256 indexed id, address indexed owner, uint256 shares);
    event RedeemClaimed(address indexed owner, address to, uint256 assets);

    error RequestTooSmall(uint256 shares, uint256 minShares);
    error NotRequestOwner(uint256 id);
    error NothingToClaim();
    error QueuePending(uint256 queuedShares);
    error ReservedForClaims();

    /// Smallest request, in shares (12 decimals): keeps the queue from filling with dust.
    function MIN_REQUEST_SHARES() external view returns (uint256);
    /// Requests a trade that adds to a position fills by itself before it gives up.
    function QUEUE_BATCH() external view returns (uint256);

    /// Shares waiting in the queue (held by the Desk itself, still part of totalSupply).
    function queuedShares() external view returns (uint256);
    /// USDG set aside for filled requests and not yet claimed.
    function reservedAssets() external view returns (uint256);
    function claimableAssets(address owner) external view returns (uint256);
    /// Requests `head` .. `length - 1` are still in the queue (cancelled ones have 0 shares).
    function queue() external view returns (uint256 head, uint256 length);
    /// `shares` is the unfilled rest.
    function redeemRequest(uint256 id) external view returns (address owner, uint256 shares);

    /// Moves `shares` of the caller into the queue.
    function requestRedeem(uint256 shares) external returns (uint256 id);
    /// Returns the unfilled shares of the caller's request.
    function cancelRedeem(uint256 id) external returns (uint256 shares);
    /// Fills up to `maxRequests` requests from the head of the queue.
    function processQueue(uint256 maxRequests) external returns (uint256 sharesFilled, uint256 assetsSetAside);
    /// Pays the caller's claimable USDG to `to`.
    function claim(address to) external returns (uint256 assets);
}
