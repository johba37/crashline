// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC4626} from "@openzeppelin/contracts/interfaces/IERC4626.sol";
import {INoteQuoter} from "./INoteQuoter.sol";
import {ISurrogatePricer} from "./ISurrogatePricer.sol";
import {ISeriesFactory} from "./ISeriesFactory.sol";

/// L3: the market for NOTE, and the pricer's only in-path use. An ERC-4626
/// vault on USDG: LPs deposit USDG; the Desk sells NOTE around the model's
/// quote and buys it back for early exit. The WRITER leg, the two prices and
/// the risk budget are in IDeskCover, which extends this interface. All policy
/// lives here: the curated grid, which model prices which series, vol, caps,
/// bands, fee cap. A model covers one product (note terms), not a stock or a
/// series: every series with those terms can share it, and a stock enters only
/// through the vol the listing sets.
///
/// Units: noteAmount in NOTE base units (6 decimals, 1 NOTE = 1 USDG notional);
/// priceBps (the price applied: the model's quote moved by the listing's
/// spread, IDeskCover) and feeBps in bps of notional. cost/proceeds in USDG base units.
///   buy:  cost     = ceil(noteAmount * priceBps / 1e4)  + fee
///   sell: proceeds = floor(noteAmount * priceBps / 1e4) - fee
///   fee = ceil(noteAmount * feeBps / 1e4); feeReceiver gets fee minus the
///   BACKSTOP_SHARE_BPS slice, which stays in the vault.
///
/// LP flows: maxDeposit / maxWithdraw return 0 while any held series can't be
/// quoted (weekend, pending fixing), because share price would be unknown.
interface IDesk is IERC4626 {
    struct Listing {
        bool active;
        ISurrogatePricer pricer; // certified model for this series' product
        uint16 volBpsAnnual; // implied vol used for every quote of this series
        // (a vol-pinned model refuses any other vol: see pricer.certifiedRange(FIELD_VOL))
        uint128 capNotional; // max WRITER the Desk may hold in this series (6 decimals):
        // the NOTE it may sell beyond its inventory, by minting pairs
        uint128 soldNotional; // WRITER the Desk holds now; computed by listing(), not stored
    }

    /// Emitted on listing and on every update (new model, vol or cap).
    event SeriesListed(
        address indexed series, address indexed pricer, bytes32 weightsHash, uint16 volBpsAnnual, uint128 capNotional
    );
    event SeriesDelisted(address indexed series);
    /// Emitted on every buy: the quote, the model version and the explicit fee.
    event NoteBought(
        address indexed series,
        address indexed buyer,
        address to,
        uint256 noteAmount,
        uint16 priceBps,
        uint256 cost,
        uint16 feeBps,
        address feeReceiver,
        bytes32 weightsHash
    );
    event NoteSold(
        address indexed series,
        address indexed seller,
        address to,
        uint256 noteAmount,
        uint16 priceBps,
        uint256 proceeds,
        uint16 feeBps,
        address feeReceiver,
        bytes32 weightsHash
    );
    event Collected(address indexed series, uint256 collateralOut);
    /// Emitted at construction and on every change of the observation band.
    event MinSecsToObservationSet(uint32 secs);

    error NotListed(address series);
    error NotFactorySeries(address series);
    error ModelMismatch(uint8 field); // PricerInputs index outside the pricer's certifiedRange
    error CapExceeded(uint256 requested, uint256 available);
    error FeeTooHigh(uint16 feeBps);
    error Slippage(uint256 actual, uint256 limit);
    error TooCloseToObservation(uint40 obsTime); // ε-band before each observation
    /// The factory's collateral is not the Desk's asset (constructor).
    error WrongAsset();
    // plus INoteQuoter errors and ISurrogatePricer.OutOfRange, bubbled up

    function factory() external view returns (ISeriesFactory);
    function quoter() external view returns (INoteQuoter);
    function MAX_FEE_BPS() external view returns (uint16);
    function BACKSTOP_SHARE_BPS() external view returns (uint16);
    function minSecsToObservation() external view returns (uint32);

    function listing(address series) external view returns (Listing memory);
    function listedSeries() external view returns (address[] memory);

    function quoteBuy(address series, uint256 noteAmount, uint16 feeBps)
        external
        view
        returns (uint256 cost, uint16 priceBps);
    function quoteSell(address series, uint256 noteAmount, uint16 feeBps)
        external
        view
        returns (uint256 proceeds, uint16 priceBps);

    /// Buyer pays `cost` USDG (approve the Desk) and receives `noteAmount` NOTE at `to`.
    function buy(address series, uint256 noteAmount, uint256 maxCost, uint16 feeBps, address feeReceiver, address to)
        external
        returns (uint256 cost);

    /// Seller hands in `noteAmount` NOTE (approve the Desk) and receives `proceeds` USDG at `to`.
    function sell(
        address series,
        uint256 noteAmount,
        uint256 minProceeds,
        uint16 feeBps,
        address feeReceiver,
        address to
    ) external returns (uint256 proceeds);

    /// Permissionless: after settlement, redeem the Desk's WRITER (and any NOTE) into USDG.
    function collect(address series) external returns (uint256 collateralOut);

    // --- curator (owner) -----------------------------------------------------
    /// Lists a series or updates its listing. Static checks, so a series can be
    /// listed before its strike or over a weekend: the series comes from
    /// `factory`, and its ki / ac / coupon, its observationCount and
    /// `volBpsAnnual` lie inside the pricer's certifiedRange. A model whose
    /// domain excludes the series can't be listed (ModelMismatch).
    function listSeries(address series, ISurrogatePricer pricer, uint16 volBpsAnnual, uint128 capNotional) external;
    function delistSeries(address series) external; // stops new buys; sells still allowed
    function setMinSecsToObservation(uint32 secs) external;
}
