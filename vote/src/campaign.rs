//! A proposal's whole life on a live network: register it (once, after a dry run), then vote in every
//! round with every validator key the listed nodes hold, until the change is in effect or the proposal
//! is gone. Every message is dry-run on the contract's current state first (`emulate.rs`), so nothing
//! that would fail is paid for, then sent through the endpoint and followed to its transaction.
//!
//! A vote goes as an external message, signed by the validator alone, unless a relay wallet carries it as
//! an internal one; a proposal is paid for by a masterchain wallet of any kind (`wallet`). Either way a vote
//! is followed into the config's own transaction, and it is done only when the config's state after that
//! transaction counts it. A vote that is not counted is signed and sent again at the next pass.
//!
//! A payment is made once. The operator confirms the value shown (unless `--yes`), in the process that sends
//! it. A wallet message that may have reached the network is looked up before anything new is sent: once it
//! executed, its payment is followed to the config's answer (registered, or refused with everything sent
//! back); the wallet pays again only if the endpoint is past the message's expiry and it never executed.
//!
//! A vote is sent only if its dry run shows the config counting it, or accepting the proposal with it.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::thread::sleep;
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail, ensure};
use sha2::{Digest, Sha256};
use tycho_types::models::StdAddr;
use tycho_types::prelude::*;

use crate::config::{self, ConfigAccount, Fate, RoundView, ValidatorSet};
use crate::emulate::{Sandbox, TxResult};
use crate::graphql::{Executed, Graphql, Sent};
use crate::node::{Net, Node};
use crate::proposal::Plan;
use crate::wallet::{NotExecuted, Transfer, Unresolved, Wallet};
use crate::{EVER, ever, now, say, short, stamp};

/// Value attached to a vote; the config returns what gas does not use.
pub const VOTE_VALUE: u128 = 1_500_000_000;
/// Seconds an external vote stays valid: it names the contract's seqno, so it must land soon or never.
const EXTERNAL_TTL: u32 = 60;
/// How many times the payer's message may expire unexecuted (nothing paid) before `propose` gives up.
const PAYMENT_TRIES: usize = 3;

pub struct Options {
    /// register the proposal if it is not there
    pub propose: bool,
    /// vote with the keys the listed nodes hold
    pub vote: bool,
    /// return after the first pass over a registered proposal; when voting, once every key held here is
    /// counted in the current round
    pub once: bool,
    /// wallet file paying the proposal's price (default: the net file's payer)
    pub payer: Option<PathBuf>,
    /// pay without asking
    pub yes: bool,
    pub ttl: Option<u32>,
    /// wallet file carrying every vote as an internal message (default: the net file's relay; without one,
    /// votes go as external messages)
    pub relay: Option<PathBuf>,
    /// vote by external messages even if a relay is set
    pub external: bool,
    pub poll: Duration,
    /// seconds after a validator set starts before voting in it: messages sent while the new validator
    /// sessions start up were lost on the test network
    pub settle: u32,
    /// give up after this long (`None`: until the outcome; a critical change takes 4+ rounds of ~18 h on mainnet)
    pub timeout: Option<Duration>,
    /// a file to lock (flock) once the payment is agreed, before anything is sent, until the process ends:
    /// whoever holds it is using the payer
    pub lock: Option<PathBuf>,
}

#[derive(Debug, PartialEq, Eq)]
pub enum Outcome {
    /// the change took effect while we watched
    Applied,
    /// nothing to do: the change was already in effect
    AlreadyInEffect,
    /// the proposal was removed without the change taking effect, and why (see [`why_removed`])
    Removed(&'static str),
    /// a proposal identified only by its hash is gone, and why
    Gone(&'static str),
    /// `once`: the proposal is registered and voting goes on
    Pending,
    /// `once` with votes: every key held here is counted in the current round, or the round was won
    /// without them; this many votes were counted
    Voted(usize),
    /// `once`: the payer's multisig transaction waits for confirmations by custodians whose keys are elsewhere
    Unconfirmed { id: u64, received: u8, required: u8 },
}

/// What is voted on: a checked plan, or any registered proposal by hash.
pub enum Target<'a> {
    Plan(&'a Plan),
    Hash(HashBytes),
}

impl Target<'_> {
    fn hash(&self) -> HashBytes {
        match self {
            Target::Plan(p) => p.hash,
            Target::Hash(h) => *h,
        }
    }
}

/// The campaign's memory between passes.
#[derive(Default)]
struct State {
    seen: bool,
    /// the proposal as last seen, with its voting setup: what it was when it disappeared
    last: Option<(config::ProposalStatus, config::VoteSetup)>,
    /// the payment, once the wallet sent it to the config: followed to the config's answer, never repeated
    paid: Option<Payment>,
    /// a payment message that may have reached the network, neither found executed nor expired yet
    unresolved: Option<Paying>,
    /// the value the operator agreed to pay: a payment sent again after its message expired unexecuted, for
    /// the same value, is not asked about again
    confirmed: Option<u128>,
    /// the config's answer to it was printed
    answered: bool,
    /// payer messages that expired without executing: nothing was paid by them
    unexecuted: usize,
    /// the payer's multisig transaction that waits for other custodians, as last printed
    awaiting: Option<(u64, u8)>,
    /// the current validator set and which listed node holds which of its keys (nodes that could not be
    /// asked are asked again at the next pass)
    vset: HashBytes,
    held: HashMap<usize, Vec<(u16, HashBytes)>>,
    /// the votes this run got counted: (validator set, validator index)
    counted: Vec<(HashBytes, u16)>,
    /// the round state last printed
    last_line: String,
}

/// What one pass decided; an `Err` from a pass is transient (a node did not answer) and retried.
enum Step {
    Continue,
    Done(Outcome),
    Fail(anyhow::Error),
}

/// A proposal's payment on its way to the config.
#[derive(Clone, Copy)]
struct Payment {
    /// the internal message that carries it
    message: HashBytes,
    price: u128,
    value: u128,
}

/// A payment message whose fate is not known yet, and what it pays.
#[derive(Clone, Copy)]
struct Paying {
    sent: Sent,
    price: u128,
    value: u128,
}

/// One validator's vote in the current round.
pub(crate) struct Ballot<'a> {
    pub node: &'a Node,
    /// the current validator set's id, the validator's index in it, and its key
    pub vset: HashBytes,
    pub idx: u16,
    pub key: HashBytes,
    pub proposal: HashBytes,
}

