//! Surrogate Pricer — Stylus student model.
//!
//! A pure function from a note's state to its fair value in bps of notional:
//! an integer MLP distilled from an off-chain Monte Carlo teacher, with the
//! weights compiled in and pinned by `weightsHash()`. No storage, no external
//! calls, no `block.timestamp`: time enters only through the inputs.
//!
//! The export's `featureSpecVersion` decides which contract this crate builds
//! (build.rs sets `cfg(perp)` for 2).
//!
//! Feature spec 1, the v1 autocallable:
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
//! `PricerInputs` is the static struct in ISurrogatePricer.sol; its ABI
//! encoding is the 10-tuple taken by `price_bps` below.
//!
//! Feature spec 2, the v2 perpetual note (docs/v2-spec.md): the model returns
//! a signed correction to the closed form that PerpQuoter computes, and names
//! the product it is pinned to.
//! ```solidity
//! interface IPerpPricer {
//!     error OutOfRange(uint8 field, int64 value);
//!     error Uncertified(uint8 region);
//!     function correctionBps(PerpPricerInputs calldata in_) external view returns (int16);
//!     function answer(PerpPricerInputs calldata in_) external view returns (int16, PerpProduct memory, bytes32);
//!     function certifiedRange(uint8 field) external view returns (int64 min, int64 max);
//!     function product() external view returns (PerpProduct memory);
//!     function weightsHash() external view returns (bytes32);
//!     function featureSpecVersion() external view returns (uint16);
//! }
//! ```
//! `PerpPricerInputs` and `PerpProduct` are static structs (IPerpPricer.sol):
//! their ABI encodings are the 5-tuples below.

#![cfg_attr(not(any(test, feature = "export-abi")), no_main)]
extern crate alloc;

pub mod engine;

use alloy_primitives::FixedBytes;
use alloy_sol_types::sol;
use stylus_sdk::prelude::*;

pub const FEATURE_SPEC_VERSION: u16 = engine::FEATURE_SPEC_VERSION;

sol! {
    /// Input `field` (index in the inputs struct) is outside the certified range.
    #[derive(Debug, PartialEq, Eq)]
    error OutOfRange(uint8 field, int64 value);
    /// The inputs fall inside excluded region `region` of the certified domain.
    #[derive(Debug, PartialEq, Eq)]
    error Uncertified(uint8 region);
    /// Derived field `field` doesn't match the others (1 = dist, 6 = ttm; feature spec 1 only).
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

/// Certified range of input `field`; `OutOfRange(field, field)` for an unknown index.
fn certified_range(field: u8) -> Result<(i64, i64), PricerError> {
    let i = field as usize;
    if i >= engine::NUM_FEATURES {
        return Err(PricerError::OutOfRange(OutOfRange { field, value: field as i64 }));
    }
    Ok((engine::DOMAIN_MIN[i], engine::DOMAIN_MAX[i]))
}

// --- feature spec 1: the v1 autocallable ------------------------------------------

/// (spotBpsOfInitial, distToKnockInBps, volBpsAnnual, kiBarrierBps,
///  acBarrierBps, couponBpsPerPeriod, timeToMaturitySecs, timeToNextObsSecs,
///  observationsRemaining, flags)
#[cfg(not(perp))]
pub type PricerInputs = (u16, i32, u16, u16, u16, u16, u32, u32, u8, u8);

#[cfg(not(perp))]
#[storage]
#[entrypoint]
pub struct SurrogatePricer {}

#[cfg(not(perp))]
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
        certified_range(field)
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

// --- feature spec 2: the v2 perpetual note -----------------------------------------

/// (spotBpsOfReference, volBpsAnnual, timeToNextFixingSecs, fixingsBeforeEarnings, flags)
#[cfg(perp)]
pub type PerpPricerInputs = (u16, u16, u32, u8, u8);

/// (kiBarrierBps, meltShare, fixingInterval, driftBps, discountBps)
#[cfg(perp)]
pub type PerpProduct = (u16, u64, u32, i16, i16);

#[cfg(perp)]
#[storage]
#[entrypoint]
pub struct PerpPricer {}

#[cfg(perp)]
#[public]
impl PerpPricer {
    /// Correction to the closed form's principal, in bps of live notional.
    /// Reverts outside the certified domain instead of extrapolating:
    /// `OutOfRange` or `Uncertified`.
    pub fn correction_bps(&self, inputs: PerpPricerInputs) -> Result<i16, PricerError> {
        let raw = [inputs.0 as i64, inputs.1 as i64, inputs.2 as i64, inputs.3 as i64, inputs.4 as i64];
        engine::correction_bps(&raw).map_err(PricerError::from)
    }

    /// The correction with the product and the weights hash: one call gives PerpQuoter all
    /// it needs. ABI: (int16, PerpProduct, bytes32); a static struct encodes as its fields.
    #[allow(clippy::type_complexity)]
    pub fn answer(
        &self,
        inputs: PerpPricerInputs,
    ) -> Result<(i16, u16, u64, u32, i16, i16, FixedBytes<32>), PricerError> {
        let correction = self.correction_bps(inputs)?;
        let p = self.product();
        Ok((correction, p.0, p.1, p.2, p.3, p.4, self.weights_hash()))
    }

    /// Certified range of input `field`.
    /// Reverts `OutOfRange(field, field)` for an unknown field index.
    pub fn certified_range(&self, field: u8) -> Result<(i64, i64), PricerError> {
        certified_range(field)
    }

    /// The product this model is pinned to, and the rates of its closed form.
    pub fn product(&self) -> PerpProduct {
        (
            engine::PRODUCT_KI_BPS,
            engine::PRODUCT_MELT_SHARE,
            engine::PRODUCT_FIXING_INTERVAL,
            engine::PRODUCT_DRIFT_BPS,
            engine::PRODUCT_DISCOUNT_BPS,
        )
    }

    /// keccak256 of the canonical student_export.json this contract was built
    /// from (recomputed at build time, not copied): weights, domain and product.
    pub fn weights_hash(&self) -> FixedBytes<32> {
        FixedBytes(engine::WEIGHTS_HASH)
    }

    pub fn feature_spec_version(&self) -> u16 {
        FEATURE_SPEC_VERSION
    }
}
