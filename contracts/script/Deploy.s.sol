// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {VmSafe} from "forge-std/Vm.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SeriesFactory} from "../src/SeriesFactory.sol";
import {NoteQuoter} from "../src/NoteQuoter.sol";
import {Desk} from "../src/Desk.sol";
import {MockChainlinkFeed} from "../src/mocks/MockChainlinkFeed.sol";
import {ISurrogatePricer} from "../src/interfaces/ISurrogatePricer.sol";

/// Robinhood Chain testnet (46630) deployment of the Solidity side: factory
/// (with its series and token implementations), quoter, Desk on the real USDG,
/// and a MockChainlinkFeed for staged demo history. The Stylus pricer is
/// deployed separately (`cargo stylus deploy`); pass its address as PRICER to
/// record it and check its ABI.
///
///   forge script script/Deploy.s.sol --rpc-url https://rpc.testnet.chain.robinhood.com            # simulate
///   DEPLOYER_KEY=0x… forge script script/Deploy.s.sol --rpc-url … --broadcast                      # deploy
///
/// With --broadcast the addresses go to deployments/<chainid>.json.
/// The curator then lists series and must call `desk.setRiskBudget(feed, bps)`:
/// until a feed has a budget, no trade may add to the Desk's positions on it.
contract Deploy is Script {
    address constant USDG_TESTNET = 0x7E955252E15c84f5768B83c41a71F9eba181802F;
    uint256 constant CHAIN_ID = 46630;
    uint40 constant MAX_FEED_STALENESS = 26 hours;
    uint32 constant MIN_SECS_TO_OBSERVATION = 1 hours;

    function run() external {
        require(block.chainid == CHAIN_ID, "Deploy.s.sol targets Robinhood Chain testnet (46630)");
        address usdg = vm.envOr("USDG", USDG_TESTNET);
        address pricer = vm.envOr("PRICER", address(0));
        uint256 key = vm.envOr("DEPLOYER_KEY", uint256(0));
        address deployer = key != 0 ? vm.addr(key) : msg.sender;
        address curator = vm.envOr("CURATOR", deployer);

        if (key != 0) vm.startBroadcast(key);
        else vm.startBroadcast();
        SeriesFactory factory = new SeriesFactory(usdg);
        NoteQuoter quoter = new NoteQuoter(MAX_FEED_STALENESS);
        Desk desk = new Desk(IERC20(usdg), factory, quoter, curator, MIN_SECS_TO_OBSERVATION);
        MockChainlinkFeed feed = new MockChainlinkFeed("RHTSLA / USD (staged demo feed)");
        address recorder = factory.deployRecorder(address(feed));
        vm.stopBroadcast();

        bytes32 weightsHash;
        if (pricer != address(0)) weightsHash = ISurrogatePricer(pricer).weightsHash();

        console2.log("chain id          ", block.chainid);
        console2.log("deployer          ", deployer);
        console2.log("curator (owner)   ", curator);
        console2.log("USDG              ", usdg);
        console2.log("SeriesFactory     ", address(factory));
        console2.log("  series impl     ", factory.seriesImplementation());
        console2.log("  token impl      ", factory.tokenImplementation());
        console2.log("NoteQuoter        ", address(quoter));
        console2.log("Desk              ", address(desk));
        console2.log("MockChainlinkFeed ", address(feed));
        console2.log("FixingsRecorder   ", recorder);
        if (pricer != address(0)) {
            console2.log("SurrogatePricer   ", pricer);
            console2.logBytes32(weightsHash);
        }

        if (vm.isContext(VmSafe.ForgeContext.ScriptBroadcast)) {
            string memory o = "deployment";
            vm.serializeUint(o, "chainId", block.chainid);
            vm.serializeAddress(o, "usdg", usdg);
            vm.serializeAddress(o, "seriesFactory", address(factory));
            vm.serializeAddress(o, "seriesImplementation", factory.seriesImplementation());
            vm.serializeAddress(o, "tokenImplementation", factory.tokenImplementation());
            vm.serializeAddress(o, "noteQuoter", address(quoter));
            vm.serializeAddress(o, "desk", address(desk));
            vm.serializeAddress(o, "mockFeed", address(feed));
            vm.serializeAddress(o, "fixingsRecorder", recorder);
            vm.serializeAddress(o, "curator", curator);
            vm.serializeAddress(o, "surrogatePricer", pricer);
            string memory out = vm.serializeBytes32(o, "pricerWeightsHash", weightsHash);
            vm.writeJson(out, string.concat(vm.projectRoot(), "/../deployments/", vm.toString(block.chainid), ".json"));
        }
    }
}
