//! What the operator sees: the network's governance state, a proposal's plan, an offline run of its whole
//! life, and the dictator check.

use std::path::Path;

use anyhow::{Context, Result, ensure};
use ed25519_dalek::Signer;
use tycho_types::models::StdAddr;
use tycho_types::prelude::*;

use crate::campaign::{self, Outcome};
use crate::config::{self, ConfigAccount};
use crate::emulate::{Sandbox, Validators};
use crate::node::Net;
use crate::proposal::{self, Change, Plan};
use crate::{EVER, KeyFile, PingOutcome, burn, ever, now, short, stamp, timeline};

pub fn report(outcome: Outcome) {
    let text = match outcome {
        Outcome::Applied => "APPLIED: the change is in effect".to_string(),
        Outcome::AlreadyInEffect => "nothing to do: the change is already in effect".to_string(),
        // accepted but not in effect: if_hash_equal no longer matched the value (or the elector refused)
        Outcome::Removed("accepted") => "REMOVED without effect: accepted, but the value had changed (stale if_hash_equal) or the elector refused it".to_string(),
        Outcome::Removed(why) => format!("REMOVED without effect: {why}"),
        Outcome::Gone(why) => format!("the proposal is gone: {why}"),
        Outcome::Pending => "the proposal is registered; voting goes on".to_string(),
        Outcome::Voted(0) => "nothing to vote in this round: every key here is counted, or the round is won; cast again next round, or keep voting with --watch".to_string(),
        Outcome::Voted(n) => format!("VOTED: {n} vote(s) counted in this round; cast again next round, or keep voting with --watch"),
        Outcome::Unconfirmed { id, received, required } => format!(
            "UNCONFIRMED: the payer's multisig transaction {id} has {received} of {required} confirmations; each other custodian runs the same command with a wallet file holding their own key"
        ),
    };
    println!("{} {text}", stamp());
}


/// The change, its proposal and its rules, priced for `ttl` seconds (`--ttl`; default: the minimum).
pub fn print_plan(cfg: &ConfigAccount, plan: &Plan, ttl: Option<u32>) -> Result<()> {
    let setup = cfg.vote_setup(plan.critical)?;
    println!("change      {}", plan.change.describe());
    // the elector's code is not read here; the change line names the new one
    if !matches!(plan.change, Change::ElectorCode { .. }) {
        let changes = proposal::registered_changes(cfg, None, &plan.proposal)?;
        if changes.is_empty() {
            println!("            no field changes: the value is the current one");
        }
        for line in changes {
            println!("            {line}");
        }
    }
    println!(
        "proposal    {} param {} critical {} if_hash_equal {}",
        plan.hash,
        plan.proposal.param_id,
        plan.critical,
        plan.proposal.if_hash_equal.map(|h| h.to_string()).unwrap_or("none".into())
    );
    let ttl = setup.clamp_ttl(ttl);
    println!("rules       {}; price {} EVER for {ttl}s", rules(&setup), ever(setup.price(plan.proposal.value.as_ref(), ttl)?));
    for w in &plan.warnings {
        println!("WARNING     {w}");
    }
    Ok(())
}


/// A registered proposal voted on by its hash: what it changes, after `propose`'s checks (what `propose`
/// refuses is refused here too; what needs a flag there is a warning here).
pub fn print_registered(cfg: &ConfigAccount, p: &config::Proposal) -> Result<()> {
    let warnings = proposal::check_registered(p)?;
    println!("change      {}", proposal::describe_registered(p));
    for line in proposal::registered_changes(cfg, None, p)? {
        println!("            {line}");
    }
    for w in warnings {
        println!("WARNING     {w}");
    }
    Ok(())
}

/// What decides a proposal: the contract counts a round lost whenever it was not won, and keeps a proposal
/// for `max_tot_rounds` more rounds after the one it was registered in.
pub fn rules(s: &config::VoteSetup) -> String {
    let rounds = |n: u32| if n == 1 { "1 round".to_string() } else { format!("{n} rounds") };
    format!(
        "{} winning {} needed, {} lost {} allowed, at most {}",
        s.min_wins,
        if s.min_wins == 1 { "round" } else { "rounds" },
        s.max_losses,
        if s.max_losses == 1 { "round" } else { "rounds" },
        rounds(s.max_tot_rounds as u32 + 1)
    )
}

