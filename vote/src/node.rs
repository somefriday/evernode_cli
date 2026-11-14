//! The network description: the chain's GraphQL endpoint, the validator keys this operator votes with, and
//! the wallets that pay.
//!
//! A vote needs only the endpoint and, per validator, its current key: from the node's key ring (`keyring`,
//! the node's own config file, read where it is or copied), or signed by the node itself over its control
//! server (`control`, see `control.rs`). Neither has to run on the node's host.
//!
//! The description can also come through a pipe (`--net /dev/fd/N`): evernode's `vote` group writes one
//! with absolute paths, and its wallets in place, so that no secret is in a file or an argument.
//!
//! `exec` and a node's `wallet` serve only `elect`, which keeps the local test network's validators staking:
//! the console's `election_bid` and `recover_stake` build elector requests, so it runs `console` where it is.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use anyhow::{Context, Result, bail, ensure};
use base64::Engine;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use tycho_types::models::StdAddr;
use tycho_types::prelude::*;

use crate::config::{ConfigAccount, ValidatorSet};
use crate::control::Control;
use crate::graphql::Graphql;
use crate::wallet;

/// The network description. Paths are relative to the file.
#[derive(Deserialize)]
pub struct Net {
    #[serde(default = "default_config_addr")]
    pub config: StdAddr,
    /// the chain's GraphQL endpoint (keep a URL that holds a project key out of shared files)
    #[serde(default)]
    pub endpoint: Option<String>,
    /// the validators this operator votes for, by where their keys are
    #[serde(default)]
    pub nodes: Vec<Node>,
    /// masterchain wallet that pays for proposals: a wallet file, or its content in place
    #[serde(default)]
    pub payer: Option<wallet::Source>,
    /// masterchain wallet that carries votes as internal messages; without one, votes go as external
    /// messages. A wallet that also stakes should not carry votes: replay protection rejects a message
    /// once a newer one from the same wallet has executed.
    #[serde(default)]
    pub relay: Option<wallet::Source>,
    /// the dictator's keys, if this operator holds them (test networks)
    #[serde(default)]
    pub dictator: Option<PathBuf>,
    /// `follow`: what an earlier pass reported of the proposal (evernode keeps it between passes)
    #[serde(default)]
    pub known: Option<crate::follow::Known>,
    #[serde(skip)]
    pub dir: PathBuf,
    #[serde(skip)]
    chain: Option<Graphql>,
}

fn default_config_addr() -> StdAddr {
    crate::config::DEFAULT_CONFIG_ADDR.parse().unwrap()
}

#[derive(Deserialize)]
pub struct Node {
    pub name: String,
    /// console key file of this node's control server (`console.json`): the node signs, its key never leaves
    #[serde(default)]
    pub control: Option<PathBuf>,
    /// overrides the server address in the key file (e.g. the local end of an SSH tunnel)
    #[serde(default)]
    pub address: Option<String>,
    /// sign with keys from the node's config file (`validator_key_ring`) instead of the control server; a
    /// copy must be refreshed after every election, when the node adds the next key
    #[serde(default)]
    pub keyring: Option<PathBuf>,
    /// test network only: the validator's wallet, which `elect` stakes from
    #[serde(default)]
    pub wallet: Option<PathBuf>,
    /// test network only: command prefix that runs `console` next to the node, for `elect`
    #[serde(default)]
    pub exec: Option<Vec<String>>,
    #[serde(default = "default_console_config")]
    pub console_config: String,
}

fn default_console_config() -> String {
    "/node/console.json".into()
}

impl Net {
    pub fn load(path: &Path) -> Result<Self> {
        let mut net: Self = serde_json::from_slice(&std::fs::read(path).with_context(|| format!("read {}", path.display()))?)
            .with_context(|| format!("parse {}", path.display()))?;
        net.dir = path.parent().unwrap_or(Path::new(".")).to_path_buf();
        let dir = net.dir.clone();
        let resolve = |p: &mut Option<PathBuf>| {
            if let Some(p) = p {
                *p = dir.join(&*p);
            }
        };
        for wallet in [&mut net.payer, &mut net.relay].into_iter().flatten() {
            wallet.resolve(&dir);
        }
        resolve(&mut net.dictator);
        for node in &mut net.nodes {
            resolve(&mut node.control);
            resolve(&mut node.keyring);
            resolve(&mut node.wallet);
        }
        net.chain = net.endpoint.as_deref().map(Graphql::new).transpose()?;
        Ok(net)
    }