/// What became of a vote.
pub(crate) enum Cast {
    /// the config counts it in the current round, or accepted the proposal with it: in this transaction
    /// (`None`: another copy of this key's vote was counted), and the config's state once it does
    Counted { tx: Option<HashBytes>, after: Box<ConfigAccount> },
    /// the relay's multisig transaction waits for confirmations
    Pending { id: u64, received: u8, required: u8 },
}

pub fn run(net: &Net, target: Target, opts: &Options) -> Result<Outcome> {
    let start = Instant::now();
    let mut st = State::default();
    // a relay that cannot carry votes fails now, not at every vote
    let relay = match (&opts.relay, &net.relay) {
        _ if opts.external => None,
        (Some(path), _) => Some(masterchain(Wallet::load(path)?)?),
        (None, Some(source)) => Some(masterchain(Wallet::open(source)?)?),
        (None, None) => None,
    };
    while opts.timeout.is_none_or(|t| start.elapsed() < t) {
        match pass(net, &target, opts, relay.as_ref(), &mut st) {
            Ok(Step::Continue) => {}
            Ok(Step::Done(outcome)) => return Ok(outcome),
            Ok(Step::Fail(e)) => return Err(e),
            Err(e) => eprintln!("{} {e:#} (retrying)", stamp()),
        }
        sleep(opts.poll);
    }
    bail!("timed out after {:?}", opts.timeout.unwrap_or_default())
}