/// Warns when the elections for the next set have closed: a proposal registered now has little time left
/// to win this round, and a round it does not win uses one of its allowed losses.
pub fn warn_round_end(cfg: &ConfigAccount) -> Result<()> {
    let t = cfg.timeline(now() as u32)?;
    if !t.elections_closed() {
        return Ok(());
    }
    if t.round_end > t.now {
        println!(
            "WARNING     the round ends {} ({}): validators have little time to vote in it, and a round the proposal does not win uses one of its allowed losses; proposing at the start of the next round avoids that",
            timeline::utc(t.round_end),
            timeline::span(t.round_end - t.now)
        );
    } else {
        // p36's start has passed but the switch is not in a block yet: a proposal registered before it counts
        // this dying round as its first
        println!(
            "WARNING     the next validator set was due at {} and has not taken over yet: registered before it does, the proposal loses this round, one of its allowed losses; proposing a minute from now avoids that",
            timeline::utc(t.round_end)
        );
    }
    Ok(())
}

pub fn status(net: &Net, keys: bool) -> Result<()> {
    let cfg = net.config()?;
    let vset = cfg.current_vset()?;
    println!("config      {} seqno {} balance {} EVER", cfg.address, cfg.seqno, ever(cfg.balance));
    println!("dictator    {} — {}", cfg.public_key, burn::classify(&cfg.public_key));
    if let Some(p) = &net.dictator
        && let Ok(k) = serde_json::from_slice::<KeyFile>(&std::fs::read(p)?)
    {
        println!("            {} the test dictator key in {}", if k.public == cfg.public_key { "is" } else { "is not" }, p.display());
    }
    if cfg.public_key == burn::derive(burn::DEFAULT_LABEL).0 {
        println!("            = the burn key of the default label");
    }
    for critical in [false, true] {
        let s = cfg.vote_setup(critical)?;
        println!("p11 {:<8} {}; stored {}..{}s", if critical { "critical" } else { "normal" }, rules(&s), s.min_store_sec, s.max_store_sec);
    }
    if !cfg.is_critical(11)? {
        println!("            p11 itself is not critical: a normal vote can change the critical rules");
    }
    println!(
        "p34         {} since {} until {} ({}), {} validators, total weight {}",
        short(&vset.id()),
        vset.utime_since,
        vset.utime_until,
        timeline::ends(vset.utime_until, now() as u32),
        vset.list.len(),
        vset.total_weight
    );
    let unvotable: Vec<_> = vset.list.iter().filter(|v| !v.votable).collect();
    if !unvotable.is_empty() {
        println!(
            "WARNING     {} validator(s) with {:.1}% of the weight have p34 entries the config contract cannot read (tags {:?}): their votes fail",
            unvotable.len(),
            unvotable.iter().map(|v| v.weight).sum::<u64>() as f64 * 100.0 / vset.total_weight.max(1) as f64,
            unvotable.iter().map(|v| format!("0x{:02x}", v.tag)).collect::<std::collections::BTreeSet<_>>()
        );
    }
    if keys {
        for node in &net.nodes {
            match node.held_keys(&vset) {
                Ok(held) => println!("  {:<12} holds {:?}", node.name, held.iter().map(|(idx, _)| idx).collect::<Vec<_>>()),
                Err(e) => println!("  {:<12} cannot list keys: {e:#}", node.name),
            }
        }
    }
    let elector_code = net.chain()?.code_hash(&cfg.elector_address()?)?;
    println!("code        config {} elector {}", cfg.code_hash, elector_code.map(|h| h.to_string()).unwrap_or_default());
    let (round, elections) = cfg.timeline(now() as u32)?.describe();
    println!("round       {round}");
    println!("elections   {elections}");
    if cfg.proposals.is_empty() {
        println!("proposals   none");
    }
    for (hash, p) in &cfg.proposals {
        let setup = cfg.vote_setup(p.critical)?;
        let view = p.view(&vset, &setup, now() as u32);
        println!("proposal    {hash}: {}", proposal::describe_registered(&p.proposal));
        println!(
            "            critical {} expires in {}s; wins {}/{} losses {}/{} rounds left {}; this round voted {:?}{} — {:?}",
            p.critical,
            p.expires as i64 - now() as i64,
            view.wins,
            setup.min_wins,
            view.losses,
            setup.max_losses,
            view.rounds_remaining,
            view.voters,
            if view.won_this_round() { " (won)" } else { "" },
            view.fate
        );
    }
    Ok(())
}


