// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {PerpTerms} from "./IPerpSeries.sol";

/// L1 entry point of the v2 perpetual note. Permissionless, deterministic, no
/// admin. Series, NOTE and WRITER are EIP-1167 clones at CREATE2 addresses
/// derived from seriesId, as in ISeriesFactory. Fixings come from the same
/// FixingsRecorder per feed that v1 uses (the v1 factory deploys it), so a
/// keeper records a fixing once for both generations.
interface IPerpFactory {
    event PerpSeriesCreated(
        bytes32 indexed seriesId, address indexed feed, address series, address note, address writer, PerpTerms terms
    );

    error BadSchedule(); // interval < 1 h or firstFixing == 0
    error BadBarrier(); // not 0 < k <= 10_000
    error BadMeltShare(); // not 0 < a < 1e18
    error BadCouponReserve(); // R > MAX_COUPON_RESERVE, or not a whole number of bps (a multiple of 100)

    function collateral() external view returns (address); // USDG, immutable
    function recorders() external view returns (address); // the v1 SeriesFactory: one recorder per feed

    function seriesId(PerpTerms calldata terms) external pure returns (bytes32);
    function predictSeries(PerpTerms calldata terms)
        external
        view
        returns (address series, address note, address writer);
    function seriesOf(bytes32 seriesId) external view returns (address); // 0 if not created
    function isSeries(address series) external view returns (bool); // provenance check
    function allSeries() external view returns (address[] memory);
    function recorderOf(address feed) external view returns (address); // deterministic, may be undeployed

    /// Idempotent: returns the existing series for identical terms. Deploys
    /// the feed's recorder if needed.
    function createSeries(PerpTerms calldata terms) external returns (address series);
}