    pub fn node(&self, name: &str) -> Result<&Node> {
        self.nodes.iter().find(|n| n.name == name).with_context(|| format!("no node {name}"))
    }

    /// The chain, through the endpoint.
    pub fn chain(&self) -> Result<&Graphql> {
        self.chain.as_ref().context("no endpoint in the net file: the tool reads and sends through a GraphQL endpoint")
    }

    pub fn config(&self) -> Result<ConfigAccount> {
        let cell = self.chain()?.account(&self.config)?.with_context(|| format!("config account {} does not exist", self.config))?;
        let cfg = ConfigAccount::parse(&cell)?;
        supported(&cfg)?;
        Ok(cfg)
    }
}

/// p8's `CapSignatureWithId` (the same bit in ever-block and tycho-types).
const CAP_SIGNATURE_WITH_ID: u64 = 1 << 26;

/// Refuses a network whose signatures cover its global id followed by the data: this tool signs the data
/// alone and dry-runs every message without the id, so each of its signatures would fail there.
pub fn supported(cfg: &ConfigAccount) -> Result<()> {
    ensure!(
        cfg.capabilities()? & CAP_SIGNATURE_WITH_ID == 0,
        "this network signs with signature ids (p8 capability CapSignatureWithId, 0x{CAP_SIGNATURE_WITH_ID:x}): this tool signs without them and does not support it"
    );
    Ok(())
}

/// ever-node's key id of an ed25519 public key: sha256 of TL `pub.ed25519 key:int256` (0x4813b4c6).
pub fn key_id(public: &HashBytes) -> HashBytes {
    let mut h = Sha256::new();
    h.update(0x4813b4c6u32.to_le_bytes());
    h.update(public.as_slice());
    HashBytes(h.finalize().into())
}

#[derive(Deserialize)]
struct KeyRingEntry {
    pvt_key: Option<String>,
}

impl Node {
    pub fn control(&self) -> Result<Control> {
        let path = self.control.as_ref().with_context(|| format!("{}: no control key file", self.name))?;
        Control::load(path, self.address.as_deref())
    }

    /// The exported key ring (base64 key id → entry), read fresh: the node adds a key every election.
    fn keyring(&self) -> Result<Option<HashMap<String, KeyRingEntry>>> {
        let Some(path) = &self.keyring else { return Ok(None) };
        #[derive(Deserialize)]
        struct NodeConfig {
            // a node that was never elected writes `null`: it holds no key
            #[serde(default)]
            validator_key_ring: Option<HashMap<String, KeyRingEntry>>,
        }
        let cfg: NodeConfig = serde_json::from_slice(&std::fs::read(path).with_context(|| format!("read {}", path.display()))?)
            .with_context(|| format!("parse {}", path.display()))?;
        Ok(Some(cfg.validator_key_ring.unwrap_or_default()))
    }

    /// The validators of `vset` whose keys this node holds: `(idx, public key)`.
    pub fn held_keys(&self, vset: &ValidatorSet) -> Result<Vec<(u16, HashBytes)>> {
        let b64 = base64::engine::general_purpose::STANDARD;
        if let Some(ring) = self.keyring()? {
            return Ok(vset
                .list
                .iter()
                .filter(|v| ring.contains_key(&b64.encode(key_id(&v.public_key).as_slice())))
                .map(|v| (v.idx, v.public_key))
                .collect());
        }
        let ids: Vec<_> = vset.list.iter().map(|v| key_id(&v.public_key)).collect();
        let found = self.control()?.export_public(&ids)?;
        Ok(vset.list.iter().zip(found).filter(|(v, k)| *k == Some(v.public_key)).map(|(v, _)| (v.idx, v.public_key)).collect())
    }

