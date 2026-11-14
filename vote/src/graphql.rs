//! The chain, through a GraphQL endpoint: evercloud's public API, or q-server in front of a local network
//! (see the ever-gql project). Everything a vote needs goes through it: account states, sending messages
//! (`postRequests`), and what became of a sent message (the transaction it caused, with its exit code),
//! and of the internal messages that transaction sent in turn.

use std::thread::sleep;
use std::time::Duration;

use anyhow::{Context, Result, bail, ensure};
use base64::Engine;
use tycho_types::models::{AccountState, OptionalAccount, StdAddr};
use tycho_types::prelude::*;

use crate::now;

/// After its expiry a message can no longer execute, but its transaction may still be on its way into
/// the endpoint's database.
const INDEXING_GRACE: u32 = 20;
/// A transaction can reach the endpoint's database a moment after the account state it produced.
const INDEXING_LAG: Duration = Duration::from_secs(3);
/// The endpoint's newest masterchain block may be this old before its account states count as stale.
const MAX_LAG: Duration = Duration::from_secs(60);
/// An internal message between masterchain accounts executes in the next block or two.
const DELIVERY: Duration = Duration::from_secs(60);

pub struct Graphql {
    url: String,
    http: reqwest::blocking::Client,
}

/// A message handed to the endpoint: its hash, and when it stops being valid.
#[derive(Clone, Copy, Debug)]
pub struct Sent {
    pub hash: HashBytes,
    pub expire_at: u32,
}

/// The transaction a message caused.
#[derive(Debug)]
pub struct Executed {
    pub hash: HashBytes,
    pub lt: u64,
    /// when it ran (its block's time)
    pub now: u32,
    pub aborted: bool,
    /// the compute phase's exit code (`None`: skipped)
    pub exit_code: Option<i32>,
    /// the internal messages it sent
    pub sent: Vec<OutMessage>,
}

/// An internal message a transaction sent.
#[derive(Debug)]
pub struct OutMessage {
    pub dst: String,
    pub hash: HashBytes,
    /// nanotokens
    pub value: u128,
    /// the body's first 32 bits: an answer's op
    pub op: Option<u32>,
}

impl Executed {
    pub fn ok(&self) -> bool {
        !self.aborted && self.exit_code == Some(0)
    }

    /// The internal message it sent to `dest`.
    pub fn sent_to(&self, dest: &StdAddr) -> Option<&OutMessage> {
        self.sent.iter().find(|m| m.dst == dest.to_string())
    }
}

impl Graphql {
    pub fn new(url: &str) -> Result<Self> {
        let http = reqwest::blocking::Client::builder().timeout(Duration::from_secs(60)).build()?;
        Ok(Self { url: url.to_string(), http })
    }

    fn query(&self, query: &str, variables: serde_json::Value) -> Result<serde_json::Value> {
        let body = serde_json::json!({ "query": query, "variables": variables });
        let mut attempts = 0;
        let response: serde_json::Value = loop {
            // reqwest's errors name the URL, and an endpoint's URL may hold a project key
            match self.http.post(&self.url).json(&body).send().and_then(|r| r.error_for_status()) {
                Ok(r) => break r.json().map_err(reqwest::Error::without_url)?,
                // the first request on a keep-alive connection the server has just closed fails; every
                // request here can be repeated (a message is identified by its hash)
                Err(e) if attempts < 2 && e.is_request() => {
                    attempts += 1;
                    sleep(Duration::from_secs(1));
                }
                Err(e) => return Err(e.without_url().into()),
            }
        };
        if let Some(errors) = response.get("errors") {
            bail!("GraphQL: {errors}");
        }
        response.get("data").cloned().context("GraphQL: no data in the answer")
    }

    /// Fails if the endpoint's newest masterchain block is old: its account states would be too. Not
    /// `info.blocksLatency`: q-server times the masterchain block that holds the newest basechain block, so a
    /// stalled basechain reads as lag although everything this tool reads is in the masterchain (bd-e).
    pub fn check_fresh(&self) -> Result<()> {
        let age = now().saturating_sub(self.newest_block_time()? as u64);
        ensure!(age <= MAX_LAG.as_secs(), "the endpoint's newest block is {age} s old");
        Ok(())
    }

    /// When the endpoint's newest masterchain block was made.
    fn newest_block_time(&self) -> Result<u32> {
        let data = self.query("{ blockchain { blocks(workchain: -1, last: 1) { edges { node { gen_utime } } } } }", serde_json::json!({}))?;
        let time = data.pointer("/blockchain/blocks/edges/0/node/gen_utime").and_then(|t| t.as_u64()).context("GraphQL: no masterchain block")?;
        Ok(u32::try_from(time)?)
    }

