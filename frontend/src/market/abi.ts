//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
// Aggregator
//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////

export const aggregatorAbi = [
  {
    type: 'function',
    inputs: [],
    name: 'decimals',
    outputs: [{ name: '', internalType: 'uint8', type: 'uint8' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'description',
    outputs: [{ name: '', internalType: 'string', type: 'string' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'roundId', internalType: 'uint80', type: 'uint80' }],
    name: 'getRoundData',
    outputs: [
      { name: '', internalType: 'uint80', type: 'uint80' },
      { name: 'answer', internalType: 'int256', type: 'int256' },
      { name: 'startedAt', internalType: 'uint256', type: 'uint256' },
      { name: 'updatedAt', internalType: 'uint256', type: 'uint256' },
      { name: 'answeredInRound', internalType: 'uint80', type: 'uint80' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'latestRound',
    outputs: [{ name: '', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'latestRoundData',
    outputs: [
      { name: 'roundId', internalType: 'uint80', type: 'uint80' },
      { name: 'answer', internalType: 'int256', type: 'int256' },
      { name: 'startedAt', internalType: 'uint256', type: 'uint256' },
      { name: 'updatedAt', internalType: 'uint256', type: 'uint256' },
      { name: 'answeredInRound', internalType: 'uint80', type: 'uint80' },
    ],
    stateMutability: 'view',
  },
] as const

//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
// Desk
//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////

export const deskAbi = [
  {
    type: 'function',
    inputs: [],
    name: 'BACKSTOP_SHARE_BPS',
    outputs: [{ name: '', internalType: 'uint16', type: 'uint16' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'MAX_COVER_FEE_BPS',
    outputs: [{ name: '', internalType: 'uint16', type: 'uint16' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'MAX_FEE_BPS',
    outputs: [{ name: '', internalType: 'uint16', type: 'uint16' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'MAX_HELD_SERIES',
    outputs: [{ name: '', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'MAX_SPREAD_BPS',
    outputs: [{ name: '', internalType: 'uint16', type: 'uint16' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'owner', internalType: 'address', type: 'address' },
      { name: 'spender', internalType: 'address', type: 'address' },
    ],
    name: 'allowance',
    outputs: [{ name: '', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'spender', internalType: 'address', type: 'address' },
      { name: 'value', internalType: 'uint256', type: 'uint256' },
    ],
    name: 'approve',
    outputs: [{ name: '', internalType: 'bool', type: 'bool' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [],
    name: 'asset',
    outputs: [
      { name: 'assetTokenAddress', internalType: 'address', type: 'address' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'account', internalType: 'address', type: 'address' }],
    name: 'balanceOf',
    outputs: [{ name: '', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'series', internalType: 'address', type: 'address' },
      { name: 'noteAmount', internalType: 'uint256', type: 'uint256' },
      { name: 'maxCost', internalType: 'uint256', type: 'uint256' },
      { name: 'feeBps', internalType: 'uint16', type: 'uint16' },
      { name: 'feeReceiver', internalType: 'address', type: 'address' },
      { name: 'to', internalType: 'address', type: 'address' },
    ],
    name: 'buy',
    outputs: [{ name: 'cost', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [
      { name: 'series', internalType: 'address', type: 'address' },
      { name: 'writerAmount', internalType: 'uint256', type: 'uint256' },
      { name: 'maxCost', internalType: 'uint256', type: 'uint256' },
      { name: 'feeBps', internalType: 'uint16', type: 'uint16' },
      { name: 'feeReceiver', internalType: 'address', type: 'address' },
      { name: 'to', internalType: 'address', type: 'address' },
    ],
    name: 'buyCover',
    outputs: [{ name: 'cost', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [{ name: 'series', internalType: 'address', type: 'address' }],
    name: 'collect',
    outputs: [
      { name: 'collateralOut', internalType: 'uint256', type: 'uint256' },
    ],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [{ name: 'shares', internalType: 'uint256', type: 'uint256' }],
    name: 'convertToAssets',
    outputs: [{ name: 'assets', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'assets', internalType: 'uint256', type: 'uint256' }],
    name: 'convertToShares',
    outputs: [{ name: 'shares', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'decimals',
    outputs: [{ name: '', internalType: 'uint8', type: 'uint8' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'series', internalType: 'address', type: 'address' }],
    name: 'delistSeries',
    outputs: [],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [
      { name: 'assets', internalType: 'uint256', type: 'uint256' },
      { name: 'receiver', internalType: 'address', type: 'address' },
    ],
    name: 'deposit',
    outputs: [{ name: 'shares', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [],
    name: 'factory',
    outputs: [
      { name: '', internalType: 'contract ISeriesFactory', type: 'address' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'heldSeries',
    outputs: [{ name: '', internalType: 'address[]', type: 'address[]' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'series', internalType: 'address', type: 'address' },
      {
        name: 'pricer',
        internalType: 'contract ISurrogatePricer',
        type: 'address',
      },
      { name: 'volBpsAnnual', internalType: 'uint16', type: 'uint16' },
      { name: 'capNotional', internalType: 'uint128', type: 'uint128' },
    ],
    name: 'listSeries',
    outputs: [],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [],
    name: 'listedSeries',
    outputs: [{ name: '', internalType: 'address[]', type: 'address[]' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'series', internalType: 'address', type: 'address' }],
    name: 'listing',
    outputs: [
      {
        name: '',
        internalType: 'struct IDesk.Listing',
        type: 'tuple',
        components: [
          { name: 'active', internalType: 'bool', type: 'bool' },
          {
            name: 'pricer',
            internalType: 'contract ISurrogatePricer',
            type: 'address',
          },
          { name: 'volBpsAnnual', internalType: 'uint16', type: 'uint16' },
          { name: 'capNotional', internalType: 'uint128', type: 'uint128' },
          { name: 'soldNotional', internalType: 'uint128', type: 'uint128' },
        ],
      },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'receiver', internalType: 'address', type: 'address' }],
    name: 'maxDeposit',
    outputs: [{ name: 'maxAssets', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'receiver', internalType: 'address', type: 'address' }],
    name: 'maxMint',
    outputs: [{ name: 'maxShares', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'owner', internalType: 'address', type: 'address' }],
    name: 'maxRedeem',
    outputs: [{ name: 'maxShares', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'owner', internalType: 'address', type: 'address' }],
    name: 'maxWithdraw',
    outputs: [{ name: 'maxAssets', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'minSecsToObservation',
    outputs: [{ name: '', internalType: 'uint32', type: 'uint32' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'shares', internalType: 'uint256', type: 'uint256' },
      { name: 'receiver', internalType: 'address', type: 'address' },
    ],
    name: 'mint',
    outputs: [{ name: 'assets', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [],
    name: 'name',
    outputs: [{ name: '', internalType: 'string', type: 'string' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'assets', internalType: 'uint256', type: 'uint256' }],
    name: 'previewDeposit',
    outputs: [{ name: 'shares', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'shares', internalType: 'uint256', type: 'uint256' }],
    name: 'previewMint',
    outputs: [{ name: 'assets', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'shares', internalType: 'uint256', type: 'uint256' }],
    name: 'previewRedeem',
    outputs: [{ name: 'assets', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'assets', internalType: 'uint256', type: 'uint256' }],
    name: 'previewWithdraw',
    outputs: [{ name: 'shares', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'series', internalType: 'address', type: 'address' },
      { name: 'noteAmount', internalType: 'uint256', type: 'uint256' },
      { name: 'feeBps', internalType: 'uint16', type: 'uint16' },
    ],
    name: 'quoteBuy',
    outputs: [
      { name: 'cost', internalType: 'uint256', type: 'uint256' },
      { name: 'priceBps', internalType: 'uint16', type: 'uint16' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'series', internalType: 'address', type: 'address' },
      { name: 'writerAmount', internalType: 'uint256', type: 'uint256' },
      { name: 'feeBps', internalType: 'uint16', type: 'uint16' },
    ],
    name: 'quoteBuyCover',
    outputs: [
      { name: 'cost', internalType: 'uint256', type: 'uint256' },
      { name: 'priceBps', internalType: 'uint16', type: 'uint16' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'series', internalType: 'address', type: 'address' },
      { name: 'noteAmount', internalType: 'uint256', type: 'uint256' },
      { name: 'feeBps', internalType: 'uint16', type: 'uint16' },
    ],
    name: 'quoteSell',
    outputs: [
      { name: 'proceeds', internalType: 'uint256', type: 'uint256' },
      { name: 'priceBps', internalType: 'uint16', type: 'uint16' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'series', internalType: 'address', type: 'address' },
      { name: 'writerAmount', internalType: 'uint256', type: 'uint256' },
      { name: 'feeBps', internalType: 'uint16', type: 'uint16' },
    ],
    name: 'quoteSellCover',
    outputs: [
      { name: 'proceeds', internalType: 'uint256', type: 'uint256' },
      { name: 'priceBps', internalType: 'uint16', type: 'uint16' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'quoter',
    outputs: [
      { name: '', internalType: 'contract INoteQuoter', type: 'address' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'shares', internalType: 'uint256', type: 'uint256' },
      { name: 'receiver', internalType: 'address', type: 'address' },
      { name: 'owner', internalType: 'address', type: 'address' },
    ],
    name: 'redeem',
    outputs: [{ name: 'assets', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [{ name: 'feed', internalType: 'address', type: 'address' }],
    name: 'risk',
    outputs: [
      { name: 'atRisk', internalType: 'uint256', type: 'uint256' },
      { name: 'limit', internalType: 'uint256', type: 'uint256' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'feed', internalType: 'address', type: 'address' }],
    name: 'riskBudgetBps',
    outputs: [{ name: '', internalType: 'uint16', type: 'uint16' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'series', internalType: 'address', type: 'address' },
      { name: 'noteAmount', internalType: 'uint256', type: 'uint256' },
      { name: 'minProceeds', internalType: 'uint256', type: 'uint256' },
      { name: 'feeBps', internalType: 'uint16', type: 'uint16' },
      { name: 'feeReceiver', internalType: 'address', type: 'address' },
      { name: 'to', internalType: 'address', type: 'address' },
    ],
    name: 'sell',
    outputs: [{ name: 'proceeds', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [
      { name: 'series', internalType: 'address', type: 'address' },
      { name: 'writerAmount', internalType: 'uint256', type: 'uint256' },
      { name: 'minProceeds', internalType: 'uint256', type: 'uint256' },
      { name: 'feeBps', internalType: 'uint16', type: 'uint16' },
      { name: 'feeReceiver', internalType: 'address', type: 'address' },
      { name: 'to', internalType: 'address', type: 'address' },
    ],
    name: 'sellCover',
    outputs: [{ name: 'proceeds', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [{ name: 'secs', internalType: 'uint32', type: 'uint32' }],
    name: 'setMinSecsToObservation',
    outputs: [],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [
      { name: 'feed', internalType: 'address', type: 'address' },
      { name: 'budgetBps', internalType: 'uint16', type: 'uint16' },
    ],
    name: 'setRiskBudget',
    outputs: [],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [
      { name: 'series', internalType: 'address', type: 'address' },
      { name: 'bidBps', internalType: 'uint16', type: 'uint16' },
      { name: 'askBps', internalType: 'uint16', type: 'uint16' },
      { name: 'volBandBps', internalType: 'uint16', type: 'uint16' },
    ],
    name: 'setSpread',
    outputs: [],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [{ name: 'series', internalType: 'address', type: 'address' }],
    name: 'spread',
    outputs: [
      {
        name: '',
        internalType: 'struct IDeskCover.Spread',
        type: 'tuple',
        components: [
          { name: 'bidBps', internalType: 'uint16', type: 'uint16' },
          { name: 'askBps', internalType: 'uint16', type: 'uint16' },
          { name: 'volBandBps', internalType: 'uint16', type: 'uint16' },
        ],
      },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'symbol',
    outputs: [{ name: '', internalType: 'string', type: 'string' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'totalAssets',
    outputs: [
      { name: 'totalManagedAssets', internalType: 'uint256', type: 'uint256' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'totalSupply',
    outputs: [{ name: '', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'to', internalType: 'address', type: 'address' },
      { name: 'value', internalType: 'uint256', type: 'uint256' },
    ],
    name: 'transfer',
    outputs: [{ name: '', internalType: 'bool', type: 'bool' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [
      { name: 'from', internalType: 'address', type: 'address' },
      { name: 'to', internalType: 'address', type: 'address' },
      { name: 'value', internalType: 'uint256', type: 'uint256' },
    ],
    name: 'transferFrom',
    outputs: [{ name: '', internalType: 'bool', type: 'bool' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [
      { name: 'assets', internalType: 'uint256', type: 'uint256' },
      { name: 'receiver', internalType: 'address', type: 'address' },
      { name: 'owner', internalType: 'address', type: 'address' },
    ],
    name: 'withdraw',
    outputs: [{ name: 'shares', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'owner',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'spender',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'value',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
    ],
    name: 'Approval',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'series',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'collateralOut',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
    ],
    name: 'Collected',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'series',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'buyer',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      { name: 'to', internalType: 'address', type: 'address', indexed: false },
      {
        name: 'writerAmount',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
      {
        name: 'priceBps',
        internalType: 'uint16',
        type: 'uint16',
        indexed: false,
      },
      {
        name: 'cost',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
      {
        name: 'feeBps',
        internalType: 'uint16',
        type: 'uint16',
        indexed: false,
      },
      {
        name: 'feeReceiver',
        internalType: 'address',
        type: 'address',
        indexed: false,
      },
      {
        name: 'weightsHash',
        internalType: 'bytes32',
        type: 'bytes32',
        indexed: false,
      },
    ],
    name: 'CoverBought',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'series',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'seller',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      { name: 'to', internalType: 'address', type: 'address', indexed: false },
      {
        name: 'writerAmount',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
      {
        name: 'priceBps',
        internalType: 'uint16',
        type: 'uint16',
        indexed: false,
      },
      {
        name: 'proceeds',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
      {
        name: 'feeBps',
        internalType: 'uint16',
        type: 'uint16',
        indexed: false,
      },
      {
        name: 'feeReceiver',
        internalType: 'address',
        type: 'address',
        indexed: false,
      },
      {
        name: 'weightsHash',
        internalType: 'bytes32',
        type: 'bytes32',
        indexed: false,
      },
    ],
    name: 'CoverSold',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'sender',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'owner',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'assets',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
      {
        name: 'shares',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
    ],
    name: 'Deposit',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      { name: 'secs', internalType: 'uint32', type: 'uint32', indexed: false },
    ],
    name: 'MinSecsToObservationSet',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'series',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'buyer',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      { name: 'to', internalType: 'address', type: 'address', indexed: false },
      {
        name: 'noteAmount',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
      {
        name: 'priceBps',
        internalType: 'uint16',
        type: 'uint16',
        indexed: false,
      },
      {
        name: 'cost',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
      {
        name: 'feeBps',
        internalType: 'uint16',
        type: 'uint16',
        indexed: false,
      },
      {
        name: 'feeReceiver',
        internalType: 'address',
        type: 'address',
        indexed: false,
      },
      {
        name: 'weightsHash',
        internalType: 'bytes32',
        type: 'bytes32',
        indexed: false,
      },
    ],
    name: 'NoteBought',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'series',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'seller',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      { name: 'to', internalType: 'address', type: 'address', indexed: false },
      {
        name: 'noteAmount',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
      {
        name: 'priceBps',
        internalType: 'uint16',
        type: 'uint16',
        indexed: false,
      },
      {
        name: 'proceeds',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
      {
        name: 'feeBps',
        internalType: 'uint16',
        type: 'uint16',
        indexed: false,
      },
      {
        name: 'feeReceiver',
        internalType: 'address',
        type: 'address',
        indexed: false,
      },
      {
        name: 'weightsHash',
        internalType: 'bytes32',
        type: 'bytes32',
        indexed: false,
      },
    ],
    name: 'NoteSold',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      { name: 'feed', internalType: 'address', type: 'address', indexed: true },
      {
        name: 'budgetBps',
        internalType: 'uint16',
        type: 'uint16',
        indexed: false,
      },
    ],
    name: 'RiskBudgetSet',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'series',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
    ],
    name: 'SeriesDelisted',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'series',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'pricer',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'weightsHash',
        internalType: 'bytes32',
        type: 'bytes32',
        indexed: false,
      },
      {
        name: 'volBpsAnnual',
        internalType: 'uint16',
        type: 'uint16',
        indexed: false,
      },
      {
        name: 'capNotional',
        internalType: 'uint128',
        type: 'uint128',
        indexed: false,
      },
    ],
    name: 'SeriesListed',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'series',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'bidBps',
        internalType: 'uint16',
        type: 'uint16',
        indexed: false,
      },
      {
        name: 'askBps',
        internalType: 'uint16',
        type: 'uint16',
        indexed: false,
      },
      {
        name: 'volBandBps',
        internalType: 'uint16',
        type: 'uint16',
        indexed: false,
      },
    ],
    name: 'SpreadSet',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      { name: 'from', internalType: 'address', type: 'address', indexed: true },
      { name: 'to', internalType: 'address', type: 'address', indexed: true },
      {
        name: 'value',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
    ],
    name: 'Transfer',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'sender',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'receiver',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'owner',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'assets',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
      {
        name: 'shares',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
    ],
    name: 'Withdraw',
  },
  {
    type: 'error',
    inputs: [{ name: 'budgetBps', internalType: 'uint16', type: 'uint16' }],
    name: 'BudgetTooHigh',
  },
  {
    type: 'error',
    inputs: [
      { name: 'requested', internalType: 'uint256', type: 'uint256' },
      { name: 'available', internalType: 'uint256', type: 'uint256' },
    ],
    name: 'CapExceeded',
  },
  {
    type: 'error',
    inputs: [{ name: 'feeBps', internalType: 'uint16', type: 'uint16' }],
    name: 'FeeTooHigh',
  },
  { type: 'error', inputs: [], name: 'HeldSeriesLimit' },
  {
    type: 'error',
    inputs: [{ name: 'field', internalType: 'uint8', type: 'uint8' }],
    name: 'ModelMismatch',
  },
  {
    type: 'error',
    inputs: [{ name: 'series', internalType: 'address', type: 'address' }],
    name: 'NotFactorySeries',
  },
  {
    type: 'error',
    inputs: [{ name: 'series', internalType: 'address', type: 'address' }],
    name: 'NotListed',
  },
  {
    type: 'error',
    inputs: [
      { name: 'atRisk', internalType: 'uint256', type: 'uint256' },
      { name: 'limit', internalType: 'uint256', type: 'uint256' },
    ],
    name: 'RiskBudgetExceeded',
  },
  {
    type: 'error',
    inputs: [
      { name: 'actual', internalType: 'uint256', type: 'uint256' },
      { name: 'limit', internalType: 'uint256', type: 'uint256' },
    ],
    name: 'Slippage',
  },
  {
    type: 'error',
    inputs: [{ name: 'spreadBps', internalType: 'uint16', type: 'uint16' }],
    name: 'SpreadTooWide',
  },
  {
    type: 'error',
    inputs: [{ name: 'obsTime', internalType: 'uint40', type: 'uint40' }],
    name: 'TooCloseToObservation',
  },
  { type: 'error', inputs: [], name: 'WrongAsset' },
  {
    type: 'function',
    inputs: [],
    name: 'MIN_REQUEST_SHARES',
    outputs: [{ name: '', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'QUEUE_BATCH',
    outputs: [{ name: '', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'id', internalType: 'uint256', type: 'uint256' }],
    name: 'cancelRedeem',
    outputs: [{ name: 'shares', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [{ name: 'to', internalType: 'address', type: 'address' }],
    name: 'claim',
    outputs: [{ name: 'assets', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [{ name: 'owner', internalType: 'address', type: 'address' }],
    name: 'claimableAssets',
    outputs: [{ name: '', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'maxRequests', internalType: 'uint256', type: 'uint256' }],
    name: 'processQueue',
    outputs: [
      { name: 'sharesFilled', internalType: 'uint256', type: 'uint256' },
      { name: 'assetsSetAside', internalType: 'uint256', type: 'uint256' },
    ],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [],
    name: 'queue',
    outputs: [
      { name: 'head', internalType: 'uint256', type: 'uint256' },
      { name: 'length', internalType: 'uint256', type: 'uint256' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'queuedShares',
    outputs: [{ name: '', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'id', internalType: 'uint256', type: 'uint256' }],
    name: 'redeemRequest',
    outputs: [
      { name: 'owner', internalType: 'address', type: 'address' },
      { name: 'shares', internalType: 'uint256', type: 'uint256' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'shares', internalType: 'uint256', type: 'uint256' }],
    name: 'requestRedeem',
    outputs: [{ name: 'id', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [],
    name: 'reservedAssets',
    outputs: [{ name: '', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      { name: 'id', internalType: 'uint256', type: 'uint256', indexed: true },
      {
        name: 'owner',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'shares',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
    ],
    name: 'RedeemCancelled',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'owner',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      { name: 'to', internalType: 'address', type: 'address', indexed: false },
      {
        name: 'assets',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
    ],
    name: 'RedeemClaimed',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      { name: 'id', internalType: 'uint256', type: 'uint256', indexed: true },
      {
        name: 'owner',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'shares',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
      {
        name: 'assets',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
    ],
    name: 'RedeemFilled',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      { name: 'id', internalType: 'uint256', type: 'uint256', indexed: true },
      {
        name: 'owner',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'shares',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
    ],
    name: 'RedeemRequested',
  },
  {
    type: 'error',
    inputs: [{ name: 'id', internalType: 'uint256', type: 'uint256' }],
    name: 'NotRequestOwner',
  },
  { type: 'error', inputs: [], name: 'NothingToClaim' },
  {
    type: 'error',
    inputs: [
      { name: 'queuedShares', internalType: 'uint256', type: 'uint256' },
    ],
    name: 'QueuePending',
  },
  {
    type: 'error',
    inputs: [
      { name: 'shares', internalType: 'uint256', type: 'uint256' },
      { name: 'minShares', internalType: 'uint256', type: 'uint256' },
    ],
    name: 'RequestTooSmall',
  },
  { type: 'error', inputs: [], name: 'ReservedForClaims' },
  { type: 'error', inputs: [], name: 'BadFeedAnswer' },
  {
    type: 'error',
    inputs: [{ name: 'updatedAt', internalType: 'uint40', type: 'uint40' }],
    name: 'FeedStale',
  },
  {
    type: 'error',
    inputs: [{ name: 'obsTime', internalType: 'uint40', type: 'uint40' }],
    name: 'FixingPending',
  },
  { type: 'error', inputs: [], name: 'NotLive' },
  {
    type: 'error',
    inputs: [{ name: 'field', internalType: 'uint8', type: 'uint8' }],
    name: 'Inconsistent',
  },
  {
    type: 'error',
    inputs: [
      { name: 'field', internalType: 'uint8', type: 'uint8' },
      { name: 'value', internalType: 'int64', type: 'int64' },
    ],
    name: 'OutOfRange',
  },
  {
    type: 'error',
    inputs: [{ name: 'region', internalType: 'uint8', type: 'uint8' }],
    name: 'Uncertified',
  },
  { type: 'error', inputs: [], name: 'AlreadyInitialized' },
  { type: 'error', inputs: [], name: 'AlreadySettled' },
  { type: 'error', inputs: [], name: 'NotSettled' },
  { type: 'error', inputs: [], name: 'NotStruck' },
  { type: 'error', inputs: [], name: 'OnlyFactory' },
  { type: 'error', inputs: [], name: 'ZeroAmount' },
  {
    type: 'error',
    inputs: [
      { name: 'sender', type: 'address' },
      { name: 'balance', type: 'uint256' },
      { name: 'needed', type: 'uint256' },
    ],
    name: 'ERC20InsufficientBalance',
  },
  {
    type: 'error',
    inputs: [
      { name: 'spender', type: 'address' },
      { name: 'allowance', type: 'uint256' },
      { name: 'needed', type: 'uint256' },
    ],
    name: 'ERC20InsufficientAllowance',
  },
] as const

//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
// FixingsRecorder
//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////

export const fixingsRecorderAbi = [
  {
    type: 'function',
    inputs: [],
    name: 'MAX_FIX_AGE',
    outputs: [{ name: '', internalType: 'uint40', type: 'uint40' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'MAX_ROLL',
    outputs: [{ name: '', internalType: 'uint40', type: 'uint40' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'feed',
    outputs: [
      { name: '', internalType: 'contract IAggregatorV3', type: 'address' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'obsTime', internalType: 'uint40', type: 'uint40' }],
    name: 'fixingOf',
    outputs: [
      {
        name: '',
        internalType: 'struct IFixingsRecorder.Fixing',
        type: 'tuple',
        components: [
          { name: 'timestamp', internalType: 'uint40', type: 'uint40' },
          { name: 'price', internalType: 'uint96', type: 'uint96' },
          { name: 'roundId', internalType: 'uint80', type: 'uint80' },
        ],
      },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'obsTime', internalType: 'uint40', type: 'uint40' }],
    name: 'isRecorded',
    outputs: [{ name: '', internalType: 'bool', type: 'bool' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'obsTime', internalType: 'uint40', type: 'uint40' },
      { name: 'roundId', internalType: 'uint80', type: 'uint80' },
    ],
    name: 'recordFixing',
    outputs: [],
    stateMutability: 'nonpayable',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'obsTime',
        internalType: 'uint40',
        type: 'uint40',
        indexed: true,
      },
      {
        name: 'roundId',
        internalType: 'uint80',
        type: 'uint80',
        indexed: false,
      },
      { name: 'price', internalType: 'uint96', type: 'uint96', indexed: false },
      {
        name: 'timestamp',
        internalType: 'uint40',
        type: 'uint40',
        indexed: false,
      },
    ],
    name: 'FixingRecorded',
  },
  {
    type: 'error',
    inputs: [{ name: 'obsTime', internalType: 'uint40', type: 'uint40' }],
    name: 'AlreadyRecorded',
  },
  {
    type: 'error',
    inputs: [{ name: 'answer', internalType: 'int256', type: 'int256' }],
    name: 'BadPrice',
  },
  {
    type: 'error',
    inputs: [
      { name: 'updatedAt', internalType: 'uint40', type: 'uint40' },
      { name: 'obsTime', internalType: 'uint40', type: 'uint40' },
    ],
    name: 'FixingTooStale',
  },
  {
    type: 'error',
    inputs: [{ name: 'obsTime', internalType: 'uint40', type: 'uint40' }],
    name: 'FutureObservation',
  },
  {
    type: 'error',
    inputs: [
      { name: 'roundId', internalType: 'uint80', type: 'uint80' },
      { name: 'obsTime', internalType: 'uint40', type: 'uint40' },
    ],
    name: 'NoGapProof',
  },
  {
    type: 'error',
    inputs: [
      { name: 'roundId', internalType: 'uint80', type: 'uint80' },
      { name: 'obsTime', internalType: 'uint40', type: 'uint40' },
    ],
    name: 'NotLastRoundBefore',
  },
  {
    type: 'error',
    inputs: [
      { name: 'updatedAt', internalType: 'uint40', type: 'uint40' },
      { name: 'obsTime', internalType: 'uint40', type: 'uint40' },
    ],
    name: 'RollTooLong',
  },
] as const

//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
// NoteQuoter
//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////

export const noteQuoterAbi = [
  {
    type: 'function',
    inputs: [],
    name: 'MAX_FEED_STALENESS',
    outputs: [{ name: '', internalType: 'uint40', type: 'uint40' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'series', internalType: 'contract INoteSeries', type: 'address' },
      { name: 'volBpsAnnual', internalType: 'uint16', type: 'uint16' },
    ],
    name: 'inputs',
    outputs: [
      {
        name: '',
        internalType: 'struct PricerInputs',
        type: 'tuple',
        components: [
          { name: 'spotBpsOfInitial', internalType: 'uint16', type: 'uint16' },
          { name: 'distToKnockInBps', internalType: 'int32', type: 'int32' },
          { name: 'volBpsAnnual', internalType: 'uint16', type: 'uint16' },
          { name: 'kiBarrierBps', internalType: 'uint16', type: 'uint16' },
          { name: 'acBarrierBps', internalType: 'uint16', type: 'uint16' },
          {
            name: 'couponBpsPerPeriod',
            internalType: 'uint16',
            type: 'uint16',
          },
          {
            name: 'timeToMaturitySecs',
            internalType: 'uint32',
            type: 'uint32',
          },
          { name: 'timeToNextObsSecs', internalType: 'uint32', type: 'uint32' },
          {
            name: 'observationsRemaining',
            internalType: 'uint8',
            type: 'uint8',
          },
          { name: 'flags', internalType: 'uint8', type: 'uint8' },
        ],
      },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'series', internalType: 'contract INoteSeries', type: 'address' },
      {
        name: 'pricer',
        internalType: 'contract ISurrogatePricer',
        type: 'address',
      },
      { name: 'volBpsAnnual', internalType: 'uint16', type: 'uint16' },
    ],
    name: 'notePriceBps',
    outputs: [
      { name: 'priceBps', internalType: 'uint16', type: 'uint16' },
      { name: 'weightsHash', internalType: 'bytes32', type: 'bytes32' },
    ],
    stateMutability: 'view',
  },
  { type: 'error', inputs: [], name: 'BadFeedAnswer' },
  {
    type: 'error',
    inputs: [{ name: 'updatedAt', internalType: 'uint40', type: 'uint40' }],
    name: 'FeedStale',
  },
  {
    type: 'error',
    inputs: [{ name: 'obsTime', internalType: 'uint40', type: 'uint40' }],
    name: 'FixingPending',
  },
  { type: 'error', inputs: [], name: 'NotLive' },
  {
    type: 'error',
    inputs: [{ name: 'field', internalType: 'uint8', type: 'uint8' }],
    name: 'Inconsistent',
  },
  {
    type: 'error',
    inputs: [
      { name: 'field', internalType: 'uint8', type: 'uint8' },
      { name: 'value', internalType: 'int64', type: 'int64' },
    ],
    name: 'OutOfRange',
  },
  {
    type: 'error',
    inputs: [{ name: 'region', internalType: 'uint8', type: 'uint8' }],
    name: 'Uncertified',
  },
] as const

//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
// NoteSeries
//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////

export const noteSeriesAbi = [
  {
    type: 'function',
    inputs: [],
    name: 'FALLBACK_GRACE',
    outputs: [{ name: '', internalType: 'uint40', type: 'uint40' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'advance',
    outputs: [
      {
        name: '',
        internalType: 'struct SeriesState',
        type: 'tuple',
        components: [
          { name: 'phase', internalType: 'enum Phase', type: 'uint8' },
          { name: 'initialFixing', internalType: 'uint96', type: 'uint96' },
          { name: 'observationsDone', internalType: 'uint8', type: 'uint8' },
          { name: 'knockedIn', internalType: 'bool', type: 'bool' },
          { name: 'autocalled', internalType: 'bool', type: 'bool' },
          { name: 'nextObservation', internalType: 'uint40', type: 'uint40' },
          { name: 'maturity', internalType: 'uint40', type: 'uint40' },
          { name: 'payoutPerNote', internalType: 'uint128', type: 'uint128' },
        ],
      },
    ],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [],
    name: 'collateral',
    outputs: [{ name: '', internalType: 'address', type: 'address' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'factory',
    outputs: [{ name: '', internalType: 'address', type: 'address' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'id',
    outputs: [{ name: '', internalType: 'bytes32', type: 'bytes32' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'maxPayoutPerNote',
    outputs: [{ name: '', internalType: 'uint128', type: 'uint128' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'amount', internalType: 'uint256', type: 'uint256' },
      { name: 'to', internalType: 'address', type: 'address' },
    ],
    name: 'mint',
    outputs: [
      { name: 'collateralIn', internalType: 'uint256', type: 'uint256' },
    ],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [],
    name: 'note',
    outputs: [{ name: '', internalType: 'address', type: 'address' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'pendingObservation',
    outputs: [
      { name: 'pending', internalType: 'bool', type: 'bool' },
      { name: 'obsTime', internalType: 'uint40', type: 'uint40' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'amount', internalType: 'uint256', type: 'uint256' }],
    name: 'previewMint',
    outputs: [
      { name: 'collateralIn', internalType: 'uint256', type: 'uint256' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'noteAmount', internalType: 'uint256', type: 'uint256' },
      { name: 'writerAmount', internalType: 'uint256', type: 'uint256' },
    ],
    name: 'previewRedeem',
    outputs: [
      { name: 'collateralOut', internalType: 'uint256', type: 'uint256' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'amount', internalType: 'uint256', type: 'uint256' }],
    name: 'previewRedeemPair',
    outputs: [
      { name: 'collateralOut', internalType: 'uint256', type: 'uint256' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'recorder',
    outputs: [
      { name: '', internalType: 'contract IFixingsRecorder', type: 'address' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'noteAmount', internalType: 'uint256', type: 'uint256' },
      { name: 'writerAmount', internalType: 'uint256', type: 'uint256' },
      { name: 'to', internalType: 'address', type: 'address' },
    ],
    name: 'redeem',
    outputs: [
      { name: 'collateralOut', internalType: 'uint256', type: 'uint256' },
    ],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [
      { name: 'amount', internalType: 'uint256', type: 'uint256' },
      { name: 'to', internalType: 'address', type: 'address' },
    ],
    name: 'redeemPair',
    outputs: [
      { name: 'collateralOut', internalType: 'uint256', type: 'uint256' },
    ],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [],
    name: 'state',
    outputs: [
      {
        name: '',
        internalType: 'struct SeriesState',
        type: 'tuple',
        components: [
          { name: 'phase', internalType: 'enum Phase', type: 'uint8' },
          { name: 'initialFixing', internalType: 'uint96', type: 'uint96' },
          { name: 'observationsDone', internalType: 'uint8', type: 'uint8' },
          { name: 'knockedIn', internalType: 'bool', type: 'bool' },
          { name: 'autocalled', internalType: 'bool', type: 'bool' },
          { name: 'nextObservation', internalType: 'uint40', type: 'uint40' },
          { name: 'maturity', internalType: 'uint40', type: 'uint40' },
          { name: 'payoutPerNote', internalType: 'uint128', type: 'uint128' },
        ],
      },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'terms',
    outputs: [
      {
        name: '',
        internalType: 'struct SeriesTerms',
        type: 'tuple',
        components: [
          { name: 'feed', internalType: 'address', type: 'address' },
          { name: 'strikeTime', internalType: 'uint40', type: 'uint40' },
          {
            name: 'observationInterval',
            internalType: 'uint32',
            type: 'uint32',
          },
          { name: 'observationCount', internalType: 'uint8', type: 'uint8' },
          { name: 'kiBarrierBps', internalType: 'uint16', type: 'uint16' },
          { name: 'acBarrierBps', internalType: 'uint16', type: 'uint16' },
          {
            name: 'couponBpsPerPeriod',
            internalType: 'uint16',
            type: 'uint16',
          },
        ],
      },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'writer',
    outputs: [{ name: '', internalType: 'address', type: 'address' }],
    stateMutability: 'view',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'caller',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      { name: 'to', internalType: 'address', type: 'address', indexed: true },
      {
        name: 'amount',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
      {
        name: 'collateralIn',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
    ],
    name: 'Minted',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      { name: 'index', internalType: 'uint8', type: 'uint8', indexed: true },
      {
        name: 'obsTime',
        internalType: 'uint40',
        type: 'uint40',
        indexed: false,
      },
      {
        name: 'fixing',
        internalType: 'uint96',
        type: 'uint96',
        indexed: false,
      },
      { name: 'knockedIn', internalType: 'bool', type: 'bool', indexed: false },
      {
        name: 'autocalled',
        internalType: 'bool',
        type: 'bool',
        indexed: false,
      },
      {
        name: 'fallbackUsed',
        internalType: 'bool',
        type: 'bool',
        indexed: false,
      },
    ],
    name: 'ObservationProcessed',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'caller',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      { name: 'to', internalType: 'address', type: 'address', indexed: true },
      {
        name: 'amount',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
      {
        name: 'collateralOut',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
    ],
    name: 'PairRedeemed',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'caller',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      { name: 'to', internalType: 'address', type: 'address', indexed: true },
      {
        name: 'noteAmount',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
      {
        name: 'writerAmount',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
      {
        name: 'collateralOut',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
    ],
    name: 'Redeemed',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'atObservation',
        internalType: 'uint8',
        type: 'uint8',
        indexed: false,
      },
      {
        name: 'autocalled',
        internalType: 'bool',
        type: 'bool',
        indexed: false,
      },
      { name: 'knockedIn', internalType: 'bool', type: 'bool', indexed: false },
      {
        name: 'payoutPerNote',
        internalType: 'uint128',
        type: 'uint128',
        indexed: false,
      },
      {
        name: 'payoutPerWriter',
        internalType: 'uint128',
        type: 'uint128',
        indexed: false,
      },
    ],
    name: 'Settled',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'initialFixing',
        internalType: 'uint96',
        type: 'uint96',
        indexed: false,
      },
    ],
    name: 'Struck',
  },
  { type: 'error', inputs: [], name: 'AlreadyInitialized' },
  { type: 'error', inputs: [], name: 'AlreadySettled' },
  { type: 'error', inputs: [], name: 'NotSettled' },
  { type: 'error', inputs: [], name: 'NotStruck' },
  { type: 'error', inputs: [], name: 'OnlyFactory' },
  { type: 'error', inputs: [], name: 'ZeroAmount' },
  {
    type: 'error',
    inputs: [
      { name: 'sender', type: 'address' },
      { name: 'balance', type: 'uint256' },
      { name: 'needed', type: 'uint256' },
    ],
    name: 'ERC20InsufficientBalance',
  },
  {
    type: 'error',
    inputs: [
      { name: 'spender', type: 'address' },
      { name: 'allowance', type: 'uint256' },
      { name: 'needed', type: 'uint256' },
    ],
    name: 'ERC20InsufficientAllowance',
  },
] as const

//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
// SeriesFactory
//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////

export const seriesFactoryAbi = [
  {
    type: 'function',
    inputs: [],
    name: 'allSeries',
    outputs: [{ name: '', internalType: 'address[]', type: 'address[]' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'collateral',
    outputs: [{ name: '', internalType: 'address', type: 'address' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      {
        name: 'terms',
        internalType: 'struct SeriesTerms',
        type: 'tuple',
        components: [
          { name: 'feed', internalType: 'address', type: 'address' },
          { name: 'strikeTime', internalType: 'uint40', type: 'uint40' },
          {
            name: 'observationInterval',
            internalType: 'uint32',
            type: 'uint32',
          },
          { name: 'observationCount', internalType: 'uint8', type: 'uint8' },
          { name: 'kiBarrierBps', internalType: 'uint16', type: 'uint16' },
          { name: 'acBarrierBps', internalType: 'uint16', type: 'uint16' },
          {
            name: 'couponBpsPerPeriod',
            internalType: 'uint16',
            type: 'uint16',
          },
        ],
      },
    ],
    name: 'createSeries',
    outputs: [{ name: 'series', internalType: 'address', type: 'address' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [{ name: 'feed', internalType: 'address', type: 'address' }],
    name: 'deployRecorder',
    outputs: [{ name: 'recorder', internalType: 'address', type: 'address' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [{ name: 'series', internalType: 'address', type: 'address' }],
    name: 'isSeries',
    outputs: [{ name: '', internalType: 'bool', type: 'bool' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      {
        name: 'terms',
        internalType: 'struct SeriesTerms',
        type: 'tuple',
        components: [
          { name: 'feed', internalType: 'address', type: 'address' },
          { name: 'strikeTime', internalType: 'uint40', type: 'uint40' },
          {
            name: 'observationInterval',
            internalType: 'uint32',
            type: 'uint32',
          },
          { name: 'observationCount', internalType: 'uint8', type: 'uint8' },
          { name: 'kiBarrierBps', internalType: 'uint16', type: 'uint16' },
          { name: 'acBarrierBps', internalType: 'uint16', type: 'uint16' },
          {
            name: 'couponBpsPerPeriod',
            internalType: 'uint16',
            type: 'uint16',
          },
        ],
      },
    ],
    name: 'predictSeries',
    outputs: [
      { name: 'series', internalType: 'address', type: 'address' },
      { name: 'note', internalType: 'address', type: 'address' },
      { name: 'writer', internalType: 'address', type: 'address' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [{ name: 'feed', internalType: 'address', type: 'address' }],
    name: 'recorderOf',
    outputs: [{ name: '', internalType: 'address', type: 'address' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      {
        name: 'terms',
        internalType: 'struct SeriesTerms',
        type: 'tuple',
        components: [
          { name: 'feed', internalType: 'address', type: 'address' },
          { name: 'strikeTime', internalType: 'uint40', type: 'uint40' },
          {
            name: 'observationInterval',
            internalType: 'uint32',
            type: 'uint32',
          },
          { name: 'observationCount', internalType: 'uint8', type: 'uint8' },
          { name: 'kiBarrierBps', internalType: 'uint16', type: 'uint16' },
          { name: 'acBarrierBps', internalType: 'uint16', type: 'uint16' },
          {
            name: 'couponBpsPerPeriod',
            internalType: 'uint16',
            type: 'uint16',
          },
        ],
      },
    ],
    name: 'seriesId',
    outputs: [{ name: '', internalType: 'bytes32', type: 'bytes32' }],
    stateMutability: 'pure',
  },
  {
    type: 'function',
    inputs: [{ name: 'seriesId', internalType: 'bytes32', type: 'bytes32' }],
    name: 'seriesOf',
    outputs: [{ name: '', internalType: 'address', type: 'address' }],
    stateMutability: 'view',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      { name: 'feed', internalType: 'address', type: 'address', indexed: true },
      {
        name: 'recorder',
        internalType: 'address',
        type: 'address',
        indexed: false,
      },
    ],
    name: 'RecorderDeployed',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'seriesId',
        internalType: 'bytes32',
        type: 'bytes32',
        indexed: true,
      },
      { name: 'feed', internalType: 'address', type: 'address', indexed: true },
      {
        name: 'series',
        internalType: 'address',
        type: 'address',
        indexed: false,
      },
      {
        name: 'note',
        internalType: 'address',
        type: 'address',
        indexed: false,
      },
      {
        name: 'writer',
        internalType: 'address',
        type: 'address',
        indexed: false,
      },
      {
        name: 'terms',
        internalType: 'struct SeriesTerms',
        type: 'tuple',
        components: [
          { name: 'feed', internalType: 'address', type: 'address' },
          { name: 'strikeTime', internalType: 'uint40', type: 'uint40' },
          {
            name: 'observationInterval',
            internalType: 'uint32',
            type: 'uint32',
          },
          { name: 'observationCount', internalType: 'uint8', type: 'uint8' },
          { name: 'kiBarrierBps', internalType: 'uint16', type: 'uint16' },
          { name: 'acBarrierBps', internalType: 'uint16', type: 'uint16' },
          {
            name: 'couponBpsPerPeriod',
            internalType: 'uint16',
            type: 'uint16',
          },
        ],
        indexed: false,
      },
    ],
    name: 'SeriesCreated',
  },
  { type: 'error', inputs: [], name: 'BadBarriers' },
  { type: 'error', inputs: [], name: 'BadCollateral' },
  { type: 'error', inputs: [], name: 'BadCoupon' },
  { type: 'error', inputs: [], name: 'BadFeed' },
  { type: 'error', inputs: [], name: 'BadSchedule' },
] as const

//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
// SeriesToken
//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////

export const seriesTokenAbi = [
  {
    type: 'function',
    inputs: [
      { name: 'owner', internalType: 'address', type: 'address' },
      { name: 'spender', internalType: 'address', type: 'address' },
    ],
    name: 'allowance',
    outputs: [{ name: '', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'spender', internalType: 'address', type: 'address' },
      { name: 'value', internalType: 'uint256', type: 'uint256' },
    ],
    name: 'approve',
    outputs: [{ name: '', internalType: 'bool', type: 'bool' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [{ name: 'account', internalType: 'address', type: 'address' }],
    name: 'balanceOf',
    outputs: [{ name: '', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'from', internalType: 'address', type: 'address' },
      { name: 'amount', internalType: 'uint256', type: 'uint256' },
    ],
    name: 'burn',
    outputs: [],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [],
    name: 'decimals',
    outputs: [{ name: '', internalType: 'uint8', type: 'uint8' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'isNote',
    outputs: [{ name: '', internalType: 'bool', type: 'bool' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'to', internalType: 'address', type: 'address' },
      { name: 'amount', internalType: 'uint256', type: 'uint256' },
    ],
    name: 'mint',
    outputs: [],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [],
    name: 'name',
    outputs: [{ name: '', internalType: 'string', type: 'string' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'series',
    outputs: [{ name: '', internalType: 'address', type: 'address' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'symbol',
    outputs: [{ name: '', internalType: 'string', type: 'string' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'totalSupply',
    outputs: [{ name: '', internalType: 'uint256', type: 'uint256' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      { name: 'to', internalType: 'address', type: 'address' },
      { name: 'value', internalType: 'uint256', type: 'uint256' },
    ],
    name: 'transfer',
    outputs: [{ name: '', internalType: 'bool', type: 'bool' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'function',
    inputs: [
      { name: 'from', internalType: 'address', type: 'address' },
      { name: 'to', internalType: 'address', type: 'address' },
      { name: 'value', internalType: 'uint256', type: 'uint256' },
    ],
    name: 'transferFrom',
    outputs: [{ name: '', internalType: 'bool', type: 'bool' }],
    stateMutability: 'nonpayable',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      {
        name: 'owner',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'spender',
        internalType: 'address',
        type: 'address',
        indexed: true,
      },
      {
        name: 'value',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
    ],
    name: 'Approval',
  },
  {
    type: 'event',
    anonymous: false,
    inputs: [
      { name: 'from', internalType: 'address', type: 'address', indexed: true },
      { name: 'to', internalType: 'address', type: 'address', indexed: true },
      {
        name: 'value',
        internalType: 'uint256',
        type: 'uint256',
        indexed: false,
      },
    ],
    name: 'Transfer',
  },
  { type: 'error', inputs: [], name: 'AlreadyInitialized' },
  { type: 'error', inputs: [], name: 'OnlyFactory' },
  { type: 'error', inputs: [], name: 'OnlySeries' },
] as const

//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
// SurrogatePricer
//////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////

export const surrogatePricerAbi = [
  {
    type: 'function',
    inputs: [{ name: 'field', internalType: 'uint8', type: 'uint8' }],
    name: 'certifiedRange',
    outputs: [
      { name: 'min', internalType: 'int64', type: 'int64' },
      { name: 'max', internalType: 'int64', type: 'int64' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'featureSpecVersion',
    outputs: [{ name: '', internalType: 'uint16', type: 'uint16' }],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [
      {
        name: 'inputs',
        internalType: 'struct PricerInputs',
        type: 'tuple',
        components: [
          { name: 'spotBpsOfInitial', internalType: 'uint16', type: 'uint16' },
          { name: 'distToKnockInBps', internalType: 'int32', type: 'int32' },
          { name: 'volBpsAnnual', internalType: 'uint16', type: 'uint16' },
          { name: 'kiBarrierBps', internalType: 'uint16', type: 'uint16' },
          { name: 'acBarrierBps', internalType: 'uint16', type: 'uint16' },
          {
            name: 'couponBpsPerPeriod',
            internalType: 'uint16',
            type: 'uint16',
          },
          {
            name: 'timeToMaturitySecs',
            internalType: 'uint32',
            type: 'uint32',
          },
          { name: 'timeToNextObsSecs', internalType: 'uint32', type: 'uint32' },
          {
            name: 'observationsRemaining',
            internalType: 'uint8',
            type: 'uint8',
          },
          { name: 'flags', internalType: 'uint8', type: 'uint8' },
        ],
      },
    ],
    name: 'priceBps',
    outputs: [
      { name: 'priceBpsOfNotional', internalType: 'uint16', type: 'uint16' },
    ],
    stateMutability: 'view',
  },
  {
    type: 'function',
    inputs: [],
    name: 'weightsHash',
    outputs: [{ name: '', internalType: 'bytes32', type: 'bytes32' }],
    stateMutability: 'view',
  },
  {
    type: 'error',
    inputs: [{ name: 'field', internalType: 'uint8', type: 'uint8' }],
    name: 'Inconsistent',
  },
  {
    type: 'error',
    inputs: [
      { name: 'field', internalType: 'uint8', type: 'uint8' },
      { name: 'value', internalType: 'int64', type: 'int64' },
    ],
    name: 'OutOfRange',
  },
  {
    type: 'error',
    inputs: [{ name: 'region', internalType: 'uint8', type: 'uint8' }],
    name: 'Uncertified',
  },
] as const
