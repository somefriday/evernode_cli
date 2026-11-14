//! Wallets that pay for proposals and carry votes as internal messages:
//! - EverWallet (`ever_wallet`): one key; a new wallet needs only funds, its first message deploys it;
//! - SafeMultisig and its variants (`multisig`): any number of custodians and required confirmations.
//!
//! Every wallet message is dry-run on the wallet's own code and current state, sent through the endpoint,
//! and followed to its transaction before the next one goes out: replay protection rejects a message once
//! a newer one from the same wallet has executed (exit 52).

pub mod ever_wallet;
mod multisig;

use std::path::{Path, PathBuf};

use anyhow::{Context, Result, ensure};
use ed25519_dalek::SigningKey;
use serde::Deserialize;
use tycho_types::models::StdAddr;
use tycho_types::prelude::*;

use crate::config::ConfigAccount;
use crate::graphql::{Executed, Graphql, Sent};
use crate::{KeyFile, now};

/// The path ever-cli and ever-sdk derive a wallet key along, from a seed phrase.
const DERIVATION_PATH: &str = "m/44'/396'/0'/0/0";

#[derive(Deserialize, Clone, Copy, PartialEq, Eq, Debug)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    EverWallet,
    /// SafeMultisig, SetcodeMultisig or SurfMultisig: their transfer and getter functions are the same
    Multisig,
}

/// A wallet file: the migration tooling's layout (`name, type, address, public, secret`), or, for several
/// keys, `keys`: key files (`{"public", "secret"}`, as ever-cli and evernode write them), relative to it,
/// and `phrases`: seed phrases. Without `type`, it is the EverWallet of its key if the address is that
/// wallet's, and a multisig otherwise.
#[derive(Deserialize)]
pub struct File {
    name: String,
    #[serde(rename = "type")]
    kind: Option<Kind>,
    address: StdAddr,
    public: Option<HashBytes>,
    secret: Option<HashBytes>,
    #[serde(default)]
    keys: Vec<PathBuf>,
    #[serde(default)]
    phrases: Vec<String>,
}

/// A wallet in the net file: the path of a wallet file, or the file's content in its place.
#[derive(Deserialize)]
#[serde(untagged)]
pub enum Source {
    Path(PathBuf),
    Inline(File),
}

impl Source {
    /// Makes relative paths relative to `dir`, the net file's directory.
    pub fn resolve(&mut self, dir: &Path) {
        match self {
            Source::Path(path) => *path = dir.join(&*path),
            Source::Inline(file) => file.keys.iter_mut().for_each(|key| *key = dir.join(&*key)),
        }
    }
}

pub struct Wallet {
    pub name: String,
    pub kind: Kind,
    pub address: StdAddr,
    /// the keys this file holds: the EverWallet's, or some of a multisig's custodians'
    keys: Vec<SigningKey>,
}

/// A signed external message to a wallet: its `time` header (ms) and expiry.
pub struct Message {
    pub cell: Cell,
    pub time: u64,
    pub expire_at: u32,
}

/// How far a transfer got.
#[derive(Debug, PartialEq, Eq)]
pub enum Transfer {
    /// the wallet sent the internal message, with this hash
    Sent(HashBytes),
    /// a multisig transaction waits for confirmations from custodians whose keys are not here
    Pending { id: u64, received: u8, required: u8 },
}

impl Wallet {
    pub fn load(path: &Path) -> Result<Self> {
        let file: File = serde_json::from_slice(&std::fs::read(path).with_context(|| format!("read {}", path.display()))?)
            .with_context(|| format!("parse {}", path.display()))?;
        Self::from_file(&file, path.parent().unwrap_or(Path::new(".")))
    }

    /// The wallet a net file names (its paths already resolved).
    pub fn open(source: &Source) -> Result<Self> {
        match source {
            Source::Path(path) => Self::load(path),
            Source::Inline(file) => Self::from_file(file, Path::new("")),
        }
    }

