// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol";
import {Strings} from "@openzeppelin/contracts/utils/Strings.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {IPerpFactory} from "./interfaces/IPerpFactory.sol";
import {ISeriesFactory} from "./interfaces/ISeriesFactory.sol";
import {PerpTerms} from "./interfaces/IPerpSeries.sol";
import {PerpSeries} from "./PerpSeries.sol";
import {PerpToken} from "./PerpToken.sol";

/// L1 entry point of the v2 perpetual note. Permissionless, deterministic, no
/// admin. Deploys its own PerpSeries and PerpToken implementations, so only
/// clones initialized by this factory exist behind `isSeries`. Series, NOTE
/// and WRITER are clones at CREATE2 addresses derived from seriesId. The
/// recorder of a feed is the one the v1 SeriesFactory deploys (and checks:
/// 8 decimals), shared by both generations.
contract PerpFactory is IPerpFactory {
    uint8 public constant COLLATERAL_DECIMALS = 6; // amounts are per 1e6 base units
    uint32 public constant MIN_INTERVAL = 1 hours;
    uint16 public constant MAX_BARRIER_BPS = 10_000; // the knock-in sits at or below the reference
    uint64 public constant MAX_MELT_SHARE = 1e18; // exclusive
    uint64 public constant MAX_COUPON_RESERVE = 5e6; // 5 x the notional: keeps a price in bps inside uint16
    uint64 public constant COUPON_RESERVE_STEP = 100; // 1 bps of notional: prices are in bps, the pair value must be exact in them

    address public immutable collateral;
    address public immutable recorders;
    address public immutable seriesImplementation;
    address public immutable tokenImplementation;

    mapping(bytes32 => address) public seriesOf;
    mapping(address => bool) public isSeries;
    address[] internal _allSeries;

    error BadCollateral();

    constructor(address collateral_, ISeriesFactory recorders_) {
        if (IERC20Metadata(collateral_).decimals() != COLLATERAL_DECIMALS) revert BadCollateral();
        if (recorders_.collateral() != collateral_) revert BadCollateral();
        collateral = collateral_;
        recorders = address(recorders_);
        seriesImplementation = address(new PerpSeries());
        tokenImplementation = address(new PerpToken());
    }

    // --- views ------------------------------------------------------------------

    function seriesId(PerpTerms calldata terms) public pure returns (bytes32) {
        return keccak256(abi.encode(terms));
    }

    function predictSeries(PerpTerms calldata terms)
        external
        view
        returns (address series, address note, address writer)
    {
        bytes32 id = seriesId(terms);
        series = Clones.predictDeterministicAddress(seriesImplementation, id);
        note = Clones.predictDeterministicAddress(tokenImplementation, _tokenSalt(id, true));
        writer = Clones.predictDeterministicAddress(tokenImplementation, _tokenSalt(id, false));
    }

    function allSeries() external view returns (address[] memory) {
        return _allSeries;
    }

    function recorderOf(address feed) external view returns (address) {
        return ISeriesFactory(recorders).recorderOf(feed);
    }

    // --- permissionless deployment ---------------------------------------------

    function createSeries(PerpTerms calldata terms) external returns (address series) {
        bytes32 id = seriesId(terms);
        series = seriesOf[id];
        if (series != address(0)) return series;

        _checkTerms(terms);

        series = Clones.cloneDeterministic(seriesImplementation, id);
        address note = Clones.cloneDeterministic(tokenImplementation, _tokenSalt(id, true));
        address writer = Clones.cloneDeterministic(tokenImplementation, _tokenSalt(id, false));
        // register before the external calls: the recorder (the v1 factory checks the feed
        // and reverts BadFeed) and the trusted initialize calls
        seriesOf[id] = series;
        isSeries[series] = true;
        _allSeries.push(series);
        address recorder = ISeriesFactory(recorders).deployRecorder(terms.feed);

        // forge-lint: disable-next-line(unsafe-typecast)
        string memory tag = Strings.toHexString(uint256(uint32(bytes4(id))), 4); // bytes4 -> uint32 is exact
        PerpToken(note)
            .initialize(
                series, true, COLLATERAL_DECIMALS, string.concat("Perpetual NOTE ", tag), string.concat("pNOTE-", tag)
            );
        PerpToken(writer)
            .initialize(
                series,
                false,
                COLLATERAL_DECIMALS,
                string.concat("Perpetual WRITER ", tag),
                string.concat("pWRITER-", tag)
            );
        PerpSeries(series).initialize(id, terms, note, writer, recorder, collateral);
        emit PerpSeriesCreated(id, terms.feed, series, note, writer, terms);
    }

    // --- internals --------------------------------------------------------------

    function _checkTerms(PerpTerms calldata t) internal pure {
        if (t.firstFixing == 0 || t.fixingInterval < MIN_INTERVAL) revert BadSchedule();
        if (t.kiBarrierBps == 0 || t.kiBarrierBps > MAX_BARRIER_BPS) revert BadBarrier();
        if (t.meltShare == 0 || t.meltShare >= MAX_MELT_SHARE) revert BadMeltShare();
        if (t.couponReserve > MAX_COUPON_RESERVE || t.couponReserve % COUPON_RESERVE_STEP != 0) {
            revert BadCouponReserve();
        }
    }

    function _tokenSalt(bytes32 id, bool isNote) internal pure returns (bytes32) {
        return keccak256(abi.encode(id, isNote));
    }
}