fn pass(net: &Net, target: &Target, opts: &Options, relay: Option<&Wallet>, st: &mut State) -> Result<Step> {
    let phash = target.hash();
    let chain = net.chain()?;
    chain.check_fresh()?;
    let cfg = net.config()?;
    resolve(chain, &cfg, st)?;
    let Some(status) = cfg.proposal(&phash) else {
        if let Target::Plan(plan) = target
            && in_effect(chain, &cfg, plan, st.seen)?
        {
            return Ok(Step::Done(if st.seen || st.paid.is_some() { Outcome::Applied } else { Outcome::AlreadyInEffect }));
        }
        if st.seen {
            let why = match &st.last {
                Some((last, setup)) => why_removed(last, setup, st.vset, &cfg.current_vset()?, now() as u32),
                None => "unknown",
            };
            return Ok(Step::Done(match target {
                Target::Plan(_) => Outcome::Removed(why),
                Target::Hash(_) => Outcome::Gone(why),
            }));
        }
        let Target::Plan(plan) = target else { return Ok(Step::Fail(anyhow::anyhow!("proposal {phash} is not registered"))) };
        if !opts.propose {
            return Ok(Step::Fail(anyhow::anyhow!("proposal {phash} is not registered")));
        }
        // the payment left the wallet: wait for the config's answer to it, and never pay again
        if let Some(payment) = st.paid {
            return answer(chain, &payment, &mut st.answered);
        }
        // a payment message that may still execute: nothing new goes out until it is resolved
        if st.unresolved.is_some() {
            return Ok(Step::Continue);
        }
        // a proposal registered as a validator set ends belongs to that set: the next one books the round
        // as lost before anyone could vote in it
        if cfg.timeline(now() as u32)?.near_switch() {
            return Ok(Step::Continue);
        }
        if st.unexecuted >= PAYMENT_TRIES {
            return Ok(Step::Fail(anyhow::anyhow!("the payer's message expired without executing {} times; nothing was paid", st.unexecuted)));
        }
        // a payer that cannot pay will not pay later either
        let payer = match payer(net, opts.payer.as_deref()) {
            Ok(payer) => payer,
            Err(e) => return Ok(Step::Fail(e)),
        };
        let r = Registration::new(&cfg, plan, opts.ttl)?;
        match propose(chain, &cfg, plan, &r, &payer, opts, &mut st.confirmed) {
            Ok(Transfer::Sent(message)) => st.paid = Some(Payment { message, price: r.price, value: r.value }),
            // asked again every pass: the payload is the same, so nothing new is submitted
            Ok(Transfer::Pending { id, received, required }) => {
                if opts.once {
                    return Ok(Step::Done(Outcome::Unconfirmed { id, received, required }));
                }
                if st.awaiting != Some((id, received)) {
                    say!("{} the payer's multisig transaction {id} has {received} of {required} confirmations", stamp());
                    st.awaiting = Some((id, received));
                }
            }
            // the message may have reached the network: it is looked up before anything new is sent
            Err(e) if e.downcast_ref::<Unresolved>().is_some() => {
                let sent = e.downcast_ref::<Unresolved>().expect("checked").sent;
                st.unresolved = Some(Paying { sent, price: r.price, value: r.value });
                return Err(e);
            }
            // nothing was paid: the next pass pays again
            Err(e) if e.downcast_ref::<NotExecuted>().is_some() => {
                st.unexecuted += 1;
                return Err(e);
            }
            // the payer is short of funds, or the operator declined: another pass changes nothing
            Err(e) if e.downcast_ref::<Stop>().is_some() => return Ok(Step::Fail(e)),
            // a dry run that fails will fail again: stop; anything else is retried
            Err(e) => return if format!("{e:#}").starts_with("dry run") { Ok(Step::Fail(e)) } else { Err(e) },
        }
        return Ok(Step::Continue);
    };
    st.seen = true;
    // the payment registered it, or someone else's did: say what the config did with ours
    if let Some(payment) = st.paid
        && !st.answered
    {
        st.answered = true;
        match chain.follow(&payment.message) {
            Ok(tx) => {
                say_answer(&payment, &tx);
            }
            Err(e) => eprintln!("{} the config's answer to the payment: {e:#}", stamp()),
        }
    }
    let vset = cfg.current_vset()?;
    let setup = cfg.vote_setup(status.critical)?;
    st.last = Some((status.clone(), setup));
    let view = status.view(&vset, &setup, now() as u32);
    if st.vset != vset.id() {
        st.vset = vset.id();
        st.held.clear();
    }
    let keys = held_keys(net, &vset, &mut st.held);
    for e in &keys.unreadable {
        eprintln!("{} {e} (asked again at the next pass)", stamp());
    }
    let ours = &keys.ours;
    // print when the round's state changes, not every poll
    let key = format!("{}/{}/{}/{:?}/{}/{}/{}", vset.id(), view.wins, view.losses, view.voters, view.won_this_round(), ours.len(), keys.unvotable.len());
    if key != st.last_line {
        say!("{} {}", stamp(), round_line(&vset, &view, &setup, ours));
        for line in &keys.unvotable {
            say!("            {line}");
        }
        st.last_line = key;
    }
    match view.fate {
        Fate::Alive => {}
        Fate::Expired => return Ok(Step::Fail(anyhow::anyhow!("proposal {phash} expired; the next vote or tick-tock deletes it"))),
        Fate::Discarded => {
            return Ok(Step::Fail(anyhow::anyhow!("proposal {phash} lost too many rounds (or ran out of them); the next vote deletes it")));
        }
    }
    // votes wait for the new set's sessions to settle, and stop before the set ends: a vote signed with this
    // set's key that lands under the next set fails its signature check
    let t = now() as u32;
    if opts.vote && !view.won_this_round() && t >= vset.utime_since + opts.settle && !cfg.timeline(t)?.near_switch() {
        for &(node, idx, key) in ours {
            if view.voters.contains(&idx) {
                continue;
            }
            let ballot = Ballot { node: &net.nodes[node], vset: vset.id(), idx, key, proposal: phash };
            let cast = match relay {
                Some(relay) => vote(chain, relay, &cfg, &ballot),
                None => vote_external(net, chain, &ballot),
            };
            match cast {
                Ok(Cast::Counted { after, .. }) => {
                    st.counted.push((vset.id(), idx));
                    // it accepted the proposal: the next pass tells how it ended
                    if after.proposal(&phash).is_none() {
                        return Ok(Step::Continue);
                    }
                }
                // every vote would wait for custodians whose keys are elsewhere
                Ok(Cast::Pending { id, received, required }) => {
                    return Ok(Step::Fail(anyhow::anyhow!(
                        "the relay's multisig transaction {id} has {received} of {required} confirmations: a relay must send with the keys here alone; vote by external message"
                    )));
                }
                // the contract refuses it in every try this round
                Err(e) if opts.once && format!("{e:#}").starts_with("dry run") => return Ok(Step::Fail(e)),
                Err(e) => eprintln!("{} {}: vote as #{idx} failed: {e:#} (sending it again)", stamp(), net.nodes[node].name),
            }
        }
    }
    if !opts.once {
        return Ok(Step::Continue);
    }
    if !opts.vote {
        return Ok(Step::Done(Outcome::Pending));
    }
    // one round's votes: done once every key held here is counted in it, or the round is won without them
    let counted = |idx: u16| view.voters.contains(&idx) || st.counted.contains(&(vset.id(), idx));
    if !(view.won_this_round() || ours.iter().all(|&(_, idx, _)| counted(idx))) {
        return Ok(Step::Continue);
    }
    one_round_done(&keys, st.counted.len())
}

/// The end of a one-shot `cast`, once every key found here is counted (or the round is won): a node that
/// could not be asked may hold a key, so that is never success.
fn one_round_done(keys: &Keys, counted: usize) -> Result<Step> {
    Ok(if !keys.unreadable.is_empty() {
        Step::Fail(anyhow::anyhow!("{counted} vote(s) counted, but these could not be asked for their keys: {}", keys.unreadable.join("; ")))
    } else if keys.ours.is_empty() {
        let unvotable = if keys.unvotable.is_empty() { String::new() } else { format!(" that can vote ({})", keys.unvotable.join("; ")) };
        Step::Fail(anyhow::anyhow!("none of the listed nodes holds a key of the current validator set{unvotable}"))
    } else {
        Step::Done(Outcome::Voted(counted))
    })
}