    fn from_file(file: &File, dir: &Path) -> Result<Self> {
        let mut keys = Vec::new();
        if let Some(secret) = file.secret {
            let key = SigningKey::from_bytes(secret.as_array());
            ensure!(file.public.is_none_or(|p| p.0 == key.verifying_key().to_bytes()), "{}: secret does not match public key", file.name);
            keys.push(key);
        }
        for key_path in &file.keys {
            let key_path = dir.join(key_path);
            let kf: KeyFile = serde_json::from_slice(&std::fs::read(&key_path).with_context(|| format!("read {}", key_path.display()))?)
                .with_context(|| format!("parse {}", key_path.display()))?;
            let key = SigningKey::from_bytes(kf.secret.as_array());
            ensure!(key.verifying_key().to_bytes() == kf.public.0, "{}: secret does not match public key", key_path.display());
            keys.push(key);
        }
        for (i, phrase) in file.phrases.iter().enumerate() {
            keys.push(key_from_phrase(phrase).with_context(|| format!("{}: seed phrase {}", file.name, i + 1))?);
        }
        ensure!(!keys.is_empty(), "{}: no key (secret, keys: key files, or phrases)", file.name);
        let kind = match file.kind {
            Some(kind) => kind,
            None if keys.len() == 1 && ever_wallet::address(&HashBytes(keys[0].verifying_key().to_bytes()))? == file.address => Kind::EverWallet,
            None => Kind::Multisig,
        };
        ensure!(kind == Kind::Multisig || keys.len() == 1, "{}: an EverWallet has one key", file.name);
        Ok(Self { name: file.name.clone(), kind, address: file.address.clone(), keys })
    }

    pub fn key(&self) -> &SigningKey {
        &self.keys[0]
    }

    /// Sends `value` with `payload` to `dest` (bounceable), as far as the keys here allow.
    pub fn transfer(&self, chain: &Graphql, cfg: &ConfigAccount, dest: &StdAddr, value: u128, payload: Cell) -> Result<Transfer> {
        match self.kind {
            Kind::EverWallet => ever_wallet::transfer(self, chain, cfg, dest, value, payload),
            Kind::Multisig => multisig::transfer(self, chain, cfg, dest, value, payload),
        }
    }
}

/// The key ever-cli and ever-sdk derive from a seed phrase: BIP39 (English, 12 to 24 words, checksum
/// checked), BIP32 on secp256k1 along [`DERIVATION_PATH`], and the private key found there taken as the
/// ed25519 secret. The phrase never appears in an error.
fn key_from_phrase(phrase: &str) -> Result<SigningKey> {
    let mnemonic = bip39::Mnemonic::parse_in_normalized(bip39::Language::English, phrase).map_err(|e| anyhow::anyhow!("not a BIP39 phrase: {e}"))?;
    let path = DERIVATION_PATH.parse::<bip32::DerivationPath>().map_err(|e| anyhow::anyhow!("{DERIVATION_PATH}: {e}"))?;
    let derived = bip32::XPrv::derive_from_path(mnemonic.to_seed(""), &path).map_err(|e| anyhow::anyhow!("derivation: {e}"))?;
    Ok(SigningKey::from_bytes(&derived.private_key().to_bytes().into()))
}

/// One wallet message: dry-run on `account` (the wallet's current state), sent, and followed to its
/// transaction, which must succeed.
fn execute(chain: &Graphql, cfg: &ConfigAccount, wallet: &Wallet, account: &Cell, message: Message, dest: &StdAddr) -> Result<Executed> {
    let (dry, _) = crate::emulate::run_external(cfg, account, &message.cell, now() as u32)?;
    ensure!(dry.accepted && dry.success, "dry run: {} fails with exit code {}{}", wallet.name, dry.exit_code, meaning(dry.exit_code));
    // the wallets send with "ignore errors": a transfer the balance does not cover is dropped, not failed
    ensure!(!dry.dropped, "dry run: {} would send nothing: its balance does not cover the transfer and its fees", wallet.name);
    let sent = Sent { hash: *message.cell.repr_hash(), expire_at: message.expire_at };
    // from the POST on, the message may be on its way: a failure leaves it to be looked up, never sent anew
    let tx = match chain.send(&message.cell, message.expire_at).and_then(|_| chain.wait(&sent)) {
        Ok(Some(tx)) => tx,
        Ok(None) => return Err(NotExecuted(format!("{}: message {} to {dest} expired without executing", wallet.name, sent.hash)).into()),
        Err(error) => return Err(Unresolved { sent, error }.into()),
    };
    let code = tx.exit_code.unwrap_or(-1);
    ensure!(tx.ok(), "{}: message {} failed on chain with exit code {code}{}", wallet.name, sent.hash, meaning(code));
    Ok(tx)
}

