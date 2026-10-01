//! Golden-vector CI: the compiled contract must reproduce the quantized Python
//! reference exactly (no tolerance) for every model vector, and refuse every
//! reject vector with exactly the stated error. Runs against whichever model
//! PRICER_MODEL_DIR selects (default model/k3; also run model/k2, model/k1-r1 and model/synthetic).

use serde_json::Value;
use crashline_pricer_model::{
    Inconsistent, OutOfRange, PricerError, SurrogatePricer, Uncertified,
    engine::{self, Refusal},
};

const FIELDS: [&str; 10] = [
    "spotBpsOfInitial",
    "distToKnockInBps",
    "volBpsAnnual",
    "kiBarrierBps",
    "acBarrierBps",
    "couponBpsPerPeriod",
    "timeToMaturitySecs",
    "timeToNextObsSecs",
    "observationsRemaining",
    "flags",
];

fn model_dir() -> &'static str {
    env!("PRICER_MODEL_DIR")
}

fn load(name: &str) -> Value {
    let path = format!("{}/{name}", model_dir());
    serde_json::from_str(&std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{path}: {e}"))).unwrap()
}

fn raw(features: &Value) -> [i64; 10] {
    FIELDS.map(|f| features[f].as_i64().unwrap_or_else(|| panic!("missing {f}")))
}

fn tuple(r: &[i64; 10]) -> (u16, i32, u16, u16, u16, u16, u32, u32, u8, u8) {
    (
        r[0] as u16, r[1] as i32, r[2] as u16, r[3] as u16, r[4] as u16,
        r[5] as u16, r[6] as u32, r[7] as u32, r[8] as u8, r[9] as u8,
    )
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

#[test]
fn vectors_belong_to_compiled_model() {
    let v = load("golden_vectors.json");
    assert_eq!(v["weightsHash"].as_str().unwrap(), format!("0x{}", hex(&engine::WEIGHTS_HASH)));
}

#[test]
fn model_vectors_exact() {
    let v = load("golden_vectors.json");
    let rows = v["modelVectors"].as_array().unwrap();
    assert_eq!(rows.len(), 100);
    for (n, row) in rows.iter().enumerate() {
        let want = row["expectedPriceBps"].as_u64().unwrap() as u16;
        assert_eq!(engine::price_bps(&raw(&row["features"])), Ok(want), "vector {n}");
    }
}

#[test]
fn reject_vectors_fail_closed() {
    let v = load("golden_vectors.json");
    let rows = v["rejectVectors"].as_array().unwrap();
    assert!(!rows.is_empty());
    for (n, row) in rows.iter().enumerate() {
        let r = raw(&row["features"]);
        let index = row["index"].as_u64().unwrap() as u8;
        let want = match row["error"].as_str().unwrap() {
            "OutOfRange" => Refusal::OutOfRange { field: index, value: r[index as usize] },
            "Uncertified" => Refusal::Uncertified(index),
            "Inconsistent" => Refusal::Inconsistent(index),
            e => panic!("unknown error kind {e}"),
        };
        assert_eq!(engine::price_bps(&r), Err(want), "vector {n}");
    }
}

#[test]
fn certified_range_matches_export() {
    use stylus_sdk::testing::*;
    let vm = TestVM::default();
    let c = SurrogatePricer::from(&vm);
    let export = load("student_export.json");
    for (i, r) in export["certifiedDomain"]["ranges"].as_array().unwrap().iter().enumerate() {
        let want = (r["min"].as_i64().unwrap(), r["max"].as_i64().unwrap());
        assert_eq!(c.certified_range(i as u8).unwrap(), want, "field {i}");
    }
    assert!(c.certified_range(10).is_err());
}

#[test]
fn contract_abi_path_matches_engine() {
    use stylus_sdk::testing::*;
    let vm = TestVM::default();
    let c = SurrogatePricer::from(&vm);
    let v = load("golden_vectors.json");
    for row in v["modelVectors"].as_array().unwrap().iter().take(10) {
        let r = raw(&row["features"]);
        assert_eq!(c.price_bps(tuple(&r)).unwrap() as u64, row["expectedPriceBps"].as_u64().unwrap());
    }
    // every refusal kind surfaces as its Solidity error through the ABI path
    for row in v["rejectVectors"].as_array().unwrap() {
        let r = raw(&row["features"]);
        let index = row["index"].as_u64().unwrap() as u8;
        let want = match row["error"].as_str().unwrap() {
            "OutOfRange" => PricerError::OutOfRange(OutOfRange { field: index, value: r[index as usize] }),
            "Uncertified" => PricerError::Uncertified(Uncertified { region: index }),
            _ => PricerError::Inconsistent(Inconsistent { field: index }),
        };
        // values beyond the ABI types can't be encoded; the vectors never contain them
        assert_eq!(c.price_bps(tuple(&r)), Err(want));
    }
    assert_eq!(c.weights_hash().0, engine::WEIGHTS_HASH);
}

#[test]
fn rounding_is_symmetric() {
    assert_eq!(engine::round_shift(-1, 2), 0); // -0.25 -> 0 (a floor-shift gives -1)
    assert_eq!(engine::round_shift(-2, 2), -1); // -0.5 -> -1 (away from zero)
    assert_eq!(engine::round_shift(2, 2), 1); // 0.5 -> 1
    assert_eq!(engine::round_shift(-3, 1), -2);
    assert_eq!(engine::round_shift(5, 3), 1);
}