    /// Signs with the validator key `public`: offline from the key ring, or by the node itself.
    pub fn sign(&self, public: &HashBytes, data: &[u8]) -> Result<[u8; 64]> {
        let sig = match self.keyring()? {
            Some(ring) => {
                let b64 = base64::engine::general_purpose::STANDARD;
                let entry = ring.get(&b64.encode(key_id(public).as_slice())).with_context(|| format!("{}: key {public} is not in the key ring", self.name))?;
                let pvt = b64.decode(entry.pvt_key.as_deref().context("key ring entry without pvt_key")?)?;
                let seed: [u8; 32] = pvt.try_into().map_err(|_| anyhow::anyhow!("key ring: unexpected private key length"))?;
                let key = ed25519_dalek::SigningKey::from_bytes(&seed);
                ensure!(key.verifying_key().to_bytes() == public.0, "{}: key ring entry does not match its public key", self.name);
                ed25519_dalek::Signer::sign(&key, data).to_bytes()
            }
            None => self.control()?.sign(&key_id(public), data)?,
        };
        let key = ed25519_dalek::VerifyingKey::from_bytes(public.as_array())?;
        key.verify_strict(data, &ed25519_dalek::Signature::from_bytes(&sig))
            .with_context(|| format!("{}: the signature does not verify", self.name))?;
        Ok(sig)
    }

    fn run(&self, args: &[&str]) -> Result<Vec<u8>> {
        let exec = self.exec.as_ref().with_context(|| format!("{}: no exec prefix for the console", self.name))?;
        let mut argv = exec.iter().map(String::as_str).chain(args.iter().copied());
        let mut cmd = Command::new(argv.next().context("empty exec prefix")?);
        let out = cmd
            .args(argv)
            .stdin(Stdio::null())
            .output()
            .with_context(|| format!("{}: spawn {args:?}", self.name))?;
        if !out.status.success() {
            bail!(
                "{}: {args:?} failed ({}): {}{}",
                self.name,
                out.status,
                String::from_utf8_lossy(&out.stdout).trim(),
                String::from_utf8_lossy(&out.stderr).trim()
            );
        }
        Ok(out.stdout)
    }

    /// The election ids the node holds validator keys for (`validator_keys` in its config.json; each
    /// `election_bid` adds one), read next to the node.
    pub fn election_keys(&self) -> Result<Vec<u32>> {
        let out = self.run(&["sh", "-c", "jq -r '.validator_keys[]?.election_id' /node/config.json"])?;
        Ok(String::from_utf8_lossy(&out).split_whitespace().filter_map(|id| id.parse().ok()).collect())
    }

    /// Runs a console command that writes a BOC to `file` next to the node and returns the BOC.
    pub fn console_boc(&self, command: &str, file: &str) -> Result<Cell> {
        let script = format!("rm -f {file} && console -C {} -c '{command} {file}' >/dev/null && cat {file}", self.console_config);
        let bytes = self.run(&["sh", "-c", &script]).with_context(|| format!("{}: {command}", self.name))?;
        Ok(Boc::decode(&bytes)?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A node that was never elected writes `"validator_key_ring": null`; one that never wrote it has none.
    #[test]
    fn a_node_never_elected_holds_no_key() {
        let path = std::env::temp_dir().join(format!("evernode-vote-keyring-{}.json", std::process::id()));
        let node = Node { name: "n".into(), control: None, address: None, keyring: Some(path.clone()), wallet: None, exec: None, console_config: default_console_config() };
        for (json, keys) in [(r#"{"validator_key_ring": null}"#, 0), ("{}", 0), (r#"{"validator_key_ring": {"a2V5": {"pvt_key": null}}}"#, 1)] {
            std::fs::write(&path, json).unwrap();
            assert_eq!(node.keyring().unwrap().unwrap().len(), keys, "{json}");
        }
        std::fs::remove_file(&path).unwrap();
    }
}