/// Looks up a payment message that may have reached the network. Executed: the payment it made is followed
/// from now on (a multisig step or a transfer that paid nothing leaves the wallet's state to decide the next
/// step). Expired unexecuted by the endpoint's own blocks: nothing was paid. Otherwise it may still execute.
fn resolve(chain: &Graphql, cfg: &ConfigAccount, st: &mut State) -> Result<()> {
    let Some(p) = st.unresolved else { return Ok(()) };
    if let Some(tx) = chain.executed(&p.sent.hash)? {
        st.unresolved = None;
        if let Some(out) = tx.sent_to(&cfg.address) {
            st.paid = Some(Payment { message: out.hash, price: p.price, value: p.value });
        }
    } else if chain.past(p.sent.expire_at)? {
        st.unresolved = None;
        st.unexecuted += 1;
        eprintln!("{} the payer's message {} expired without executing: nothing was paid", stamp(), p.sent.hash);
    }
    Ok(())
}

/// Why the contract removed a proposal last seen as `last` in validator set `last_set`. The paths out of
/// `vote_dict` are expiry, too many lost rounds (or no rounds left), and acceptance. Replaying the last read
/// is exact only in that set and before the expiry: then only acceptance removes a proposal. After a set
/// switch or the expiry, other validators may have won the round after the read: both stay possible.
pub(crate) fn why_removed(last: &config::ProposalStatus, setup: &config::VoteSetup, last_set: HashBytes, vset: &ValidatorSet, now: u32) -> &'static str {
    let exact = last_set == vset.id() && now < last.expires;
    match (last.view(vset, setup, now).fate, exact) {
        (Fate::Alive, _) => "accepted",
        (Fate::Discarded, true) => "lost too many rounds",
        (Fate::Discarded, false) => "accepted, or lost its last round: the validator set changed before it was read again",
        (Fate::Expired, _) => "accepted, or expired: it expired before it was read again",
    }
}

/// Is the change already the config's state (a burn already burned, a param already holding the value)?
/// `closing`: the proposal has just gone, so an elector upgrade is given the time the elector takes.
pub(crate) fn in_effect(chain: &Graphql, cfg: &ConfigAccount, plan: &Plan, closing: bool) -> Result<bool> {
    let elector = match &plan.change {
        crate::proposal::Change::ElectorCode { code, .. } if closing => elector_code_after(chain, cfg, code.repr_hash())?,
        crate::proposal::Change::ElectorCode { .. } => chain.code_hash(&cfg.elector_address()?)?,
        _ => None,
    };
    plan.change.in_effect(cfg, elector)
}

/// How long the elector may take to apply an accepted upgrade: the config sends it a message, which runs a
/// block or two later, and the endpoint may lag behind.
const ELECTOR_DELAY: Duration = Duration::from_secs(120);

/// The elector's code hash once it is `code`, or after [`ELECTOR_DELAY`] whatever it is: the elector
/// changes its code in its own transaction, after the config's.
pub(crate) fn elector_code_after(chain: &Graphql, cfg: &ConfigAccount, code: &HashBytes) -> Result<Option<HashBytes>> {
    let elector = cfg.elector_address()?;
    wait_for_code(|| chain.code_hash(&elector), code, ELECTOR_DELAY, Duration::from_secs(5))
}

fn wait_for_code(mut read: impl FnMut() -> Result<Option<HashBytes>>, code: &HashBytes, delay: Duration, every: Duration) -> Result<Option<HashBytes>> {
    let deadline = Instant::now() + delay;
    loop {
        let found = read()?;
        if found.as_ref() == Some(code) || Instant::now() >= deadline {
            return Ok(found);
        }
        sleep(every);
    }
}

/// The config's answer to `payment`, once it processed the message: registered (it kept the price and sent
/// the rest back), or refused (it sent everything back). The payment is never repeated either way.
fn answer(chain: &Graphql, payment: &Payment, answered: &mut bool) -> Result<Step> {
    let Some(tx) = chain.executed(&payment.message)? else { return Ok(Step::Continue) };
    let registered = *answered || say_answer(payment, &tx) == Some(config::ANSWER_PROPOSAL_OK);
    *answered = true;
    Ok(if registered {
        // the next pass finds the proposal
        Step::Continue
    } else {
        Step::Fail(anyhow::anyhow!("the config refused the proposal (transaction {})", tx.hash))
    })
}

/// Prints what the config did with `payment` in its transaction `tx` and returns its answer: registered (it
/// kept the price, or less for an extension, and sent the rest back), or a refusal (it sent everything back).
/// What it kept is measured: the amount paid minus the amount it sent back.
fn say_answer(payment: &Payment, tx: &Executed) -> Option<u32> {
    // the config answers every registration with one message to the payer
    let reply = tx.sent.first();
    let back = reply.map_or(0, |m| m.value);
    let op = reply.and_then(|m| m.op);
    match op {
        Some(config::ANSWER_PROPOSAL_OK) => say!(
            "{} registered: the config kept {} EVER of the {} EVER paid and sent back {} EVER; the price is {} EVER (transaction {})",
            stamp(),
            ever(payment.value.saturating_sub(back)),
            ever(payment.value),
            ever(back),
            ever(payment.price),
            tx.hash
        ),
        Some(op) => say!(
            "{} the config refused this payment: 0x{op:08x} ({}); it sent back {} EVER (transaction {})",
            stamp(),
            config::registration_answer(op),
            ever(back),
            tx.hash
        ),
        None => say!("{} the config's transaction {} for the payment sent no answer (exit code {:?})", stamp(), tx.hash, tx.exit_code),
    }
    op
}

