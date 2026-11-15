//! One pass for a proposal joined on this host (`follow`), run by evernode's schedule: read the chain, vote
//! with every key the listed nodes hold that the config has not counted in the current round, and report as
//! JSON what was seen and done and when the next pass is due. Progress goes to stderr: the cron log, or the
//! operator's terminal at `join`.
//!
//! Stdout carries JSON lines: `{"vote": …}` for each vote of ours the moment it is counted, then the report.
//! A pass killed after a vote still leaves that vote's line behind, so evernode records it even when the
//! proposal closes before another pass could find it counted.
//!
//! The engine keeps nothing between passes. evernode stores each report and hands back what a later pass
//! needs ([`Known`]) in the net description: what the proposal changes, to tell once it is gone whether it
//! took effect, and the config's version when it was last read open. A config older than that comes from a
//! stale endpoint, and nothing is decided from it: in particular a proposal missing from it is not closed.

use std::collections::HashMap;

use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use tycho_types::prelude::*;

use crate::campaign::{self, Ballot, Cast};
use crate::config::{ConfigAccount, Fate, PARAM_ELECTOR_CODE, ProposalStatus, RoundView, VoteSetup};
use crate::graphql::Graphql;
use crate::node::Net;
use crate::proposal;
use crate::timeline::{Timeline, span, utc};
use crate::{now, short, stamp};

/// Seconds after a validator set starts before voting in it, as `cast`'s default `--settle`: messages sent
/// while the new validator sessions start were lost on the test network.
const SETTLE: u32 = 40;
/// Seconds to the next pass while a vote of ours is missing in the current round.
const RETRY: u32 = 60;
/// Tries per vote in one pass: a vote that loses the race for the config's seqno is signed again.
const TRIES: usize = 3;

/// What an earlier pass reported, as evernode hands it back.
#[derive(Deserialize, Clone, Debug)]
pub struct Known {
    /// the proposal's param: -999 the master key, -1000 and -1001 the codes
    pub param: i32,
    /// what the chain shows once the change is in effect ([`crate::config::Proposal::target`])
    pub target: HashBytes,
    /// the config's version (`last_trans_lt`) when the proposal was last read open, in decimal
    pub config_version: String,
}

/// What a pass saw and did; evernode stores it. Fields that only an open proposal has are `None` once it is
/// gone from the config: the stored ones still hold.
#[derive(Serialize, Debug)]
pub struct Report {
    pub proposal: String,
    /// `open`, `applied` or `not applied`
    pub state: &'static str,
    /// what it changes, in words, and line by line against the current config
    pub change: Option<String>,
    pub change_lines: Option<Vec<String>>,
    pub param: i32,
    pub target: String,
    pub critical: Option<bool>,
    pub config_version: String,
    pub progress: Option<Progress>,
    pub round_end: u32,
    pub round_end_exact: bool,
    pub earliest_acceptance: Option<u32>,
    /// votes of ours: sent by this pass, or found counted in the current round
    pub votes: Vec<VoteRecord>,
    /// when the next pass is due; none once the proposal is closed
    pub next_pass_at: Option<u32>,
    /// what came of the pass, in a line
    pub result: String,
    /// nodes that could not be asked for their keys, votes that did not land
    pub errors: Vec<String>,
    /// what `propose` would only register with a flag (`--force`, `--allow-usable-key`), and other risks
    pub warnings: Vec<String>,
}

#[derive(Serialize, Debug, Clone)]
pub struct Progress {
    pub round: i32,
    pub wins: u8,
    pub wins_needed: u8,
    pub losses: u8,
    pub losses_allowed: u8,
    pub rounds_left: i32,
    pub won_this_round: bool,
    /// validators that voted in this round
    pub voters: usize,
}

#[derive(Serialize, Debug, Clone)]
pub struct VoteRecord {
    pub round: i32,
    pub node: String,
    pub validator: u16,
    pub key: String,
    /// the config's transaction that counted it; none for a vote found counted (cast elsewhere or by an earlier pass)
    pub transaction: Option<String>,
    pub sent_at: Option<u32>,
    pub counted_at: Option<u32>,
}

