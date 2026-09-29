// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol";
import {Create2} from "@openzeppelin/contracts/utils/Create2.sol";
import {Strings} from "@openzeppelin/contracts/utils/Strings.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {ISeriesFactory} from "./interfaces/ISeriesFactory.sol";
import {SeriesTerms} from "./interfaces/INoteSeries.sol";
import {IAggregatorV3} from "./interfaces/IAggregatorV3.sol";
import {FixingsRecorder} from "./FixingsRecorder.sol";
import {NoteSeries} from "./NoteSeries.sol";
import {SeriesToken} from "./SeriesToken.sol";

/// L1 entry point. Permissionless, deterministic, no admin. Deploys its own
/// NoteSeries and SeriesToken implementations, so only clones initialized by
/// this factory exist behind `isSeries`. Series, NOTE and WRITER are clones at
/// CREATE2 addresses derived from seriesId; recorders are CREATE2 deployments
/// derived from the feed (salt 0, the feed is in the init code).
contract SeriesFactory is ISeriesFactory {
    uint8 public constant COLLATERAL_DECIMALS = 6; // payouts are per 1e6 base units
    uint32 public constant MIN_INTERVAL = 1 hours;
    uint8 public constant MAX_OBSERVATIONS = 104;
    uint16 public constant MAX_BARRIER_BPS = 20_000;
    uint16 public constant MAX_COUPON_BPS = 10_000;

    address public immutable collateral;
    address public immutable seriesImplementation;
    address public immutable tokenImplementation;

    mapping(bytes32 => address) public seriesOf;
    mapping(address => bool) public isSeries;
    address[] internal _allSeries;

    error BadCollateral();

    constructor(address collateral_) {
        if (IERC20Metadata(collateral_).decimals() != COLLATERAL_DECIMALS) revert BadCollateral();
        collateral = collateral_;
        seriesImplementation = address(new NoteSeries());
        tokenImplementation = address(new SeriesToken());
    }

    // --- views ------------------------------------------------------------------

    function seriesId(SeriesTerms calldata terms) public pure returns (bytes32) {
        return keccak256(abi.encode(terms));
    }

    function predictSeries(SeriesTerms calldata terms)
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

    function recorderOf(address feed) public view returns (address) {
        return Create2.computeAddress(bytes32(0), keccak256(_recorderInitCode(feed)));
    }

    // --- permissionless deployment ---------------------------------------------

    function deployRecorder(address feed) public returns (address recorder) {
        recorder = recorderOf(feed);
        if (recorder.code.length != 0) return recorder;
        _checkFeed(feed);
        Create2.deploy(0, bytes32(0), _recorderInitCode(feed));
        emit RecorderDeployed(feed, recorder);
    }

    function createSeries(SeriesTerms calldata terms) external returns (address series) {
        bytes32 id = seriesId(terms);
        series = seriesOf[id];
        if (series != address(0)) return series;

        _checkTerms(terms);
        address recorder = deployRecorder(terms.feed);

        series = Clones.cloneDeterministic(seriesImplementation, id);
        address note = Clones.cloneDeterministic(tokenImplementation, _tokenSalt(id, true));
        address writer = Clones.cloneDeterministic(tokenImplementation, _tokenSalt(id, false));

        string memory tag = Strings.toHexString(uint256(uint32(bytes4(id))), 4);
        SeriesToken(note)
            .initialize(
                series, true, COLLATERAL_DECIMALS, string.concat("Autocall NOTE ", tag), string.concat("NOTE-", tag)
            );
        SeriesToken(writer)
            .initialize(
                series,
                false,
                COLLATERAL_DECIMALS,
                string.concat("Autocall WRITER ", tag),
                string.concat("WRITER-", tag)
            );
        NoteSeries(series).initialize(id, terms, note, writer, recorder, collateral);

        seriesOf[id] = series;
        isSeries[series] = true;
        _allSeries.push(series);
        emit SeriesCreated(id, terms.feed, series, note, writer, terms);
    }

    // --- internals --------------------------------------------------------------

    function _checkTerms(SeriesTerms calldata t) internal pure {
        if (
            t.strikeTime == 0 || t.observationInterval < MIN_INTERVAL || t.observationCount == 0
                || t.observationCount > MAX_OBSERVATIONS
                || uint256(t.strikeTime) + (uint256(t.observationCount) + 1) * t.observationInterval > type(uint40).max
        ) revert BadSchedule();
        if (t.kiBarrierBps == 0 || t.kiBarrierBps > t.acBarrierBps || t.acBarrierBps > MAX_BARRIER_BPS) {
            revert BadBarriers();
        }
        if (t.couponBpsPerPeriod > MAX_COUPON_BPS) revert BadCoupon();
    }

    function _checkFeed(address feed) internal view {
        if (feed.code.length == 0) revert BadFeed();
        try IAggregatorV3(feed).decimals() returns (uint8 d) {
            if (d != 8) revert BadFeed();
        } catch {
            revert BadFeed();
        }
    }

    function _recorderInitCode(address feed) internal pure returns (bytes memory) {
        return abi.encodePacked(type(FixingsRecorder).creationCode, abi.encode(feed));
    }

    function _tokenSalt(bytes32 id, bool isNote) internal pure returns (bytes32) {
        return keccak256(abi.encode(id, isNote));
    }
}
