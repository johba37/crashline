//! Crashline — Stylus student model.
//!
//! A pure function from a note's state to its fair value in bps of notional:
//! an integer MLP distilled from an off-chain Monte Carlo teacher, with the
//! weights compiled in and pinned by `weightsHash()`. No storage, no external
//! calls, no `block.timestamp`: time enters only through the inputs.
//!
//! ```solidity
//! interface ISurrogatePricer {
//!     error OutOfRange(uint8 field, int64 value);
//!     error Uncertified(uint8 region);
//!     error Inconsistent(uint8 field);
//!     function priceBps(PricerInputs calldata in_) external view returns (uint16);
//!     function certifiedRange(uint8 field) external view returns (int64 min, int64 max);
//!     function weightsHash() external view returns (bytes32);
//!     function featureSpecVersion() external view returns (uint16);
//! }
//! ```
//! `PricerInputs` is the static struct in NoteQuoter.sol; its ABI encoding is
//! the 10-tuple taken by `price_bps` below.

#![cfg_attr(not(any(test, feature = "export-abi")), no_main)]
extern crate alloc;

pub mod engine;

use alloy_primitives::FixedBytes;
use alloy_sol_types::sol;
use stylus_sdk::prelude::*;

pub const FEATURE_SPEC_VERSION: u16 = 1;

sol! {
    /// Input `field` (index in PricerInputs) is outside the certified range.
    #[derive(Debug, PartialEq, Eq)]
    error OutOfRange(uint8 field, int64 value);
    /// The inputs fall inside excluded region `region` of the certified domain.
    #[derive(Debug, PartialEq, Eq)]
    error Uncertified(uint8 region);
    /// Derived field `field` doesn't match the others (1 = dist, 6 = ttm).
    #[derive(Debug, PartialEq, Eq)]
    error Inconsistent(uint8 field);
}

#[derive(SolidityError, Debug, PartialEq, Eq)]
pub enum PricerError {
    OutOfRange(OutOfRange),
    Uncertified(Uncertified),
    Inconsistent(Inconsistent),
}

impl From<engine::Refusal> for PricerError {
    fn from(r: engine::Refusal) -> Self {
        match r {
            engine::Refusal::OutOfRange { field, value } => PricerError::OutOfRange(OutOfRange { field, value }),
            engine::Refusal::Uncertified(region) => PricerError::Uncertified(Uncertified { region }),
            engine::Refusal::Inconsistent(field) => PricerError::Inconsistent(Inconsistent { field }),
        }
    }
}

/// (spotBpsOfInitial, distToKnockInBps, volBpsAnnual, kiBarrierBps,
///  acBarrierBps, couponBpsPerPeriod, timeToMaturitySecs, timeToNextObsSecs,
///  observationsRemaining, flags)
pub type PricerInputs = (u16, i32, u16, u16, u16, u16, u32, u32, u8, u8);

#[storage]
#[entrypoint]
pub struct SurrogatePricer {}

#[public]
impl SurrogatePricer {
    /// Clean fair value of the note in bps of notional (excludes coupon
    /// accrued before now). Reverts outside the certified domain instead of
    /// extrapolating: `OutOfRange`, `Inconsistent` or `Uncertified`.
    pub fn price_bps(&self, inputs: PricerInputs) -> Result<u16, PricerError> {
        let raw = [
            inputs.0 as i64,
            inputs.1 as i64,
            inputs.2 as i64,
            inputs.3 as i64,
            inputs.4 as i64,
            inputs.5 as i64,
            inputs.6 as i64,
            inputs.7 as i64,
            inputs.8 as i64,
            inputs.9 as i64,
        ];
        engine::price_bps(&raw).map_err(PricerError::from)
    }

    /// Certified range of input `field` (min == max for a pinned note term).
    /// Reverts `OutOfRange(field, field)` for an unknown field index.
    pub fn certified_range(&self, field: u8) -> Result<(i64, i64), PricerError> {
        let i = field as usize;
        if i >= engine::NUM_FEATURES {
            return Err(PricerError::OutOfRange(OutOfRange { field, value: field as i64 }));
        }
        Ok((engine::DOMAIN_MIN[i], engine::DOMAIN_MAX[i]))
    }

    /// keccak256 of the canonical student_export.json this contract was built
    /// from (recomputed at build time, not copied).
    pub fn weights_hash(&self) -> FixedBytes<32> {
        FixedBytes(engine::WEIGHTS_HASH)
    }

    pub fn feature_spec_version(&self) -> u16 {
        FEATURE_SPEC_VERSION
    }
}
