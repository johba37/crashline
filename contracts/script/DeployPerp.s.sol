// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {VmSafe} from "forge-std/Vm.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SeriesFactory} from "../src/SeriesFactory.sol";
import {PerpFactory} from "../src/PerpFactory.sol";
import {PerpQuoter} from "../src/PerpQuoter.sol";
import {PerpDesk} from "../src/PerpDesk.sol";
import {PerpFormulaPricer} from "../src/PerpFormulaPricer.sol";
import {ISeriesFactory} from "../src/interfaces/ISeriesFactory.sol";
import {IPerpPricer, PerpProduct} from "../src/interfaces/IPerpPricer.sol";

/// Robinhood Chain testnet (46630) deployment of the v2 perpetual note's
/// Solidity side: the perpetual factory (with its series and token
/// implementations), the quoter, the Desk on the real USDG, and a
/// formula-only pricer for the P1 product. Recorders are the v1 factory's:
/// pass the deployed one as SERIES_FACTORY (deployments/46630.json), or leave
/// it out and a fresh v1 factory is deployed for them. The Stylus student is
/// deployed separately (`PRICER_MODEL_DIR=../../model/p1 cargo stylus
/// deploy`); pass its address as PERP_PRICER to record it and check its ABI.
///
///   forge script script/DeployPerp.s.sol --rpc-url https://rpc.testnet.chain.robinhood.com        # simulate
///   DEPLOYER_KEY=0x… forge script script/DeployPerp.s.sol --rpc-url … --broadcast                 # deploy
///
/// With --broadcast the addresses go to deployments/<chainid>-perp.json.
/// The curator then creates and lists series (pricer, vol, next earnings date,
/// cap) and must call `desk.setRiskBudget(feed, bps)`: until a feed has a
/// budget, no trade may add to the Desk's positions on it.
contract DeployPerp is Script {
    address constant USDG_TESTNET = 0x7E955252E15c84f5768B83c41a71F9eba181802F;
    uint256 constant CHAIN_ID = 46630;
    uint40 constant MAX_FEED_STALENESS = 26 hours;
    /// No trade in the last 6 hours before a fixing: model/p1 refuses quotes next to the
    /// barriers there (its two exclusion bands), so with this band the Desk never meets them.
    uint32 constant MIN_SECS_TO_FIXING = 6 hours;

    /// The P1 product (docs/v2-spec.md §4): k 60%, phi 1/yr at weekly fixings, drift 4%, discount 0.
    function p1() public pure returns (PerpProduct memory) {
        return PerpProduct({
            kiBarrierBps: 6000,
            meltShare: 18_995_352_771_274_247,
            fixingInterval: 604_800,
            driftBps: 400,
            discountBps: 0
        });
    }

    /// Where the formula-only pricer answers: model/p1's two fixing bands and its vol range.
    function p1Bands() public pure returns (PerpFormulaPricer.Bands memory) {
        return PerpFormulaPricer.Bands({
            fixingBandSecs: 6 hours, knockInBandBps: 1000, healBandBps: 500, volMinBps: 2000, volMaxBps: 9000
        });
    }

    function run() external {
        require(block.chainid == CHAIN_ID, "DeployPerp.s.sol targets Robinhood Chain testnet (46630)");
        address usdg = vm.envOr("USDG", USDG_TESTNET);
        address seriesFactory = vm.envOr("SERIES_FACTORY", address(0));
        address student = vm.envOr("PERP_PRICER", address(0));
        uint256 key = vm.envOr("DEPLOYER_KEY", uint256(0));
        address deployer = key != 0 ? vm.addr(key) : msg.sender;
        address curator = vm.envOr("CURATOR", deployer);

        if (key != 0) vm.startBroadcast(key);
        else vm.startBroadcast();
        if (seriesFactory == address(0)) seriesFactory = address(new SeriesFactory(usdg));
        PerpFactory factory = new PerpFactory(usdg, ISeriesFactory(seriesFactory));
        PerpQuoter quoter = new PerpQuoter(MAX_FEED_STALENESS);
        PerpDesk desk = new PerpDesk(IERC20(usdg), factory, quoter, curator, MIN_SECS_TO_FIXING);
        PerpFormulaPricer formulaPricer = new PerpFormulaPricer(p1(), p1Bands());
        vm.stopBroadcast();

        bytes32 weightsHash;
        if (student != address(0)) {
            weightsHash = IPerpPricer(student).weightsHash();
            require(IPerpPricer(student).featureSpecVersion() == 2, "PERP_PRICER is not a feature spec 2 model");
        }

        console2.log("chain id            ", block.chainid);
        console2.log("deployer            ", deployer);
        console2.log("curator (owner)     ", curator);
        console2.log("USDG                ", usdg);
        console2.log("SeriesFactory (v1)  ", seriesFactory);
        console2.log("PerpFactory         ", address(factory));
        console2.log("  series impl       ", factory.seriesImplementation());
        console2.log("  token impl        ", factory.tokenImplementation());
        console2.log("PerpQuoter          ", address(quoter));
        console2.log("PerpDesk            ", address(desk));
        console2.log("PerpFormulaPricer   ", address(formulaPricer));
        if (student != address(0)) {
            console2.log("PerpPricer (Stylus) ", student);
            console2.logBytes32(weightsHash);
        }

        if (vm.isContext(VmSafe.ForgeContext.ScriptBroadcast)) {
            string memory o = "deployment";
            vm.serializeUint(o, "chainId", block.chainid);
            vm.serializeAddress(o, "usdg", usdg);
            vm.serializeAddress(o, "seriesFactory", seriesFactory);
            vm.serializeAddress(o, "perpFactory", address(factory));
            vm.serializeAddress(o, "perpSeriesImplementation", factory.seriesImplementation());
            vm.serializeAddress(o, "perpTokenImplementation", factory.tokenImplementation());
            vm.serializeAddress(o, "perpQuoter", address(quoter));
            vm.serializeAddress(o, "perpDesk", address(desk));
            vm.serializeAddress(o, "perpFormulaPricer", address(formulaPricer));
            vm.serializeAddress(o, "curator", curator);
            vm.serializeAddress(o, "perpPricer", student);
            string memory out = vm.serializeBytes32(o, "perpPricerWeightsHash", weightsHash);
            vm.writeJson(
                out, string.concat(vm.projectRoot(), "/../deployments/", vm.toString(block.chainid), "-perp.json")
            );
        }
    }
}