/// A wallet message that expired without executing: the wallet sent nothing, so sending again cannot pay twice.
#[derive(Debug)]
pub struct NotExecuted(pub String);

impl std::fmt::Display for NotExecuted {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for NotExecuted {}

/// A wallet message that may have reached the network: something failed after it was handed to the endpoint.
/// Whoever sent it looks it up (executed, or expired unexecuted) before the wallet sends anything new.
#[derive(Debug)]
pub struct Unresolved {
    pub sent: Sent,
    pub error: anyhow::Error,
}

impl std::fmt::Display for Unresolved {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{:#} (message {} may still execute: it is looked up before anything new is sent)", self.error, self.sent.hash)
    }
}

impl std::error::Error for Unresolved {}

/// What the wallets' common exit codes mean.
fn meaning(code: i32) -> &'static str {
    match code {
        52 => " (replay protection: a newer message from this wallet executed first)",
        57 => " (the message expired)",
        100 => " (the key is not the wallet's, or not a custodian's)",
        102 => " (no such multisig transaction: it executed or expired)",
        103 => " (this custodian has already confirmed it)",
        107 => " (the value is below the multisig's minimum)",
        113 => " (this custodian has too many pending multisig transactions)",
        // a multisig with several custodians measures the payload on the gas credit of an external message
        -14 | 121 => " (a multisig transaction takes a payload of at most 4 cells: pay from an EverWallet or a single-custodian multisig)",
        -1 => " (no code to run, or no funds to pay for the message)",
        _ => "",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// What ever-cli 0.44 (`getkeypair -p`, the version evernode's images build) derives from two BIP39
    /// test phrases: 12 and 24 words.
    #[test]
    fn a_seed_phrase_gives_the_key_ever_cli_derives() {
        let vectors = [
            (
                "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
                "77c647c114a311fc70d8f6d52d6ffef1ee105e36eba5a8769e3ce4d1a25bde25",
                "f3d1b6f23d9d0df2dec233b7ded22662c55b4fb5fd2b3a3ffeb83d6834562456",
            ),
            (
                "legal winner thank year wave sausage worth useful legal winner thank year wave sausage worth useful legal winner thank year wave sausage worth title",
                "c45e413c5f14698ca7880a35d0eac4c8771241a673ae88fcbf94058a29902c84",
                "86c19b475c072379580c56ef676bb3b87318fee09e6862274f031d46cddc4f50",
            ),
        ];
        for (phrase, public, secret) in vectors {
            let key = key_from_phrase(phrase).unwrap();
            assert_eq!((hex::encode(key.verifying_key().to_bytes()), hex::encode(key.to_bytes())), (public.into(), secret.into()));
            // as typed: extra spaces and a line break change nothing
            let typed = format!("  {}\n", phrase.replace(' ', "   "));
            assert_eq!(key_from_phrase(&typed).unwrap().to_bytes(), key.to_bytes());
        }
        // a wrong word or checksum is refused, and the error does not repeat the phrase
        for bad in ["abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon", "abandon zebrax"] {
            let error = format!("{:#}", key_from_phrase(bad).unwrap_err());
            assert!(error.starts_with("not a BIP39 phrase") && !error.contains("abandon"), "{error}");
        }
    }

    #[test]
    fn an_inline_wallet_without_type_is_told_apart_by_its_address() {
        let phrase = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about";
        let public = HashBytes(key_from_phrase(phrase).unwrap().verifying_key().to_bytes());
        let inline = |address: String| -> Source {
            serde_json::from_value(serde_json::json!({"name": "payer", "address": address, "phrases": [phrase]})).unwrap()
        };
        let everwallet = Wallet::open(&inline(ever_wallet::address(&public).unwrap().to_string())).unwrap();
        assert_eq!(everwallet.kind, Kind::EverWallet);
        let safe = Wallet::open(&inline(format!("-1:{}", "7".repeat(64)))).unwrap();
        assert_eq!(safe.kind, Kind::Multisig);
        // a path is a wallet file, made relative to the net file
        let mut path: Source = serde_json::from_value(serde_json::json!("wallets/payer.json")).unwrap();
        path.resolve(Path::new("/run"));
        assert!(matches!(path, Source::Path(p) if p == Path::new("/run/wallets/payer.json")));
    }
}