/// Plays the proposal's whole life on a copy of the contract with stand-in validators: registration,
/// one full round of votes after another, and what the contract and the resulting config look like.
/// `elector` (its `Account` cell) lets an elector upgrade play out in the elector as well.
pub fn simulate(cfg: &ConfigAccount, plan: &Plan, validators: usize, elector_account: Option<Cell>) -> Result<()> {
    let elector_code = elector_account.as_ref().and_then(|a| match a.parse::<tycho_types::models::OptionalAccount>().ok()?.0?.state {
        tycho_types::models::AccountState::Active(s) => s.code.map(|c| *c.repr_hash()),
        _ => None,
    });
    if plan.change.in_effect(cfg, elector_code)? {
        println!("result      nothing to do: the change is already in effect");
        return Ok(());
    }
    let start = now() as u32;
    let mut sb = Sandbox::new(cfg, start)?;
    let vs = Validators::random(validators, start, 3600);
    vs.install(&mut sb, 0)?;
    let from = StdAddr::new(-1, HashBytes([0x77; 32]));
    let setup = cfg.vote_setup(plan.critical)?;
    let ttl = setup.clamp_ttl(None);
    let value = setup.price(plan.proposal.value.as_ref(), ttl)? + 2 * EVER;
    let r = sb.register(&from, &plan.proposal, plan.critical, ttl, value)?;
    let answer = r.answer().unwrap_or_default();
    println!("register    0x{answer:08x}: {}", config::registration_answer(answer));
    ensure!(answer == config::ANSWER_PROPOSAL_OK, "the contract does not register this proposal");
    // every message the votes of the round that removed the proposal sent
    let mut last_round_out = Vec::new();
    for n in 1..=setup.max_tot_rounds as u32 + 1 {
        let st = sb.state()?;
        let Some(p) = st.proposal(&plan.hash) else { break };
        let vset = st.current_vset()?;
        let view = p.view(&vset, &setup, sb.now);
        let results = vs.vote_all(&mut sb, &from, campaign::VOTE_VALUE, &plan.hash)?;
        // an accepting vote for a code upgrade answers nothing (-1000 ends in throw(0) after set_code) or
        // forwards upgrade_code to the elector first (-1001)
        // how many votes ended which way, in order of first appearance
        let mut statuses: Vec<(&str, usize)> = Vec::new();
        for r in &results {
            let text = match (r.success, r.answer().and_then(config::vote_status)) {
                (false, _) => "FAILED",
                (true, Some((_, text))) => text,
                (true, None) => "no vote answer (an action ran)",
            };
            match statuses.iter_mut().find(|(t, _)| *t == text) {
                Some((_, count)) => *count += 1,
                None => statuses.push((text, 1)),
            }
        }
        let statuses = statuses.iter().map(|(t, c)| format!("{c} × {t}")).collect::<Vec<_>>().join(", ");
        println!("round {n:<5} wins {} losses {} rounds left {}: {statuses}", view.wins, view.losses, view.rounds_remaining);
        last_round_out = results.iter().flat_map(|r| r.out.iter().map(|m| (m.dst, m.op))).collect();
        vs.install(&mut sb, n)?;
    }
    let st = sb.state()?;
    ensure!(st.proposal(&plan.hash).is_none(), "the proposal was still open after {} rounds", setup.max_tot_rounds + 1);
    match &plan.change {
        Change::ElectorCode { .. } => {
            let elector = cfg.elector_address()?.address;
            let sent = last_round_out.iter().any(|(dst, op)| *dst == Some(elector) && *op == Some(0x4e436f64));
            ensure!(sent, "the proposal was removed, but upgrade_code was not forwarded to the elector");
            println!("result      accepted: the config forwarded upgrade_code to the elector");
            if let Some(elector) = elector_account {
                let body = crate::emulate::elector_upgrade_body(&plan.change.value()?, sb.now as u64)?;
                let (r, after) = crate::emulate::deliver(&sb.block_config()?, &elector, &cfg.address, 1 << 30, body, sb.now)?;
                let code = |acc: &Cell| match acc.parse::<tycho_types::models::OptionalAccount>().ok().and_then(|a| a.0).map(|a| a.state) {
                    Some(tycho_types::models::AccountState::Active(s)) => s.code.map(|c| *c.repr_hash()),
                    _ => None,
                };
                if r.success && code(&after) != code(&elector) {
                    println!("            the elector installed the new code");
                } else {
                    println!("            the ELECTOR REFUSES it (exit code {}): its code stays {}", r.exit_code, code(&elector).map(|h| h.to_string()).unwrap_or_default());
                }
            }
        }
        change => {
            ensure!(change.in_effect(&st, None)?, "the proposal was removed, but the change is not in effect");
            println!("result      accepted and in effect: {}", change.describe());
        }
    }
    ever_executor::BlockchainConfig::with_config(sb.block_config()?).context("ever-node cannot read the resulting config")?;
    let tock = sb.tock()?;
    ensure!(tock.success, "the config contract's tick-tock fails afterwards (exit code {}): the network would stop", tock.exit_code);
    println!("            ever-node's executor reads the resulting config, and the contract's tick-tock still runs");
    Ok(())
}


/// Seconds the dictator's messages stay valid: a rejected one is known once it has expired.
const DICTATOR_TTL: u32 = 30;