pub fn pass(net: &Net, phash: HashBytes, dry_run: bool) -> Result<Report> {
    let chain = net.chain()?;
    chain.check_fresh()?;
    let cfg = net.config()?;
    check_version(&cfg, net.known.as_ref())?;
    let t = now() as u32;
    let timeline = cfg.timeline(t)?;
    let Some(status) = cfg.proposal(&phash) else {
        // the first pass, at join, needs a registered proposal; after that, missing means closed
        let known = net.known.as_ref().with_context(|| format!("proposal {phash} is not registered"))?;
        let mut report = gone(phash, known, &cfg, &timeline);
        close(&mut report, chain, &cfg, "it is no longer among the config's proposals", true)?;
        print_summary(&report);
        return Ok(report);
    };
    let mut report = open_report(phash, status, chain, &cfg, &timeline)?;
    let setup = cfg.vote_setup(status.critical)?;
    let vset = cfg.current_vset()?;
    let view = status.view(&vset, &setup, t);
    if view.fate != Fate::Alive {
        let why = if view.fate == Fate::Expired { "it expired" } else { "it lost too many rounds, or ran out of them" };
        close(&mut report, chain, &cfg, why, false)?;
        print_summary(&report);
        return Ok(report);
    }
    let round = round_number(&view, &setup);
    let keys = campaign::held_keys(net, &vset, &mut HashMap::new());
    report.errors.extend(keys.unreadable.iter().cloned());
    let ours = &keys.ours;
    let record = |node: usize, idx: u16, key: HashBytes| VoteRecord {
        round,
        node: net.nodes[node].name.clone(),
        validator: idx,
        key: key.to_string(),
        transaction: None,
        sent_at: None,
        counted_at: None,
    };
    for &(node, idx, key) in ours.iter().filter(|(_, idx, _)| view.voters.contains(idx)) {
        report.votes.push(stream(record(node, idx, key)));
    }
    let missing: Vec<_> = if view.won_this_round() { Vec::new() } else { ours.iter().filter(|(_, idx, _)| !view.voters.contains(idx)).copied().collect() };
    let settled = t >= vset.utime_since + SETTLE;
    let mut voted = 0;
    // the config's state once it included our last vote's transaction: never older than what this pass read
    let mut latest: Option<ConfigAccount> = None;
    // the proposal closed as one of our votes ran, which got no answer to say whether it counted
    let mut unconfirmed = false;
    if !dry_run && settled && !timeline.near_switch() && !missing.is_empty() {
        for &(node, idx, key) in &missing {
            let ballot = Ballot { node: &net.nodes[node], vset: vset.id(), idx, key, proposal: phash };
            let mut failure = None;
            for _ in 0..TRIES {
                let sent_at = now() as u32;
                match campaign::vote_external(net, chain, &ballot) {
                    Ok(Cast::Counted { tx, after }) => {
                        let counted = VoteRecord {
                            transaction: tx.map(|h| h.to_string()),
                            sent_at: Some(sent_at),
                            counted_at: Some(now() as u32),
                            ..record(node, idx, key)
                        };
                        report.votes.push(stream(counted));
                        voted += 1;
                        latest = Some(*after);
                        failure = None;
                        break;
                    }
                    Ok(Cast::Closed { after, .. }) => {
                        latest = Some(*after);
                        unconfirmed = true;
                        failure = None;
                        break;
                    }
                    Ok(Cast::Pending { .. }) => failure = Some(anyhow::anyhow!("an external vote cannot wait for confirmations")),
                    Err(e) => {
                        eprintln!("{} {}: vote as #{idx} did not land: {e:#}", stamp(), ballot.node.name);
                        failure = Some(e);
                    }
                }
            }
            if let Some(e) = failure {
                report.errors.push(format!("{}: vote as #{idx}: {e:#}", ballot.node.name));
            }
            // the vote accepted the proposal: it is gone, and the rest have nothing to vote for
            if latest.as_ref().is_some_and(|after| after.proposal(&phash).is_none()) {
                break;
            }
        }
    }
    // what the votes changed, from the state after the last one
    let (missing_now, view_now) = match &latest {
        Some(after) => match after.proposal(&phash) {
            None => {
                let why = if unconfirmed {
                    "accepted as our vote ran, which gets no answer to say whether it counted"
                } else {
                    "accepted with our vote"
                };
                close(&mut report, chain, after, why, true)?;
                print_summary(&report);
                return Ok(report);
            }
            Some(status) => {
                let view = status.view(&vset, &setup, now() as u32);
                report.progress = Some(progress(&view, &setup));
                report.config_version = after.last_trans_lt.to_string();
                let missing = if view.won_this_round() { 0 } else { ours.iter().filter(|(_, idx, _)| !view.voters.contains(idx)).count() };
                (missing, view)
            }
        },
        None => (missing.len(), view),
    };
    report.earliest_acceptance = earliest_acceptance(&view_now, &setup, &timeline);
    report.next_pass_at = Some(next_pass_at(&timeline, vset.utime_since, missing_now, !report.errors.is_empty()));
    report.result = result_line(&timeline, &vset, &keys, missing_now, voted, &view_now, dry_run, &report.errors);
    print_summary(&report);
    Ok(report)
}

