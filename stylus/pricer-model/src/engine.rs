//! Certified-domain check + integer forward pass. Bit-exact twin of
//! `tools/pricer_quant.py::forward`; the golden vectors are the contract
//! between the two.
//!
//! No floats, no allocation, no storage: every constant is compiled in from
//! the export by build.rs, and i64 overflow is ruled out there for all
//! in-range inputs.

pub struct Layer {
    pub inputs: usize,
    pub outputs: usize,
    /// Row-major, output-channel-major: `w[i * inputs + j]`.
    pub w: &'static [i16],
    pub b: &'static [i64],
    /// Per-output-channel requantization multiplier (Q16 mantissa).
    pub m: &'static [i64],
    /// Per-output-channel total right shift (16 + requantShift).
    pub s: &'static [u32],
}

include!(concat!(env!("OUT_DIR"), "/model.rs"));

/// Why the model refuses. Never clamped: the model only speaks where it was
/// certified against the teacher.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Refusal {
    /// `field` outside its certified range.
    OutOfRange { field: u8, value: i64 },
    /// Inside excluded region `region` (e.g. the autocall observation-day band).
    Uncertified(u8),
    /// A derived field doesn't match the others (1 = dist, 6 = ttm).
    Inconsistent(u8),
}

/// Derived fields of feature spec 1 (spec 2 has none).
#[cfg(not(perp))]
fn check_derived(raw: &[i64; NUM_FEATURES]) -> Result<(), Refusal> {
    const SPOT: usize = 0;
    const DIST: usize = 1;
    const KI: usize = 3;
    const TTM: usize = 6;
    const TNEXT: usize = 7;
    const OBS: usize = 8;
    if CHECK_DIST && raw[DIST] != raw[SPOT] - raw[KI] {
        return Err(Refusal::Inconsistent(DIST as u8));
    }
    if OBS_INTERVAL != 0 && raw[TTM] != raw[TNEXT] + raw[OBS] * OBS_INTERVAL {
        return Err(Refusal::Inconsistent(TTM as u8));
    }
    Ok(())
}

#[cfg(perp)]
fn check_derived(_raw: &[i64; NUM_FEATURES]) -> Result<(), Refusal> {
    Ok(())
}

/// Order is binding (mirrors `pq.check_domain`): ranges by field index, then
/// derived fields (dist, then ttm), then exclusions in order.
pub fn check_domain(raw: &[i64; NUM_FEATURES]) -> Result<(), Refusal> {
    for i in 0..NUM_FEATURES {
        if raw[i] < DOMAIN_MIN[i] || raw[i] > DOMAIN_MAX[i] {
            return Err(Refusal::OutOfRange { field: i as u8, value: raw[i] });
        }
    }
    check_derived(raw)?;
    for (k, bounds) in EXCLUSIONS.iter().enumerate() {
        if bounds.iter().all(|&(f, lo, hi)| raw[f] >= lo && raw[f] <= hi) {
            return Err(Refusal::Uncertified(k as u8));
        }
    }
    Ok(())
}

/// `p / 2^s`, rounded half away from zero (sign-magnitude, so negatives round
/// symmetrically).
#[inline]
pub fn round_shift(p: i64, s: u32) -> i64 {
    let half = 1i64 << (s - 1);
    if p >= 0 { (p + half) >> s } else { -((-p + half) >> s) }
}

/// Raw units -> [-QMAX, QMAX]. The certified domain is inside the
/// normalization ranges (build.rs), so after `check_domain` this can't fail;
/// the check stays as defense in depth.
pub fn normalize(raw: &[i64; NUM_FEATURES]) -> Result<[i64; NUM_FEATURES], Refusal> {
    let mut x = [0i64; NUM_FEATURES];
    for i in 0..NUM_FEATURES {
        let (lo, hi, v) = (FIELD_MIN[i], FIELD_MAX[i], raw[i]);
        if v < lo || v > hi {
            return Err(Refusal::OutOfRange { field: i as u8, value: v });
        }
        let range = hi - lo;
        x[i] = ((v - lo) * 2 * QMAX + range / 2) / range - QMAX;
    }
    Ok(x)
}

/// Clean fair value in bps of notional, clamped to [0, 65535] (feature spec 1).
#[cfg(not(perp))]
pub fn price_bps(raw: &[i64; NUM_FEATURES]) -> Result<u16, Refusal> {
    forward(raw).map(|p| p as u16)
}

/// Correction to the closed form's principal in bps of live notional, clamped
/// to the int16 range (feature spec 2).
#[cfg(perp)]
pub fn correction_bps(raw: &[i64; NUM_FEATURES]) -> Result<i16, Refusal> {
    forward(raw).map(|p| p as i16)
}

/// Domain check, then the integer forward pass; the head is clamped to
/// [OUTPUT_MIN, OUTPUT_MAX], the return type of the feature spec.
pub fn forward(raw: &[i64; NUM_FEATURES]) -> Result<i64, Refusal> {
    check_domain(raw)?;
    let x0 = normalize(raw)?;
    let mut a = [0i64; MAX_WIDTH];
    let mut b = [0i64; MAX_WIDTH];
    a[..NUM_FEATURES].copy_from_slice(&x0);

    let (hidden, head) = LAYERS.split_at(LAYERS.len() - 1);
    for layer in hidden {
        for i in 0..layer.outputs {
            let acc = dot(layer, i, &a).max(0); // relu
            let y = round_shift(acc * layer.m[i], layer.s[i]);
            b[i] = y.clamp(-QMAX - 1, QMAX);
        }
        core::mem::swap(&mut a, &mut b);
    }
    let head = &head[0];
    let acc = dot(head, 0, &a);
    let price = round_shift(acc * head.m[0], head.s[0]) + OUTPUT_OFFSET_BPS;
    Ok(price.clamp(OUTPUT_MIN, OUTPUT_MAX))
}

#[inline]
fn dot(layer: &Layer, i: usize, x: &[i64; MAX_WIDTH]) -> i64 {
    let row = &layer.w[i * layer.inputs..(i + 1) * layer.inputs];
    let mut acc = layer.b[i];
    for (w, v) in row.iter().zip(&x[..layer.inputs]) {
        acc += *w as i64 * v;
    }
    acc
}
