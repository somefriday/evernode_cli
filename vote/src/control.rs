//! ever-node's control server, spoken natively (ADNL over TCP, the protocol `console` uses).
//!
//! A vote can be signed by the node itself: `sign` with the key from its key ring, after `export_public`
//! tells which current validator keys it holds. The key never leaves the node, and the tool needs only the
//! console key file (server address, server public key, client secret) and a route to the control port:
//! direct, or an SSH tunnel (`ssh -L 3031:127.0.0.1:3031 validator-host`) to a server on localhost.
//! `account` and `send` serve the test network's staking loop (`stake.rs`), which must not depend on the
//! GraphQL endpoint.

use std::path::Path;
use std::sync::OnceLock;

use adnl::client::{AdnlClient, AdnlClientConfig, AdnlClientConfigJson};
use adnl::common::TaggedTlObject;
use anyhow::{Context, Result, anyhow, bail};
use ton_api::ton::{self, TLObject};
use ton_api::ton::accountaddress::AccountAddress;
use ton_api::ton::engine::validator::ControlQueryError;
use ton_api::ton::raw::ShardAccountState;
use ton_api::ton::rpc::engine::validator::ControlQuery;
use tycho_types::models::StdAddr;
use tycho_types::prelude::*;

fn runtime() -> &'static tokio::runtime::Runtime {
    static RT: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
    RT.get_or_init(|| tokio::runtime::Builder::new_multi_thread().enable_all().build().expect("tokio runtime"))
}

pub struct Control {
    config: serde_json::Value,
}

impl Control {
    /// Loads a console key file: either a whole `console.json` (`{"config": {...}, ...}`) or its `config`
    /// section. `address` overrides the server address in it (e.g. a tunnel's local end).
    pub fn load(path: &Path, address: Option<&str>) -> Result<Self> {
        let json: serde_json::Value =
            serde_json::from_slice(&std::fs::read(path).with_context(|| format!("read {}", path.display()))?)?;
        let mut config = json.get("config").cloned().unwrap_or(json);
        if config.get("client_key").is_none() {
            bail!("{}: no client_key (the console's private key) in the key file", path.display());
        }
        if let Some(address) = address {
            config["server_address"] = address.into();
        }
        Ok(Self { config })
    }

    fn query(&self, query: TLObject) -> Result<TLObject> {
        self.queries(vec![query])?.pop().unwrap()
    }

    /// Runs several queries over one connection; each answer or control-server error in order.
    fn queries(&self, queries: Vec<TLObject>) -> Result<Vec<Result<TLObject>>> {
        let json: AdnlClientConfigJson = serde_json::from_value(self.config.clone())?;
        let (_, config) = AdnlClientConfig::from_json_config(json).map_err(|e| anyhow!("{e}"))?;
        let boxed = queries
            .iter()
            .map(|q| {
                let data = ton_api::serialize_boxed(q).map_err(|e| anyhow!("{e}"))?;
                Ok(TaggedTlObject { object: TLObject::new(ControlQuery { data }) })
            })
            .collect::<Result<Vec<_>>>()?;
        runtime().block_on(async {
            let mut client = AdnlClient::connect(&config).await.map_err(|e| anyhow!("connect: {e}"))?;
            let mut answers = Vec::with_capacity(boxed.len());
            for q in &boxed {
                match client.query(q).await {
                    Ok(answer) => answers.push(match answer.downcast::<ControlQueryError>() {
                        Ok(error) => Err(anyhow!("control server error: {error:?}")),
                        Err(answer) => Ok(answer),
                    }),
                    Err(e) => {
                        let _ = client.shutdown().await;
                        bail!("query: {e}");
                    }
                }
            }
            let _ = client.shutdown().await;
            Ok(answers)
        })
    }

    /// The account cell (`raw.getShardAccountState`); `None` if the account does not exist.
    pub fn account(&self, addr: &StdAddr) -> Result<Option<Cell>> {
        let answer = self.query(TLObject::new(ton::rpc::raw::GetShardAccountState {
            account_address: AccountAddress { account_address: addr.to_string() },
        }))?;
        let state = answer.downcast::<ShardAccountState>().map_err(|a| anyhow!("unexpected answer {a:?}"))?;
        let Some(bytes) = state.shard_account() else { return Ok(None) };
        // ShardAccount: account:^Account last_trans_hash:bits256 last_trans_lt:uint64
        Ok(Some(Boc::decode(bytes)?.reference_cloned(0).context("ShardAccount without account")?))
    }

    pub fn send(&self, message: &Cell) -> Result<()> {
        let answer = self.query(TLObject::new(ton::rpc::lite_server::SendMessage { body: Boc::encode(message) }))?;
        answer
            .downcast::<ton::engine::validator::Success>()
            .map_err(|a| anyhow!("sendmessage: unexpected answer {a:?}"))?;
        Ok(())
    }

    /// Signs `data` with the key `key_id` of the node's key ring; the key never leaves the node.
    pub fn sign(&self, key_id: &HashBytes, data: &[u8]) -> Result<[u8; 64]> {
        let answer = self.query(TLObject::new(ton::rpc::engine::validator::Sign {
            key_hash: ever_block::UInt256::with_array(key_id.0),
            data: data.to_vec(),
        }))?;
        let sig = answer
            .downcast::<ton::engine::validator::Signature>()
            .map_err(|a| anyhow!("sign: unexpected answer {a:?}"))?;
        sig.signature().to_vec().try_into().map_err(|_| anyhow!("bad signature length"))
    }

    /// The public keys of `key_ids` the node holds (`None` for the others), over one connection.
    /// Only the node's "key not found" counts as not held; any other error is an error.
    pub fn export_public(&self, key_ids: &[HashBytes]) -> Result<Vec<Option<HashBytes>>> {
        let queries = key_ids
            .iter()
            .map(|id| TLObject::new(ton::rpc::engine::validator::ExportPublicKey { key_hash: ever_block::UInt256::with_array(id.0) }))
            .collect();
        self.queries(queries)?
            .into_iter()
            .map(|answer| match answer {
                Ok(answer) => {
                    let key = answer.downcast::<ton::PublicKey>().map_err(|a| anyhow!("exportpub: unexpected answer {a:?}"))?;
                    Ok(key.key().map(|k| HashBytes(*k.as_slice())))
                }
                Err(e) if format!("{e:#}").contains("key not found") => Ok(None),
                Err(e) => Err(e),
            })
            .collect()
    }
}