    /// Is the endpoint past `expire_at`? A message executes only in a block made before its expiry, so once
    /// the endpoint holds a masterchain block made [`INDEXING_GRACE`] later, its transaction would be there.
    /// The wall clock alone does not tell: the endpoint may lag behind it. Fails once the endpoint lags so far
    /// that it cannot be told.
    pub fn past(&self, expire_at: u32) -> Result<bool> {
        if now() <= (expire_at + INDEXING_GRACE) as u64 {
            return Ok(false);
        }
        past_expiry(expire_at, self.newest_block_time()?, now())
    }

    /// The account cell, or `None` if the account does not exist.
    pub fn account(&self, addr: &StdAddr) -> Result<Option<Cell>> {
        let data = self.query(
            "query($a: String!) { blockchain { account(address: $a) { info { boc } } } }",
            serde_json::json!({ "a": addr.to_string() }),
        )?;
        match data.pointer("/blockchain/account/info/boc").and_then(|b| b.as_str()) {
            Some(boc) => Ok(Some(Boc::decode_base64(boc)?)),
            None => Ok(None),
        }
    }

    /// The account cell once it includes the transaction at `lt`: a transaction can reach the endpoint
    /// a moment before the account state it produced.
    pub fn account_after(&self, addr: &StdAddr, lt: u64) -> Result<Cell> {
        let mut last = None;
        crate::wait_for(Duration::from_secs(30), || {
            last = self.account(addr)?;
            Ok(last.as_ref().map(last_trans_lt).transpose()?.unwrap_or(0) >= lt)
        })
        .with_context(|| format!("{addr}: the endpoint has no state after its transaction at lt {lt}"))?;
        Ok(last.expect("checked above"))
    }

    /// The account's balance in nanotokens (0 if it does not exist).
    pub fn balance(&self, addr: &StdAddr) -> Result<u128> {
        Ok(match self.account(addr)? {
            Some(cell) => cell.parse::<OptionalAccount>()?.0.map(|a| a.balance.tokens.into_inner()).unwrap_or(0),
            None => 0,
        })
    }

    /// The account's code hash, if it is active.
    pub fn code_hash(&self, addr: &StdAddr) -> Result<Option<HashBytes>> {
        let Some(cell) = self.account(addr)? else { return Ok(None) };
        Ok(match cell.parse::<OptionalAccount>()?.0.map(|a| a.state) {
            Some(AccountState::Active(state)) => state.code.map(|c| *c.repr_hash()),
            _ => None,
        })
    }

    /// Hands an external message to the network; it is valid until `expire_at`.
    pub fn send(&self, message: &Cell, expire_at: u32) -> Result<Sent> {
        let b64 = base64::engine::general_purpose::STANDARD;
        let hash = *message.repr_hash();
        let request = serde_json::json!({
            "id": b64.encode(hash.as_slice()),
            "body": Boc::encode_base64(message),
            "expireAt": expire_at as u64 * 1000,
        });
        self.query(
            "mutation($requests: [Request]) { postRequests(requests: $requests) }",
            serde_json::json!({ "requests": [request] }),
        )?;
        Ok(Sent { hash, expire_at })
    }