/// The keys to vote with, and why some are missing.
#[derive(Default)]
pub(crate) struct Keys {
    /// `(node index, validator index, key)` for each current validator a listed node can sign for
    pub ours: Vec<(usize, u16, HashBytes)>,
    /// the nodes that could not be asked, with why
    pub unreadable: Vec<String>,
    /// keys held here whose p34 entry the config contract cannot read: their votes would fail (exit 41)
    pub unvotable: Vec<String>,
}

/// Which listed node holds which current key. Key-ring files are read at every pass: the operator may copy a
/// new one at any time. A control server's answer is kept for the validator set in `known`; a node that
/// could not be asked is asked again at the next pass.
pub(crate) fn held_keys(net: &Net, vset: &ValidatorSet, known: &mut HashMap<usize, Vec<(u16, HashBytes)>>) -> Keys {
    let mut keys = Keys::default();
    for (i, node) in net.nodes.iter().enumerate() {
        let held = match known.get(&i) {
            Some(held) => held.clone(),
            None => match node.held_keys(vset) {
                Ok(held) => held,
                Err(e) => {
                    keys.unreadable.push(format!("{}: cannot list its keys: {e:#}", node.name));
                    continue;
                }
            },
        };
        if node.keyring.is_none() {
            known.insert(i, held.clone());
        }
        for (idx, key) in held {
            // one vote per validator, even if two listed nodes hold its key
            if keys.ours.iter().any(|(_, j, _)| *j == idx) {
                continue;
            }
            match vset.list.iter().find(|v| v.idx == idx) {
                Some(v) if !v.votable => keys.unvotable.push(format!(
                    "{}: validator #{idx} cannot vote: its p34 entry has tag 0x{:02x}, which the config contract does not read",
                    node.name, v.tag
                )),
                _ => keys.ours.push((i, idx, key)),
            }
        }
    }
    keys
}

fn round_line(vset: &ValidatorSet, view: &RoundView, setup: &config::VoteSetup, ours: &[(usize, u16, HashBytes)]) -> String {
    let our_weight: u64 = vset.list.iter().filter(|v| ours.iter().any(|(_, idx, _)| *idx == v.idx)).map(|v| v.weight).sum();
    let mut line = format!(
        "round {} (p34 {}): wins {}/{} losses {}/{} rounds left {}, voted {:?}{}; we hold {} key(s), {:.1}% of the weight",
        short(&vset.id()),
        crate::timeline::ends(vset.utime_until, now() as u32),
        view.wins,
        setup.min_wins,
        view.losses,
        setup.max_losses,
        view.rounds_remaining,
        view.voters,
        if view.won_this_round() { " — won this round" } else { "" },
        ours.len(),
        our_weight as f64 * 100.0 / vset.total_weight.max(1) as f64,
    );
    if vset.votable_weight() <= vset.win_threshold() {
        line += " — CANNOT WIN: validators whose p34 entries the contract reads hold no more than 3/4 of the weight";
    }
    line
}

/// What registering `plan` costs, and the body that pays for it: the price for `ttl` seconds, and the value
/// attached (the price, plus what `register_voting_proposal` wants on top, `2^30` nanotokens: the config sends
/// the rest back).
pub struct Registration {
    pub ttl: u32,
    pub price: u128,
    pub value: u128,
    pub body: Cell,
}

impl Registration {
    pub fn new(cfg: &ConfigAccount, plan: &Plan, ttl: Option<u32>) -> Result<Self> {
        let setup = cfg.vote_setup(plan.critical)?;
        let ttl = setup.clamp_ttl(ttl);
        let price = setup.price(plan.proposal.value.as_ref(), ttl)?;
        // the same body every time, so that the custodians of a multisig payer all confirm one transaction
        let query_id = u64::from_be_bytes(plan.hash.0[..8].try_into()?);
        let body = config::new_proposal_body(query_id, ttl, &plan.proposal, plan.critical)?;
        Ok(Self { ttl, price, value: price + 2 * EVER, body })
    }

    /// Registers it from `payer` on a copy of the contract: the config must answer "registered" and keep
    /// exactly the price, so the price shown is the price taken.
    pub fn dry_run(&self, cfg: &ConfigAccount, payer: &StdAddr) -> Result<()> {
        let mut sb = Sandbox::new(cfg, now() as u32)?;
        let dry = sb.internal(payer, self.value, self.body.clone())?;
        let answer = dry.answer().unwrap_or_default();
        ensure!(
            dry.success && answer == config::ANSWER_PROPOSAL_OK,
            "dry run: the config would answer 0x{answer:08x} ({}), exit code {}",
            config::registration_answer(answer),
            dry.exit_code
        );
        let kept = sb.state()?.balance.saturating_sub(cfg.balance);
        ensure!(kept == self.price, "dry run: the config would keep {} EVER, not the price shown ({} EVER)", ever(kept), ever(self.price));
        Ok(())
    }
}

