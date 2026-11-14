//! What to burn the dictator key to.
//!
//! The config contract checks the dictator's signature with `CHKSIGNU`, which on ever-node is
//! ed25519-dalek 2's `VerifyingKey::from_bytes` + `verify` (ever-vm `executor/crypto.rs`, ever-block
//! `wrappers.rs`). `verify` is the non-strict check: it does not reject small-order keys.
//!
//! - Key 0 (what the contract sets for a `-999` proposal without a value) decodes to a point of order 4.
//!   For such a key `R = identity, s = 0` is a valid signature of roughly every fourth message, so anyone
//!   could forge dictator commands by varying `valid_until`. Burning to 0 hands the key to everybody.
//! - A 32-byte string that is not the encoding of any curve point fails `from_bytes`, so `CHKSIGNU`
//!   returns false (or throws, before `CapsTvmBugfixes2022`) for every signature: nobody can ever sign.
//!
//! The burn key is derived from a public label, so anyone can check that nobody chose it to match a
//! secret: `sha256(label ":" counter)` for the first counter whose hash is not a curve point.

use ed25519_dalek::VerifyingKey;
use sha2::{Digest, Sha256};
use tycho_types::cell::HashBytes;

pub const DEFAULT_LABEL: &str = "it is a fight club baby";

/// Derives the burn key for `label`; returns the key and the counter that produced it.
pub fn derive(label: &str) -> (HashBytes, u32) {
    (0u32..)
        .map(|i| {
            let mut h = Sha256::new();
            h.update(label.as_bytes());
            h.update(format!(":{i}").as_bytes());
            (HashBytes(h.finalize().into()), i)
        })
        .find(|(key, _)| VerifyingKey::from_bytes(key.as_array()).is_err())
        .expect("about half of all hashes are not curve points")
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KeyKind {
    /// not a curve point: no signature verifies
    Unusable,
    /// a small-order point: signatures can be forged without any secret
    Weak,
    /// an ordinary key: whoever holds its secret controls the config
    Usable,
}

pub fn classify(key: &HashBytes) -> KeyKind {
    match VerifyingKey::from_bytes(key.as_array()) {
        Err(_) => KeyKind::Unusable,
        Ok(k) if k.is_weak() => KeyKind::Weak,
        Ok(_) => KeyKind::Usable,
    }
}

impl std::fmt::Display for KeyKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            KeyKind::Unusable => "burned (not a curve point: no signature can ever verify)",
            KeyKind::Weak => "WEAK (small-order point: signatures can be forged without a secret)",
            KeyKind::Usable => "active (an ordinary ed25519 key: its holder controls the config)",
        })
    }
}

#[cfg(test)]
mod tests {
    use ed25519_dalek::{Signature, Verifier};

    use super::*;

    #[test]
    fn burn_key_is_not_a_point() {
        let (key, _) = derive(DEFAULT_LABEL);
        assert_eq!(classify(&key), KeyKind::Unusable);
        assert_eq!(derive(DEFAULT_LABEL), derive(DEFAULT_LABEL));
    }

    /// Key 0 accepts forged signatures under the same `verify` call ever-vm makes.
    #[test]
    fn zero_key_is_forgeable() {
        let zero = VerifyingKey::from_bytes(&[0u8; 32]).expect("0 decodes to a point");
        assert_eq!(classify(&HashBytes::ZERO), KeyKind::Weak);

        // R = identity (y = 1), s = 0: valid whenever H(R, A, m) is divisible by 4
        let mut forged = [0u8; 64];
        forged[0] = 1;
        let sig = Signature::from_bytes(&forged);
        let accepted = (0u32..64)
            .filter(|i| zero.verify(format!("set config param, valid_until {i}").as_bytes(), &sig).is_ok())
            .count();
        assert!(accepted > 0, "no forgery among 64 messages");
        // the strict check rejects small-order keys, but the TVM does not use it
        assert!(zero.verify_strict(b"x", &sig).is_err());
    }
}