pub fn dictator_ping(net: &Net, keys: &Path) -> Result<PingOutcome> {
    let keys: KeyFile = serde_json::from_slice(&std::fs::read(keys)?).with_context(|| format!("parse {}", keys.display()))?;
    let secret = ed25519_dalek::SigningKey::from_bytes(keys.secret.as_array());
    ensure!(secret.verifying_key().to_bytes() == keys.public.0, "secret does not match public key");
    let cfg = net.config()?;
    println!(
        "{} config seqno {}, dictator {} ({}); signing action 0 with {}",
        stamp(),
        cfg.seqno,
        short(&cfg.public_key),
        burn::classify(&cfg.public_key),
        short(&keys.public)
    );
    let valid_until = now() as u32 + DICTATOR_TTL;
    let signed = config::dictator_action(0, cfg.seqno, valid_until, None)?;
    let signature = secret.sign(signed.repr_hash().as_slice()).to_bytes();
    let body = config::signed_body(&signature, &signed)?;
    // what the contract does with it on its current state: a key it rejects is known without sending
    let dry = Sandbox::new(&cfg, now() as u32)?.external(body.clone())?;
    if !dry.accepted {
        println!("{} REJECTED — the config does not take this key's signature (dry run: exit code {})", stamp(), dry.exit_code);
        return Ok(PingOutcome::Rejected);
    }
    println!("{} dry run: accepted", stamp());
    let chain = net.chain()?;
    let sent = chain.send(&config::external_message(&cfg.address, body)?, valid_until)?;
    let outcome = ping_outcome(chain.wait(&sent)?.is_some());
    println!(
        "{} {}: message {}",
        stamp(),
        match outcome {
            PingOutcome::Accepted => "ACCEPTED — the key controls the config",
            _ => "NOT DELIVERED — the dry run accepted it, but it expired without a transaction (dropped, beaten to the seqno, or not indexed): this says nothing about the key",
        },
        short(&sent.hash)
    );
    Ok(outcome)
}

/// A ping the dry run accepted: accepted if a transaction ran; otherwise not delivered, which says nothing
/// about the key (only the dry run tells a rejected one).
fn ping_outcome(ran: bool) -> PingOutcome {
    if ran { PingOutcome::Accepted } else { PingOutcome::NotDelivered }
}

/// The dictator sets param `id` to `value` directly (action `0x43665021`), as long as there is one.
pub fn dictator_set(net: &Net, keys: &Path, id: i32, value: Cell) -> Result<()> {
    let keys: KeyFile = serde_json::from_slice(&std::fs::read(keys)?).with_context(|| format!("parse {}", keys.display()))?;
    let secret = ed25519_dalek::SigningKey::from_bytes(keys.secret.as_array());
    ensure!(secret.verifying_key().to_bytes() == keys.public.0, "secret does not match public key");
    if id >= 0 {
        let mut warnings = Vec::new();
        proposal::check_param_value(id as u32, &value, &mut warnings)?;
        for w in warnings {
            println!("WARNING     {w}");
        }
    }
    let cfg = net.config()?;
    let valid_until = now() as u32 + DICTATOR_TTL;
    let signed = config::dictator_action(0x43665021, cfg.seqno, valid_until, Some(config::set_param_args(id, value.clone())?))?;
    let signature = secret.sign(signed.repr_hash().as_slice()).to_bytes();
    let body = config::signed_body(&signature, &signed)?;
    let dry = Sandbox::new(&cfg, now() as u32)?.external(body.clone())?;
    ensure!(dry.accepted, "dry run: the config rejects it (exit code {}): is this the dictator's key?", dry.exit_code);
    let chain = net.chain()?;
    let sent = chain.send(&config::external_message(&cfg.address, body)?, valid_until)?;
    let tx = chain.wait(&sent)?.context("the message expired without a transaction")?;
    ensure!(!tx.aborted, "the transaction aborted (exit code {:?})", tx.exit_code);
    let after = ConfigAccount::parse(&chain.account_after(&cfg.address, tx.lt)?)?;
    ensure!(after.param_hash(id)? == *value.repr_hash(), "the param did not change");
    println!("{} p{id} set by the dictator to {}", stamp(), proposal::param_json(id, &value).map(|j| j.to_string()).unwrap_or_else(|| format!("cell {}", value.repr_hash())));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Review 1, #9: a ping the dry run accepted is "accepted" only if a transaction ran; otherwise it is "not
    /// delivered", which matches neither `--expect` value.
    #[test]
    fn a_ping_without_a_transaction_is_not_delivered() {
        assert!(ping_outcome(true) == PingOutcome::Accepted);
        let lost = ping_outcome(false);
        assert!(lost != PingOutcome::Accepted && lost != PingOutcome::Rejected);
    }
}