    /// The transaction message `hash` caused, once the endpoint has it and every message it sent: the
    /// messages are stored apart from the transaction and can arrive a moment later.
    pub fn executed(&self, hash: &HashBytes) -> Result<Option<Executed>> {
        for _ in 0..10 {
            let data = self.query(
                "query($h: String!) { blockchain { transactions_by_in_msg(msg_hash: $h) {
                    hash lt now aborted compute { exit_code } out_msgs out_messages { hash msg_type_name dst value(format: DEC) body } } } }",
                serde_json::json!({ "h": hash.to_string() }),
            )?;
            let Some(tx) = data.pointer("/blockchain/transactions_by_in_msg/0") else { return Ok(None) };
            let sent = tx["out_msgs"].as_array().map_or(0, Vec::len);
            let found: Vec<&serde_json::Value> = tx["out_messages"].as_array().into_iter().flatten().filter(|m| m.is_object()).collect();
            if found.len() < sent {
                sleep(INDEXING_LAG);
                continue;
            }
            let lt = tx["lt"].as_str().context("transaction without lt")?;
            return Ok(Some(Executed {
                hash: tx["hash"].as_str().context("transaction without hash")?.parse()?,
                lt: u64::from_str_radix(lt.trim_start_matches("0x"), 16)?,
                now: tx["now"].as_u64().context("transaction without now")?.try_into()?,
                aborted: tx["aborted"].as_bool().unwrap_or(true),
                exit_code: tx.pointer("/compute/exit_code").and_then(|c| c.as_i64()).map(|c| c as i32),
                sent: found.iter().filter(|m| m["msg_type_name"] == "Internal").map(|m| out_message(m)).collect::<Result<_>>()?,
            }));
        }
        bail!("message {hash}: the endpoint has its transaction but not every message the transaction sent")
    }

    /// Waits for the transaction `sent` causes; `None` once it has expired without one.
    pub fn wait(&self, sent: &Sent) -> Result<Option<Executed>> {
        self.wait_unless(sent, || Ok(false))
    }

    /// Waits for the transaction the internal message `hash` causes: one a transaction of ours sent.
    pub fn follow(&self, hash: &HashBytes) -> Result<Executed> {
        let mut found = None;
        crate::wait_for(DELIVERY, || {
            found = self.executed(hash)?;
            Ok(found.is_some())
        })
        .with_context(|| format!("message {hash} reached no transaction"))?;
        Ok(found.expect("checked above"))
    }

    /// As `wait`, but also gives up once `dead` says the message can no longer execute.
    pub fn wait_unless(&self, sent: &Sent, mut dead: impl FnMut() -> Result<bool>) -> Result<Option<Executed>> {
        loop {
            // asked first: once the endpoint is past the expiry, a transaction would already be indexed
            let past = self.past(sent.expire_at)?;
            if let Some(tx) = self.executed(&sent.hash)? {
                return Ok(Some(tx));
            }
            if past {
                return Ok(None);
            }
            if dead()? {
                sleep(INDEXING_LAG);
                return self.executed(&sent.hash);
            }
            sleep(Duration::from_secs(2));
        }
    }
}

/// [`Graphql::past`], given the endpoint's newest masterchain block time and the wall clock.
fn past_expiry(expire_at: u32, newest_block: u32, now: u64) -> Result<bool> {
    let decided_at = expire_at + INDEXING_GRACE;
    if newest_block > decided_at {
        return Ok(true);
    }
    ensure!(
        now <= decided_at as u64 + MAX_LAG.as_secs(),
        "the endpoint has no block after {}, when a message expired: whether it executed is not known yet",
        crate::timeline::utc(expire_at)
    );
    Ok(false)
}

fn out_message(m: &serde_json::Value) -> Result<OutMessage> {
    let op = match m["body"].as_str() {
        Some(body) => {
            let cell = Boc::decode_base64(body)?;
            let mut cs = cell.as_slice()?;
            (cs.size_bits() >= 32).then(|| cs.load_u32()).transpose()?
        }
        None => None,
    };
    Ok(OutMessage {
        dst: m["dst"].as_str().context("message without dst")?.to_string(),
        hash: m["hash"].as_str().context("message without hash")?.parse()?,
        value: m["value"].as_str().map(str::parse).transpose()?.unwrap_or(0),
        op,
    })
}

fn last_trans_lt(account: &Cell) -> Result<u64> {
    Ok(account.parse::<OptionalAccount>()?.0.map(|a| a.last_trans_lt).unwrap_or(0))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_message_is_unexecuted_only_once_the_endpoint_is_past_its_expiry() {
        let expire = 1_000;
        let late = (expire + INDEXING_GRACE + 30) as u64;
        assert!(!past_expiry(expire, expire + 5, late).unwrap(), "the wall clock is past it, the endpoint is not");
        assert!(past_expiry(expire, expire + INDEXING_GRACE + 1, late).unwrap(), "the endpoint holds a later block");
        let too_late = (expire + INDEXING_GRACE) as u64 + MAX_LAG.as_secs() + 1;
        assert!(past_expiry(expire, expire + 5, too_late).is_err(), "a lagging endpoint never yields unexecuted");
    }

    #[test]
    fn a_failed_request_does_not_name_the_endpoint() {
        let chain = Graphql::new("http://127.0.0.1:9/0123SECRETPROJECTKEY/graphql").unwrap();
        let error = format!("{:#}", chain.check_fresh().unwrap_err());
        assert!(!error.contains("SECRETPROJECTKEY") && !error.contains("127.0.0.1"), "{error}");
    }
}
