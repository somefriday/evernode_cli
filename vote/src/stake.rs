//! Test-network infrastructure: keeps one validator node staking in every election, so validator sets —
//! and with them voting rounds — keep changing. The console's `election_bid` and `recover_stake` build the
//! elector requests, so this runs `console` next to the node (`exec` in the net file). It reads and sends
//! through the node's own control server, not the endpoint: the network must keep going without it.

use std::thread::sleep;
use std::time::Duration;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use tycho_types::prelude::*;

use crate::config::ConfigAccount;
use crate::node::{Net, Node};
use crate::wallet::{Message, Wallet, ever_wallet};
use crate::{EVER, elector, ever, now, stamp, wait_for};

/// How long after its expiry a bid's message may still show up as executed: when the chain stalls, a block
/// collated before the expiry reaches the node later.
const LANDING_GRACE: u64 = 60;

pub fn run_elect(net: &Net, node: &Node, stake: u128, poll: Duration) -> Result<()> {
    let wallet_path = node.wallet.as_ref().with_context(|| format!("{}: no wallet in the net file", node.name))?;
    let wallet = Wallet::load(wallet_path)?;
    println!("{} {}: staking {} EVER from {} in every election", stamp(), node.name, ever(stake), wallet.address);
    // kept across restarts: a lost bid is resent from here
    let state = net.dir.join(format!("elect-{}.json", node.name));
    let mut bid: Bid = std::fs::read(&state).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default();
    loop {
        let before = bid.clone();
        if let Err(e) = elect_step(net, node, &wallet, stake, &mut bid) {
            eprintln!("{} {}: {e:#}", stamp(), node.name);
        }
        if bid != before {
            std::fs::write(&state, serde_json::to_vec(&bid)?)?;
        }
        sleep(poll);
    }
}

/// The node's bid in the last election it joined.
#[derive(Clone, Default, PartialEq, Serialize, Deserialize)]
struct Bid {
    election: u32,
    /// the elector request the console signed (base64 BOC); `None` for a key an earlier run made
    request: Option<String>,
    /// the `time` header (ms) and expiry of the wallet message that carries it
    time: u64,
    expire_at: u32,
}

/// Bids with one validator key per election. Asking the console again would make a second key, and if both
/// bids land the node gets two seats but validates with one key only (ever-node's `get_validator_key` takes
/// the first): enough dark seats stop the chain, which is how test network bd-a was lost. A bid whose
/// message expired unexecuted goes out again as the same signed request: the elector merges stakes that
/// arrive twice for one key, so this never makes a second seat. A bid given up for lost stalls elections
/// for good when every node's stake is needed (bd-e: `min_validators` was 3 of 3).
fn elect_step(net: &Net, node: &Node, wallet: &Wallet, stake: u128, bid: &mut Bid) -> Result<()> {
    let control = node.control()?;
    let cfg = ConfigAccount::parse(&control.account(&net.config)?.context("no config account")?)?;
    let elector_addr = cfg.elector_address()?;
    let election = elector::active_election(&control.account(&elector_addr)?.context("no elector")?)?;
    if election == 0 {
        return Ok(());
    }
    let wallet_time = || -> Result<u64> { ever_wallet::last_time(&control.account(&wallet.address)?.context("no wallet")?) };
    let send = |value: u128, payload: Cell| -> Result<Message> {
        let msg = ever_wallet::send_transaction(wallet.key(), &wallet.address, &elector_addr, value, payload, None)?;
        control.send(&msg.cell)?;
        Ok(msg)
    };
    if election == bid.election {
        let Some(request) = &bid.request else { return Ok(()) };
        // executed, or it still may be
        if wallet_time()? >= bid.time || now() <= u64::from(bid.expire_at) + LANDING_GRACE {
            return Ok(());
        }
        let msg = send(stake + EVER, Boc::decode_base64(request)?)?;
        (bid.time, bid.expire_at) = (msg.time, msg.expire_at);
        println!("{} {}: the bid for election {election} expired unexecuted; sent the same request again", stamp(), node.name);
        return Ok(());
    }
    // the node already holds a key for this election: an earlier run (or loop) bid in it
    if node.election_keys()?.contains(&election) {
        *bid = Bid { election, ..Bid::default() };
        return Ok(());
    }
    // stakes and rewards of finished rounds come back first
    let msg = send(EVER, node.console_boc("recover_stake", "/tmp/evernode-vote-recover.boc")?)?;
    // one block can take the wallet's two messages in any order, and its replay protection would reject the
    // older one: bid once the recovery has executed (or after 30 s; a lost recovery is retried next time)
    let _ = wait_for(Duration::from_secs(30), || Ok(wallet_time()? >= msg.time));
    // the console creates the next validator key and signs the request for the wallet in console.json
    let request = node.console_boc(&format!("election_bid {election} {}", election + 100_000), "/tmp/evernode-vote-bid.boc")?;
    let msg = send(stake + EVER, request.clone())?;
    *bid = Bid { election, request: Some(Boc::encode_base64(&request)), time: msg.time, expire_at: msg.expire_at };
    println!("{} {}: staked {} EVER in election {election}", stamp(), node.name, ever(stake));
    Ok(())
}