/// Nothing was paid, and another pass would not pay either: the payer holds too little, or the operator
/// declined.
#[derive(Debug)]
struct Stop(String);

impl std::fmt::Display for Stop {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Stop {}

/// Whether a payer holding `balance` can send `value` (the price plus 2 EVER) and its fees: the price plus
/// 3 EVER in all, as the docs and the error say.
fn can_pay(balance: u128, value: u128) -> bool {
    balance >= value + EVER
}

/// Holds the lock at `path` until the process ends, taken once.
fn hold_lock(path: &Path) -> Result<()> {
    static HELD: std::sync::OnceLock<std::fs::File> = std::sync::OnceLock::new();
    if HELD.get().is_none() {
        let _ = HELD.set(take_lock(path)?);
    }
    Ok(())
}

/// The lock at `path`, or a `Stop` if someone holds it: evernode passes the node's election lock, which a
/// scheduled election run holds while it uses the same Safe.
fn take_lock(path: &Path) -> Result<std::fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    let file = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .open(path)
        .with_context(|| format!("lock {}", path.display()))?;
    match file.try_lock() {
        Ok(()) => Ok(file),
        Err(std::fs::TryLockError::WouldBlock) => Err(Stop(format!(
            "nothing paid: {} is locked, so an election run is using the payer; propose again when it finishes",
            path.display()
        ))
        .into()),
        Err(std::fs::TryLockError::Error(e)) => Err(e).with_context(|| format!("lock {}", path.display())),
    }
}

/// Pays for `plan` with registration `r` once its dry run passed and the operator confirmed the value (once:
/// `confirmed` remembers it).
fn propose(chain: &Graphql, cfg: &ConfigAccount, plan: &Plan, r: &Registration, payer: &Wallet, opts: &Options, confirmed: &mut Option<u128>) -> Result<Transfer> {
    r.dry_run(cfg, &payer.address)?;
    let balance = chain.balance(&payer.address)?;
    if !can_pay(balance, r.value) {
        return Err(Stop(format!(
            "{} holds {} EVER; the proposal needs {} EVER plus fees: send it the price plus 3 EVER and propose again",
            payer.name,
            ever(balance),
            ever(r.value)
        ))
        .into());
    }
    // asked here, so the value confirmed is the value sent
    if !opts.yes && *confirmed != Some(r.value) {
        let question = format!(
            "Pay {} EVER from {} to register proposal {}? The config keeps the price, {} EVER, and sends the rest back",
            ever(r.value),
            payer.name,
            short(&plan.hash),
            ever(r.price)
        );
        if !crate::confirm(&question)? {
            return Err(Stop("nothing paid: the payment was declined".into()).into());
        }
        *confirmed = Some(r.value);
    }
    // after the answer, so a question left open holds nobody up, and before the payment
    if let Some(path) = &opts.lock {
        hold_lock(path)?;
    }
    let transfer = payer.transfer(chain, cfg, &cfg.address, r.value, r.body.clone())?;
    if let Transfer::Sent(_) = transfer {
        say!(
            "{} proposal {} (param {}, critical {}, ttl {}s, price {} EVER) sent from {}",
            stamp(),
            plan.hash,
            plan.proposal.param_id,
            plan.critical,
            r.ttl,
            ever(r.price),
            payer.name
        );
    }
    Ok(transfer)
}

/// The wallet that pays for proposals: the wallet file `from`, else the net file's payer.
pub fn payer(net: &Net, from: Option<&Path>) -> Result<Wallet> {
    masterchain(match from {
        Some(path) => Wallet::load(path)?,
        None => Wallet::open(net.payer.as_ref().context("nobody pays for registering the proposal: give propose a paying wallet (--from, or payer in the net file)")?)?,
    })
}

fn masterchain(wallet: Wallet) -> Result<Wallet> {
    // the config ignores messages from outside the masterchain (recv_internal: src_wc + 1)
    ensure!(
        wallet.address.workchain == -1,
        "{}: not in the masterchain, and the config takes messages from nowhere else (a DePool validator's own wallet is in workchain 0)",
        wallet.name
    );
    Ok(wallet)
}

impl Ballot<'_> {
    /// The vote's query id: the same at every try, so that a multisig relay finds its own pending transaction
    /// (it matches the body) instead of queueing another.
    fn query_id(&self) -> u64 {
        let mut h = Sha256::new();
        h.update(self.proposal.as_slice());
        h.update(self.vset.as_slice());
        h.update(self.idx.to_be_bytes());
        u64::from_be_bytes(h.finalize()[..8].try_into().expect("a sha256 has 8 bytes"))
    }
}

