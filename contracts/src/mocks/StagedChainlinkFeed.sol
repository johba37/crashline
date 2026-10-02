// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {MockChainlinkFeed} from "./MockChainlinkFeed.sol";

/// MockChainlinkFeed that is deployed with its price history: a backdated round per
/// (answer, time), then one at `spot` now. One transaction where a wallet would otherwise
/// sign each round (the app's /setup page). The deployer owns it, as with the mock.
contract StagedChainlinkFeed is MockChainlinkFeed {
    constructor(string memory description_, int256[] memory answers, uint40[] memory times, int256 spot)
        MockChainlinkFeed(description_)
    {
        for (uint256 i = 0; i < answers.length; i++) {
            _push(answers[i], times[i]);
        }
        // forge-lint: disable-next-line(unsafe-typecast)
        _push(spot, uint40(block.timestamp));
    }
}