/// Prints a vote of ours as a JSON line the moment it is known, and returns it for the report.
fn stream(vote: VoteRecord) -> VoteRecord {
    println!("{}", serde_json::json!({ "vote": vote }));
    vote
}

/// A config older than the one an earlier pass read comes from a stale endpoint.
fn check_version(cfg: &ConfigAccount, known: Option<&Known>) -> Result<()> {
    let Some(known) = known else { return Ok(()) };
    let seen: u64 = known.config_version.parse().context("known config_version is not a number")?;
    ensure!(
        cfg.last_trans_lt >= seen,
        "the endpoint serves an older config (version {}) than an earlier pass read ({seen}): it is stale, so nothing is decided from it",
        cfg.last_trans_lt
    );
    Ok(())
}

fn open_report(phash: HashBytes, status: &ProposalStatus, chain: &Graphql, cfg: &ConfigAccount, timeline: &Timeline) -> Result<Report> {
    let p = &status.proposal;
    // whoever registered it, it gets propose's checks: what propose refuses is never voted for
    let warnings = proposal::check_registered(p)?;
    let setup = cfg.vote_setup(status.critical)?;
    let view = status.view(&cfg.current_vset()?, &setup, timeline.now);
    Ok(Report {
        proposal: phash.to_string(),
        state: "open",
        change: Some(proposal::describe_registered(p)),
        change_lines: Some(proposal::registered_changes(cfg, elector_code(chain, cfg, p.param_id)?, p)?),
        param: p.param_id,
        target: p.target().to_string(),
        critical: Some(status.critical),
        config_version: cfg.last_trans_lt.to_string(),
        progress: Some(progress(&view, &setup)),
        round_end: timeline.round_end,
        round_end_exact: timeline.exact,
        earliest_acceptance: None,
        votes: Vec::new(),
        next_pass_at: None,
        result: String::new(),
        errors: Vec::new(),
        warnings,
    })
}

/// A report for a proposal that is no longer in the config, from what an earlier pass knew of it.
fn gone(phash: HashBytes, known: &Known, cfg: &ConfigAccount, timeline: &Timeline) -> Report {
    Report {
        proposal: phash.to_string(),
        state: "open",
        change: None,
        change_lines: None,
        param: known.param,
        target: known.target.to_string(),
        critical: None,
        config_version: cfg.last_trans_lt.to_string(),
        progress: None,
        round_end: timeline.round_end,
        round_end_exact: timeline.exact,
        earliest_acceptance: None,
        votes: Vec::new(),
        next_pass_at: None,
        result: String::new(),
        errors: Vec::new(),
        warnings: Vec::new(),
    }
}

/// Ends the report: the proposal can no longer be voted on, and the change is in effect or not. `accepted`:
/// it may have been accepted, so an elector upgrade is given the time the elector takes to apply it.
fn close(report: &mut Report, chain: &Graphql, cfg: &ConfigAccount, why: &str, accepted: bool) -> Result<()> {
    let target: HashBytes = report.target.parse().context("target is not a hash")?;
    let elector = if accepted && report.param == PARAM_ELECTOR_CODE { campaign::elector_code_after(chain, cfg, &target)? } else { elector_code(chain, cfg, report.param)? };
    let applied = proposal::target_in_effect(cfg, elector, report.param, &target)?;
    report.state = if applied { "applied" } else { "not applied" };
    report.next_pass_at = None;
    report.earliest_acceptance = None;
    report.config_version = cfg.last_trans_lt.to_string();
    report.result = format!("closed: {why}; the change is {}in effect", if applied { "" } else { "not " });
    Ok(())
}

/// The elector's code hash, read only for an elector upgrade.
fn elector_code(chain: &Graphql, cfg: &ConfigAccount, param: i32) -> Result<Option<HashBytes>> {
    if param == PARAM_ELECTOR_CODE { chain.code_hash(&cfg.elector_address()?) } else { Ok(None) }
}

/// The round the proposal is in, counting from 1: `max_tot_rounds` are left when it is registered.
fn round_number(view: &RoundView, setup: &VoteSetup) -> i32 {
    setup.max_tot_rounds as i32 - view.rounds_remaining + 1
}

fn progress(view: &RoundView, setup: &VoteSetup) -> Progress {
    Progress {
        round: round_number(view, setup),
        wins: view.wins,
        wins_needed: setup.min_wins,
        losses: view.losses,
        losses_allowed: setup.max_losses,
        rounds_left: view.rounds_remaining,
        won_this_round: view.won_this_round(),
        voters: view.voters.len(),
    }
}