/// A vote carried as an internal message by `relay`.
fn vote(chain: &Graphql, relay: &Wallet, cfg: &ConfigAccount, b: &Ballot) -> Result<Cast> {
    let signature = b.node.sign(&b.key, &config::vote_sign_data(b.idx, &b.proposal))?;
    let body = config::vote_body(b.query_id(), &signature, b.idx, &b.proposal)?;
    let t = now() as u32;
    let mut sb = Sandbox::new(cfg, t)?;
    let dry = sb.internal(&relay.address, VOTE_VALUE, body.clone())?;
    let accepts = predict(cfg, &sb.state()?, &dry, b, t)?;
    let message = match relay.transfer(chain, cfg, &cfg.address, VOTE_VALUE, body)? {
        Transfer::Sent(message) => message,
        Transfer::Pending { id, received, required } => return Ok(Cast::Pending { id, received, required }),
    };
    // the relay's message runs in a transaction of the config's own
    let tx = chain.follow(&message)?;
    ensure!(tx.ok(), "the config's transaction {} for the vote failed with exit code {:?}", tx.hash, tx.exit_code);
    // the config answers the relay: that, not the dry run, says what the vote did
    let answer = tx.sent_to(&relay.address).and_then(|m| m.op).and_then(config::vote_status);
    landed(chain, cfg, b, &tx, answer, accepts, &format!("carried by {}", relay.name))
}

/// A vote by external message: signed by the validator, carried by nobody's wallet. It names the
/// contract's current seqno, so only one external message can take it: a vote that loses the race is
/// given up as soon as the seqno moves on, and signed again with the new one at the next pass.
pub(crate) fn vote_external(net: &Net, chain: &Graphql, b: &Ballot) -> Result<Cast> {
    let cfg = net.config()?;
    let t = now() as u32;
    let valid_until = t + EXTERNAL_TTL;
    let signed = config::external_vote(cfg.seqno, valid_until, b.idx, &b.proposal)?;
    let signature = b.node.sign(&b.key, signed.data())?;
    let body = config::signed_body(&signature, &signed)?;
    let mut sb = Sandbox::new(&cfg, t)?;
    let dry = sb.external(body.clone())?;
    ensure!(dry.accepted, "dry run: the config would not accept the external vote (exit code {})", dry.exit_code);
    let accepts = predict(&cfg, &sb.state()?, &dry, b, t)?;
    let sent = chain.send(&config::external_message(&cfg.address, body)?, valid_until)?;
    // once another message took the seqno, ours can no longer execute, unless the config counts our index (or
    // the proposal has closed): then it may have run, and its transaction is still on its way into the
    // endpoint. Or another copy of this key's vote counted it.
    let mut found = None;
    let tx = chain.wait_unless(&sent, || {
        let later = net.config()?;
        if later.seqno == cfg.seqno {
            return Ok(false);
        }
        let ours = match later.proposal(&b.proposal) {
            Some(p) => p.vset_id == b.vset && p.voters.contains(&b.idx),
            None => true,
        };
        if ours && later.proposal(&b.proposal).is_some() {
            found = Some(later);
        }
        Ok(!ours)
    })?;
    let Some(tx) = tx else {
        if let Some(after) = found {
            say!("{} {}: validator #{} is counted, but not by this message: another copy of its vote was faster", stamp(), b.node.name, b.idx);
            return Ok(Cast::Counted { tx: None, after: Box::new(after) });
        }
        bail!("the external vote with seqno {} did not land: another message took the seqno first, or it expired", cfg.seqno);
    };
    ensure!(!tx.aborted, "the external vote's transaction {} aborted (exit code {:?})", tx.hash, tx.exit_code);
    landed(chain, &cfg, b, &tx, None, accepts, &format!("by external message (seqno {})", cfg.seqno))
}

/// What the config would do with vote `b`, from its dry run on `before` (at time `t`): count it (`false`), or
/// accept the proposal with it (`true`). Anything else is refused before sending: the vote would count
/// nothing, and an external one would still take the config's seqno.
pub(crate) fn predict(before: &ConfigAccount, after: &ConfigAccount, dry: &TxResult, b: &Ballot, t: u32) -> Result<bool> {
    // the contract commits the external vote's seqno before counting it: a failure after that is not visible
    // as a rejection
    ensure!(dry.success && dry.exit_code == 0, "dry run: the vote would count nothing: the config fails with exit code {}", dry.exit_code);
    if let Some(p) = after.proposal(&b.proposal) {
        let answer = dry.answer().and_then(config::vote_status).map(|(_, text)| format!(": {text}")).unwrap_or_default();
        ensure!(p.vset_id == b.vset && p.voters.contains(&b.idx), "dry run: the config would not count the vote{answer}");
        return Ok(false);
    }
    // gone with the vote: accepted, unless the vote only deleted an expired proposal
    ensure!(
        before.proposal(&b.proposal).is_some_and(|p| p.expires > t),
        "dry run: the proposal is gone or expired: the vote would count nothing"
    );
    Ok(true)
}

/// What vote `b` did, from the config's answer to it (`answer`, a relayed vote's) or its state once that
/// state includes the vote's transaction `tx` (see [`effect`]). Prints the vote, with the transaction to look
/// it up by.
fn landed(chain: &Graphql, cfg: &ConfigAccount, b: &Ballot, tx: &Executed, answer: Option<(i32, &str)>, accepts: bool, how: &str) -> Result<Cast> {
    let after = ConfigAccount::parse(&chain.account_after(&cfg.address, tx.lt)?)?;
    let expires = cfg.proposal(&b.proposal).map_or(0, |p| p.expires);
    let what = effect(&after, b, answer, accepts, tx.now, expires).with_context(|| format!("transaction {}", tx.hash))?;
    say!(
        "{} {}: voted as validator #{} ({}) for {} {how}: {what} (transaction {})",
        stamp(),
        b.node.name,
        b.idx,
        short(&b.key),
        short(&b.proposal),
        tx.hash
    );
    Ok(Cast::Counted { tx: Some(tx.hash), after: Box::new(after) })
}

/// What a vote's transaction at time `at` did. The config's state `after` it shows a vote counted in its round.
/// Otherwise a relayed vote has the config's own `answer` (status, text), which says it. An external vote
/// gets no answer: a proposal gone before `expires`, as its dry run predicted (`accepts`), may still have been
/// closed by another vote, or by the config's own tick, before this one ran, so that is not called counted.
pub(crate) fn effect(after: &ConfigAccount, b: &Ballot, answer: Option<(i32, &str)>, accepts: bool, at: u32, expires: u32) -> Result<String> {
    if let Some(p) = after.proposal(&b.proposal)
        && p.vset_id == b.vset
        && p.voters.contains(&b.idx)
    {
        let won = if p.weight_remaining < 0 { ", the round is won" } else { "" };
        return Ok(format!("counted, {} validator(s) voted in this round{won}", p.voters.len()));
    }
    Ok(match (answer, after.proposal(&b.proposal)) {
        (Some((6 | 7, _)), _) => "counted, and the proposal is accepted with it".to_string(),
        (Some((2, _)), _) => "counted".to_string(),
        (Some((_, text)), _) => bail!("the config answered \"{text}\": the vote was not counted"),
        (None, Some(_)) => bail!("it ran, but the config does not count the vote"),
        (None, None) if accepts && at < expires => {
            "the proposal is closed, as this vote was to close it; an external vote gets no answer, so the next pass says how it ended".to_string()
        }
        (None, None) => bail!("it ran after the proposal had closed (it expired, or other votes accepted it): the vote was not counted"),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lock_file(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("evernode-vote-{name}-{}.lock", std::process::id()))
    }

    #[test]
    fn the_stated_minimum_pays() {
        let price = 5 * EVER;
        let value = price + 2 * EVER; // what a registration sends
        assert!(can_pay(price + 3 * EVER, value), "the price plus 3 EVER, as the docs and the error say");
        assert!(!can_pay(price + 3 * EVER - 1, value));
    }

    #[test]
    fn a_held_lock_stops_the_payment_before_anything_is_sent() {
        let path = lock_file("held");
        let election_run = std::fs::File::create(&path).unwrap();
        election_run.try_lock().unwrap();
        let e = take_lock(&path).unwrap_err();
        assert!(e.downcast_ref::<Stop>().is_some(), "a Stop ends the run: no pass pays later");
        assert!(e.to_string().starts_with("nothing paid: "), "{e}");
        drop(election_run);
        std::fs::remove_file(&path).unwrap();
    }

    #[test]
    fn a_free_lock_is_taken_and_kept_from_an_election_run() {
        let path = lock_file("free");
        let held = take_lock(&path).unwrap();
        let election_run = std::fs::File::open(&path).unwrap();
        assert!(matches!(election_run.try_lock(), Err(std::fs::TryLockError::WouldBlock)));
        drop(held);
        assert!(election_run.try_lock().is_ok());
        std::fs::remove_file(&path).unwrap();
    }

    #[test]
    fn the_elector_wait_returns_once_the_code_is_there_and_gives_up_after_the_delay() {
        let (old, new) = (HashBytes([1; 32]), HashBytes([2; 32]));
        let mut reads = vec![Some(old), Some(old), Some(new)].into_iter();
        let found = wait_for_code(|| Ok(reads.next().flatten()), &new, Duration::from_secs(10), Duration::ZERO).unwrap();
        assert_eq!(found, Some(new), "applied on the third read");
        let found = wait_for_code(|| Ok(Some(old)), &new, Duration::from_millis(50), Duration::from_millis(10)).unwrap();
        assert_eq!(found, Some(old), "not applied after the delay");
    }

    #[test]
    fn a_one_shot_cast_with_an_unreadable_node_fails_after_voting() {
        let keys = Keys { ours: vec![(0, 3, HashBytes([3; 32]))], unreadable: vec!["laptop: cannot list its keys: read keys.json".into()], unvotable: vec![] };
        let Step::Fail(e) = one_round_done(&keys, 1).unwrap() else { panic!("a one-shot cast that could not ask every node succeeded") };
        assert!(format!("{e:#}").contains("1 vote(s) counted") && format!("{e:#}").contains("laptop"), "{e:#}");
        let keys = Keys { unreadable: vec![], ..keys };
        assert!(matches!(one_round_done(&keys, 1).unwrap(), Step::Done(Outcome::Voted(1))));
        let none = Keys { ours: vec![], unreadable: vec![], unvotable: vec!["vdp4: validator #0 cannot vote".into()] };
        let Step::Fail(e) = one_round_done(&none, 0).unwrap() else { panic!() };
        assert!(format!("{e:#}").contains("cannot vote"), "{e:#}");
    }

    #[test]
    fn a_relayed_vote_has_the_same_body_at_every_try() {
        let node: Node = serde_json::from_value(serde_json::json!({"name": "vdp4"})).unwrap();
        let ballot = |idx| Ballot { node: &node, vset: HashBytes([5; 32]), idx, key: HashBytes([6; 32]), proposal: HashBytes([7; 32]) };
        assert_eq!(ballot(3).query_id(), ballot(3).query_id());
        assert_ne!(ballot(3).query_id(), ballot(4).query_id());
    }
}