/// The earliest the proposal can be accepted: when it wins the last round it needs, if it wins every round
/// from now on.
fn earliest_acceptance(view: &RoundView, setup: &VoteSetup, t: &Timeline) -> Option<u32> {
    let needed = setup.min_wins.saturating_sub(view.wins) as u32;
    let next = t.next_round_start();
    match (needed, view.won_this_round()) {
        (0, _) => None,
        // this round can be the last one it needs
        (1, false) => Some(t.now),
        // this round counts, and `needed - 1` more
        (n, false) => Some(next + (n - 2) * t.round_length),
        // this round is won already: `needed` more, the first of them next
        (n, true) => Some(next + (n - 1) * t.round_length),
    }
}

/// When the next pass is due: in a minute while a vote of ours is missing or something failed, otherwise at
/// the next round's start. A round that started less than [`SETTLE`] seconds ago is voted in once it settles.
fn next_pass_at(t: &Timeline, set_start: u32, missing: usize, failed: bool) -> u32 {
    if failed {
        t.now + RETRY
    } else if missing == 0 || t.near_switch() {
        t.next_round_start()
    } else if t.now < set_start + SETTLE {
        set_start + SETTLE
    } else {
        t.now + RETRY
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn result_line(
    t: &Timeline,
    vset: &crate::config::ValidatorSet,
    keys: &campaign::Keys,
    missing: usize,
    voted: usize,
    view: &RoundView,
    dry_run: bool,
    errors: &[String],
) -> String {
    let ours = keys.ours.len();
    let mut done = match () {
        _ if !errors.is_empty() => format!("{} problem(s): {}", errors.len(), errors.join("; ")),
        _ if ours == 0 && !keys.unvotable.is_empty() => "no key of the listed nodes can vote".to_string(),
        _ if ours == 0 => "no key of the listed nodes is in this round's validator set".to_string(),
        _ if view.won_this_round() && voted == 0 => "this round is won".to_string(),
        _ if missing == 0 && voted > 0 => format!("voted: {voted} vote(s) counted"),
        _ if missing == 0 => "every key of ours is counted in this round".to_string(),
        _ if t.near_switch() => format!("the round ends in {}: voting goes on in the next one", span(t.round_end.saturating_sub(t.now))),
        _ if t.now < vset.utime_since + SETTLE => format!("the round started {} ago: votes go out {SETTLE} s into it", span(t.now - vset.utime_since)),
        _ if dry_run => format!("would vote with {missing} key(s) now"),
        _ => format!("{missing} vote(s) of ours are not counted yet"),
    };
    for line in &keys.unvotable {
        done = format!("{done}; {line}");
    }
    if vset.votable_weight() <= vset.win_threshold() {
        done = format!("CANNOT WIN: validators whose p34 entries the config contract reads hold no more than 3/4 of the weight; {done}");
    }
    if dry_run { format!("dry run: {done}") } else { done }
}

/// The report in words, for the operator's terminal or the cron log.
fn print_summary(r: &Report) {
    eprintln!("{} proposal {}: {}", stamp(), r.proposal, r.change.as_deref().unwrap_or("(gone from the config)"));
    for line in r.change_lines.iter().flatten() {
        eprintln!("            {line}");
    }
    for w in &r.warnings {
        eprintln!("WARNING     {w}");
    }
    if let Some(p) = &r.progress {
        eprintln!(
            "            round {}: wins {}/{}, losses {}/{}, {} round(s) left; {} validator(s) voted{}",
            p.round,
            p.wins,
            p.wins_needed,
            p.losses,
            p.losses_allowed,
            p.rounds_left,
            p.voters,
            if p.won_this_round { ", the round is won" } else { "" }
        );
    }
    if r.state == "open" {
        eprintln!(
            "            the round ends {}{}",
            utc(r.round_end),
            if r.round_end_exact { " (exact: the next set is elected)" } else { " (planned; exact once the next set is elected)" }
        );
        if let Some(e) = r.earliest_acceptance {
            eprintln!("            earliest acceptance {}", utc(e));
        }
    }
    let next = r.next_pass_at.map(|n| format!("; next pass {}", utc(n))).unwrap_or_default();
    eprintln!("{} {}: {}{next}", stamp(), short(&r.proposal.parse::<HashBytes>().unwrap_or_default()), r.result);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::ValidatorSet;
    use crate::timeline::ElectionTiming;

    const TIMING: ElectionTiming = ElectionTiming { elected_for: 300, start_before: 200, end_before: 80 };
    const START: u32 = 1_000_000;

    /// A round that started at `START`, planned for 300 s, at `now`, with the next set elected or not.
    fn timeline(now: u32, next: Option<u32>) -> Timeline {
        let mut cell = CellBuilder::new();
        cell.store_u8(0x12).unwrap();
        cell.store_u32(START).unwrap();
        cell.store_u32(START + 300).unwrap();
        cell.store_u16(0).unwrap();
        cell.store_u16(0).unwrap();
        cell.store_u64(0).unwrap();
        cell.store_bit_zero().unwrap();
        let current = ValidatorSet::parse(cell.build().unwrap()).unwrap();
        let next = next.map(|since| {
            let mut c = CellBuilder::new();
            c.store_u8(0x12).unwrap();
            c.store_u32(since).unwrap();
            c.store_u32(since + 300).unwrap();
            c.store_u16(0).unwrap();
            c.store_u16(0).unwrap();
            c.store_u64(0).unwrap();
            c.store_bit_zero().unwrap();
            ValidatorSet::parse(c.build().unwrap()).unwrap()
        });
        Timeline::new(&current, next.as_ref(), &TIMING, now)
    }

    fn view(wins: u8, won: bool, rounds_remaining: i32) -> RoundView {
        RoundView { current: true, voters: vec![], weight_remaining: if won { -1 } else { 1 }, rounds_remaining, wins, losses: 0, fate: Fate::Alive }
    }

    const CRITICAL: VoteSetup = VoteSetup { max_tot_rounds: 7, min_wins: 4, max_losses: 2, min_store_sec: 0, max_store_sec: 0, bit_price: 0, cell_price: 0 };

    #[test]
    fn next_pass_is_a_minute_away_while_a_vote_is_missing_and_the_next_round_once_done() {
        let t = timeline(START + 100, None);
        assert_eq!(next_pass_at(&t, START, 1, false), START + 100 + RETRY);
        assert_eq!(next_pass_at(&t, START, 0, false), START + 300, "done: the next round, at p34's planned end");
        assert_eq!(next_pass_at(&t, START, 0, true), START + 100 + RETRY, "something failed: a minute");
        // the next set is elected: its start is exact
        let elected = timeline(START + 250, Some(START + 310));
        assert_eq!(next_pass_at(&elected, START, 0, false), START + 310);
    }

    #[test]
    fn next_pass_waits_for_the_round_to_settle_and_skips_the_boundary() {
        let early = timeline(START + 10, None);
        assert_eq!(next_pass_at(&early, START, 1, false), START + SETTLE);
        // 20 s before the switch nothing is voted: the next pass is the next round's start
        let late = timeline(START + 290, Some(START + 300));
        assert_eq!(next_pass_at(&late, START, 1, false), START + 300);
        // overdue with no new set: votes go on in the same round
        let overdue = timeline(START + 400, None);
        assert_eq!(next_pass_at(&overdue, START, 1, false), START + 400 + RETRY);
        assert_eq!(next_pass_at(&overdue, START, 0, false), START + 400 + 60, "a 300 s round: looked at every minute");
    }

    #[test]
    fn earliest_acceptance_counts_the_rounds_still_needed() {
        let t = timeline(START + 100, None);
        // 4 wins needed, none yet: this round and three more
        assert_eq!(earliest_acceptance(&view(0, false, 7), &CRITICAL, &t), Some(START + 300 + 2 * 300));
        // 3 won, this one open: this round
        assert_eq!(earliest_acceptance(&view(3, false, 4), &CRITICAL, &t), Some(START + 100));
        // 3 won including this one: the next round
        assert_eq!(earliest_acceptance(&view(3, true, 4), &CRITICAL, &t), Some(START + 300));
    }

    #[test]
    fn rounds_count_from_one() {
        assert_eq!(round_number(&view(0, false, 7), &CRITICAL), 1);
        assert_eq!(round_number(&view(0, false, 0), &CRITICAL), 8);
    }

    #[test]
    fn an_older_config_is_stale() {
        let cfg = crate::emulate::tests::config_account();
        let known = |v: u64| Known { param: 14, target: HashBytes::ZERO, config_version: v.to_string() };
        assert!(check_version(&cfg, None).is_ok());
        assert!(check_version(&cfg, Some(&known(cfg.last_trans_lt))).is_ok());
        let err = check_version(&cfg, Some(&known(cfg.last_trans_lt + 1))).unwrap_err();
        assert!(format!("{err:#}").contains("stale"));
    }
}
