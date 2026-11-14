//! Changes Everscale's network configuration by validator vote — first of all burning the config master
//! key, the "dictator".
//!
//! The config contract (TON's FunC config, `res/ton-config-code.fc`) lets whoever holds its master key
//! change any config param, replace the config and elector code, or appoint a new key. Validators can do
//! all of that by vote, and can replace the key itself with a proposal for param `-999`. This tool checks a
//! proposal against the contract's rules and dry-runs it on the contract's real code (`emulate.rs`),
//! registers it, and votes in each round with every current validator key it can sign with, until the
//! change takes effect. It reaches the chain through a GraphQL endpoint (`graphql.rs`); a vote needs
//! nothing else but the validator's key.

mod burn;
mod campaign;
mod config;
mod control;
mod elector;
mod emulate;
mod follow;
mod graphql;
mod node;
mod proposal;
mod report;
mod stake;
mod timeline;
mod wallet;

use std::io::Read;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::sleep;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, bail, ensure};
use clap::{Parser, Subcommand, ValueEnum};
use serde::Deserialize;
use tycho_types::models::StdAddr;
use tycho_types::prelude::*;

use crate::campaign::{Options, Target};
use crate::config::ConfigAccount;
use crate::node::Net;
use crate::proposal::{Allow, Change, IfHash, Plan};

pub const EVER: u128 = 1_000_000_000;

/// Progress lines go to stderr while stdout carries a JSON report (`follow`).
static PROGRESS_ON_STDERR: AtomicBool = AtomicBool::new(false);

pub fn progress_on_stderr() -> bool {
    PROGRESS_ON_STDERR.load(Ordering::Relaxed)
}

/// A progress line: on stdout, or on stderr when stdout is for a JSON report.
#[macro_export]
macro_rules! say {
    ($($arg:tt)*) => {
        if $crate::progress_on_stderr() { eprintln!($($arg)*) } else { println!($($arg)*) }
    };
}

#[derive(Parser)]
#[command(version, about = "Change Everscale's config by validator vote: burn the config master key, set params, upgrade code")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

/// What validators are asked to accept (one of the first six options).
#[derive(clap::Args)]
#[command(group(clap::ArgGroup::new("change").required(true).multiple(false)))]
struct ChangeArgs {
    /// burn the dictator key: appoint the burn key derived from --label
    #[arg(long, group = "change")]
    burn: bool,
    /// appoint this dictator key (hex)
    #[arg(long, group = "change", value_name = "KEY")]
    dictator: Option<String>,
    /// set config param N to --value-json or --value-boc
    #[arg(long, group = "change", value_name = "N", allow_negative_numbers = true)]
    param: Option<i32>,
    /// replace the config contract's code (BOC: base64, or @file)
    #[arg(long, group = "change", value_name = "BOC")]
    config_code: Option<String>,
    /// replace the elector's code (BOC: base64, or @file)
    #[arg(long, group = "change", value_name = "BOC")]
    elector_code: Option<String>,
    /// a registered proposal, by hash (cast only: the other commands need the change itself)
    #[arg(long, group = "change", value_name = "HASH")]
    proposal: Option<String>,

    /// the public label the burn key is derived from (see `burn-key`)
    #[arg(long, default_value = burn::DEFAULT_LABEL)]
    label: String,
    /// the param's value as ever-node's JSON: {"p15": {...}} or the value alone
    #[arg(long, requires = "param", conflicts_with = "value_boc")]
    value_json: Option<String>,
    /// the param's value as a BOC (base64, or @file)
    #[arg(long, requires = "param")]
    value_boc: Option<String>,
    /// data handed to the elector's upgrade hook (BOC; default: an empty cell)
    #[arg(long, requires = "elector_code")]
    elector_data: Option<String>,
    /// if_hash_equal: auto (the current value for params), current, none, or a hash
    #[arg(long, default_value = "auto")]
    if_hash: IfHash,
    /// allow appointing an ordinary key (a new dictator instead of a burn)
    #[arg(long)]
    allow_usable_key: bool,
    /// allow changes to governance params, system addresses, validator sets and code
    #[arg(long)]
    force: bool,
}

#[derive(clap::Args)]
struct LoopArgs {
    /// seconds between checks
    #[arg(long, default_value_t = 15)]
    poll: u64,
    /// seconds after a validator set starts before voting in it
    #[arg(long, default_value_t = 40)]
    settle: u32,
    /// give up after this many seconds (default: run until the outcome)
    #[arg(long)]
    timeout: Option<u64>,
}

#[derive(clap::Args)]
struct Wallets {
    /// masterchain wallet that pays the proposal's price (default: the net file's payer)
    #[arg(long)]
    from: Option<PathBuf>,
    /// seconds to keep the proposal (clamped to p11 min_store_sec..max_store_sec; default: the minimum)
    #[arg(long)]
    ttl: Option<u32>,
    /// masterchain wallet that carries every vote as an internal message (default: the net file's relay;
    /// without one, votes go as external messages, signed by the validator alone)
    #[arg(long)]
    relay: Option<PathBuf>,
    /// vote by external messages even if the net file sets a relay
    #[arg(long, conflicts_with = "relay")]
    external: bool,
}

#[derive(Subcommand)]
enum Cmd {
    /// Print the burn key for a label and why nobody can sign for it
    BurnKey {
        #[arg(long, default_value = burn::DEFAULT_LABEL)]
        label: String,
    },
    /// Show the dictator key, the voting rules, the validator set and every proposal's round
    Status {
        #[arg(long)]
        net: PathBuf,
        /// also show which listed node holds which current validator key
        #[arg(long)]
        keys: bool,
    },
    /// Show a config param as ever-node's JSON (or BOC), its hash, and the rules a proposal for it follows
    Param {
        #[arg(long)]
        net: PathBuf,
        #[arg(allow_negative_numbers = true)]
        id: i32,
    },
    /// Check a proposal, dry-run it on the contract, and register it
    Propose {
        #[arg(long)]
        net: PathBuf,
        #[command(flatten)]
        change: ChangeArgs,
        #[command(flatten)]
        wallets: Wallets,
        /// stop after the checks and the dry run
        #[arg(long)]
        dry_run: bool,
        /// pay without asking
        #[arg(long)]
        yes: bool,
    },
    /// Vote for a registered proposal with every current validator key the listed nodes hold, and wait until
    /// the config counts each vote in this round
    Cast {
        #[arg(long)]
        net: PathBuf,
        #[command(flatten)]
        change: ChangeArgs,
        #[command(flatten)]
        wallets: Wallets,
        /// keep voting every round until the proposal is gone
        #[arg(long)]
        watch: bool,
        #[command(flatten)]
        looping: LoopArgs,
    },
    /// Vote every round until the change takes effect; a change nobody registered is registered first, paid
    /// from the payer (--from, or the net file's payer) after asking
    Run {
        #[arg(long)]
        net: PathBuf,
        #[command(flatten)]
        change: ChangeArgs,
        #[command(flatten)]
        wallets: Wallets,
        #[command(flatten)]
        looping: LoopArgs,
        /// pay without asking
        #[arg(long)]
        yes: bool,
    },
    /// One pass for a proposal joined on this host, for evernode's schedule: vote with every key the listed
    /// nodes hold that the config has not counted in the current round, then print a JSON report of what was
    /// seen and done and when the next pass is due. Progress goes to stderr. What an earlier pass saw comes
    /// back in the net description (`known`).
    Follow {
        #[arg(long)]
        net: PathBuf,
        /// the joined proposal
        #[arg(long, value_name = "HASH")]
        proposal: String,
        /// read the chain and report what a pass would do, without voting
        #[arg(long)]
        dry_run: bool,
    },
    /// `run --burn`: burn the dictator key
    Burn {
        #[arg(long)]
        net: PathBuf,
        #[arg(long, default_value = burn::DEFAULT_LABEL)]
        label: String,
        #[command(flatten)]
        wallets: Wallets,
        #[command(flatten)]
        looping: LoopArgs,
        /// pay without asking
        #[arg(long)]
        yes: bool,
    },
    /// Play a proposal's whole life offline on the contract's current code and state, with stand-in validators
    Simulate {
        #[arg(long)]
        net: PathBuf,
        #[command(flatten)]
        change: ChangeArgs,
        #[arg(long, default_value_t = 3)]
        validators: usize,
    },
    /// Send the dictator's no-op command (action 0, needs a valid signature) and report whether it was accepted
    DictatorPing {
        #[arg(long)]
        net: PathBuf,
        /// the dictator's key file (default: `dictator` in the net file)
        #[arg(long)]
        keys: Option<PathBuf>,
        /// exit with an error unless the outcome is this one
        #[arg(long)]
        expect: Option<PingOutcome>,
    },
    /// While a dictator exists: set a param directly with its key (no vote; test networks)
    DictatorSet {
        #[arg(long)]
        net: PathBuf,
        /// the dictator's key file (default: `dictator` in the net file)
        #[arg(long)]
        keys: Option<PathBuf>,
        #[arg(long, allow_negative_numbers = true)]
        param: i32,
        #[arg(long, conflicts_with = "value_boc")]
        value_json: Option<String>,
        #[arg(long)]
        value_boc: Option<String>,
    },
    /// Print code that behaves like the config contract's current code but has another hash (base64 BOC),
    /// to test a code-upgrade vote without changing behaviour; `--elector` wraps the elector's code
    WrapCode {
        #[arg(long)]
        net: PathBuf,
        #[arg(long)]
        elector: bool,
    },
    /// Keep one validator node staking in every election (test network: its wallet is in the net file)
    Elect {
        #[arg(long)]
        net: PathBuf,
        #[arg(long)]
        node: String,
        /// stake in EVER
        #[arg(long, default_value_t = 100_000)]
        stake: u64,
        #[arg(long, default_value_t = 20)]
        poll: u64,
    },
    /// Generate an ed25519 key pair (JSON: public, secret)
    GenKey,
    /// The key pair ever-cli derives from a seed phrase (JSON: public, secret). The phrase is read from
    /// standard input: as a process argument, every user of the host could read it.
    KeyFromPhrase,
    /// Make an EverWallet: a wallet file with a new key (or --secret) and its masterchain address. Send the
    /// address some EVER and the wallet's first message deploys it. The account BOC with --balance is for a
    /// test zerostate.
    MintWallet {
        #[arg(long)]
        name: String,
        /// balance in EVER of the zerostate account
        #[arg(long, default_value_t = 0)]
        balance: u64,
        /// mint it for this existing secret (hex) instead of a new key: same address, fresh balance
        #[arg(long)]
        secret: Option<String>,
    },
    /// Print the representation hash of a BOC's root cell (base64, or @file): what if_hash_equal and code
    /// hashes compare
    BocHash {
        #[arg(long)]
        boc: String,
    },
    /// Show the dictator key and proposals of a config account BOC (base64), e.g. from a zerostate
    InspectConfig {
        #[arg(long)]
        boc: String,
    },
    /// Replace the dictator key in a config account BOC (base64) for a test zerostate; prints the new BOC
    PatchConfigKey {
        #[arg(long)]
        boc: String,
        #[arg(long)]
        key: String,
    },
}

#[derive(Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum PingOutcome {
    /// a transaction ran: the key controls the config
    Accepted,
    /// the dry run rejects the key's signature
    Rejected,
    /// the dry run accepted it, but no transaction ran: this says nothing about the key
    NotDelivered,
}

#[derive(Deserialize)]
pub struct KeyFile {
    pub public: HashBytes,
    pub secret: HashBytes,
}

fn main() -> Result<()> {
    match Cli::parse().cmd {
        Cmd::BurnKey { label } => {
            let (key, counter) = burn::derive(&label);
            println!("label:   {label}");
            println!("counter: {counter} (sha256(label \":\" counter) for the first counter that is not a curve point)");
            println!("key:     {key}");
            println!("status:  {}", burn::classify(&key));
        }
        Cmd::Status { net, keys } => report::status(&Net::load(&net)?, keys)?,
        Cmd::Param { net, id } => {
            let cfg = Net::load(&net)?.config()?;
            let critical = cfg.is_critical(id)?;
            println!(
                "p{id}: hash {} mandatory {} critical {} ({} winning rounds)",
                cfg.param_hash(id)?,
                cfg.is_mandatory(id)?,
                critical,
                cfg.vote_setup(critical)?.min_wins
            );
            match cfg.param(id)? {
                Some(v) => match proposal::param_json(id, &v) {
                    Some(json) => println!("{}", serde_json::to_string_pretty(&serde_json::json!({ format!("p{id}"): json }))?),
                    None => println!("{}", Boc::encode_base64(v)),
                },
                None => println!("unset"),
            }
        }
        Cmd::Propose { net, change, wallets, dry_run, yes } => {
            let net = Net::load(&net)?;
            let cfg = net.config()?;
            let plan = change.plan(&cfg)?;
            report::print_plan(&cfg, &plan, wallets.ttl)?;
            // checked before the dry run, which would register it again (an extension, at another price)
            ensure!(!campaign::in_effect(net.chain()?, &cfg, &plan, false)?, "nothing to do: the change is already in effect");
            ensure!(
                cfg.proposal(&plan.hash).is_none(),
                "proposal {} is already registered: there is nothing to pay; vote for it with --proposal {}",
                plan.hash,
                plan.hash
            );
            report::warn_round_end(&cfg)?;
            let opts = Options { yes, ..options(wallets, LoopArgs { poll: 5, settle: 0, timeout: Some(400) }) };
            if dry_run {
                let payer = campaign::payer(&net, opts.payer.as_deref()).map(|w| w.address).unwrap_or(StdAddr::new(-1, HashBytes([0x77; 32])));
                let r = campaign::Registration::new(&cfg, &plan, opts.ttl)?;
                r.dry_run(&cfg, &payer)?;
                println!(
                    "dry run: the config answers 0x{:08x} (registered) and keeps {} EVER, the price for {}s",
                    config::ANSWER_PROPOSAL_OK,
                    ever(r.price),
                    r.ttl
                );
                return Ok(());
            }
            // register, then stop at the first voting pass (no votes are sent: no node is asked to)
            report::report(campaign::run(&net, Target::Plan(&plan), &Options { propose: true, once: true, vote: false, ..opts })?);
        }
        Cmd::Cast { net, change, wallets, watch, looping } => {
            let net = Net::load(&net)?;
            let cfg = net.config()?;
            let opts = Options { once: !watch, ..options(wallets, looping) };
            let outcome = match change.hash()? {
                Some(hash) => {
                    // whoever registered it, it gets propose's checks before any vote
                    if let Some(status) = cfg.proposal(&hash) {
                        report::print_registered(&cfg, &status.proposal)?;
                    }
                    campaign::run(&net, Target::Hash(hash), &opts)?
                }
                None => {
                    let plan = change.plan(&cfg)?;
                    report::print_plan(&cfg, &plan, opts.ttl)?;
                    campaign::run(&net, Target::Plan(&plan), &opts)?
                }
            };
            report::report(outcome);
        }
        Cmd::Run { net, change, wallets, looping, yes } => {
            let net = Net::load(&net)?;
            let cfg = net.config()?;
            ensure!(change.proposal.is_none(), "run needs the change itself, not a proposal hash (use cast --watch)");
            let plan = change.plan(&cfg)?;
            report::print_plan(&cfg, &plan, wallets.ttl)?;
            // only a proposal it registers can be registered too late in the round
            if cfg.proposal(&plan.hash).is_none() {
                report::warn_round_end(&cfg)?;
            }
            report::report(campaign::run(&net, Target::Plan(&plan), &Options { propose: true, yes, ..options(wallets, looping) })?);
        }
        Cmd::Follow { net, proposal, dry_run } => {
            PROGRESS_ON_STDERR.store(true, Ordering::Relaxed);
            let net = Net::load(&net)?;
            let report = follow::pass(&net, parse_key(&proposal)?, dry_run)?;
            println!("{}", serde_json::to_string(&report)?);
        }
        Cmd::Burn { net, label, wallets, looping, yes } => {
            let net = Net::load(&net)?;
            let cfg = net.config()?;
            let plan = Change::Dictator(burn::derive(&label).0).plan(&cfg, IfHash::Auto, Allow::default())?;
            report::print_plan(&cfg, &plan, wallets.ttl)?;
            // only a proposal it registers can be registered too late in the round
            if cfg.proposal(&plan.hash).is_none() {
                report::warn_round_end(&cfg)?;
            }
            report::report(campaign::run(&net, Target::Plan(&plan), &Options { propose: true, yes, ..options(wallets, looping) })?);
        }
        Cmd::Simulate { net, change, validators } => {
            let net = Net::load(&net)?;
            let cfg = net.config()?;
            let plan = change.plan(&cfg)?;
            report::print_plan(&cfg, &plan, None)?;
            let elector = match plan.change {
                Change::ElectorCode { .. } => net.chain()?.account(&cfg.elector_address()?)?,
                _ => None,
            };
            report::simulate(&cfg, &plan, validators, elector)?;
        }
        Cmd::DictatorPing { net, keys, expect } => {
            let net = Net::load(&net)?;
            let keys = keys.or_else(|| net.dictator.clone()).context("no dictator key file: pass --keys or set dictator in the net file")?;
            let outcome = report::dictator_ping(&net, &keys)?;
            if let Some(expect) = expect {
                ensure!(outcome == expect, "unexpected outcome");
            }
        }
        Cmd::DictatorSet { net, keys, param, value_json, value_boc } => {
            let net = Net::load(&net)?;
            let keys = keys.or_else(|| net.dictator.clone()).context("no dictator key file: pass --keys or set dictator in the net file")?;
            let value = match (value_json, value_boc) {
                (Some(json), None) => proposal::param_from_json(param, &json)?,
                (None, Some(boc)) => proposal::load_boc(&boc)?,
                _ => bail!("pass --value-json or --value-boc"),
            };
            report::dictator_set(&net, &keys, param, value)?;
        }
        Cmd::WrapCode { net, elector } => {
            let net = Net::load(&net)?;
            let cfg = net.config()?;
            let addr = if elector { cfg.elector_address()? } else { cfg.address.clone() };
            let account = net.chain()?.account(&addr)?.context("no such account")?;
            let code = match account.parse::<tycho_types::models::OptionalAccount>()?.0.map(|a| a.state) {
                Some(tycho_types::models::AccountState::Active(state)) => state.code.context("no code")?,
                _ => bail!("{addr} is not active"),
            };
            // the elector is FunC on mainnet and TVM Solidity on the test network: a NOP after the root suits both
            let wrapped = if elector { proposal::equivalent_code(&code)? } else { proposal::delegating_code(&code)? };
            println!("{}", Boc::encode_base64(wrapped));
        }
        Cmd::Elect { net, node, stake, poll } => {
            let net = Net::load(&net)?;
            stake::run_elect(&net, net.node(&node)?, stake as u128 * EVER, Duration::from_secs(poll))?;
        }
        Cmd::GenKey => println!("{}", wallet::key_pair_json(&ed25519_dalek::SigningKey::from_bytes(&rand::random()))),
        Cmd::KeyFromPhrase => {
            let mut phrase = String::new();
            std::io::stdin().read_to_string(&mut phrase).context("reading the seed phrase from standard input")?;
            println!("{}", wallet::key_pair_json(&wallet::key_from_phrase(&phrase)?));
        }
        Cmd::MintWallet { name, balance, secret } => {
            let key = ed25519_dalek::SigningKey::from_bytes(&match secret {
                Some(s) => parse_key(&s)?.0,
                None => rand::random(),
            });
            let public = HashBytes(key.verifying_key().to_bytes());
            let (address, account) = wallet::ever_wallet::mint(&public, balance as u128 * EVER)?;
            println!(
                "{}",
                serde_json::to_string_pretty(&serde_json::json!({
                    "name": name, "type": "ever_wallet", "address": address.to_string(),
                    "public": public.to_string(), "secret": hex::encode(key.to_bytes()),
                    "balance": balance.to_string(), "boc": Boc::encode_base64(account),
                }))?
            );
        }
        Cmd::BocHash { boc } => println!("{}", proposal::load_boc(&boc)?.repr_hash()),
        Cmd::InspectConfig { boc } => {
            let cfg = ConfigAccount::parse(&Boc::decode_base64(boc.trim())?)?;
            println!("config   {} seqno {} balance {} EVER", cfg.address, cfg.seqno, ever(cfg.balance));
            println!("dictator {} — {}", cfg.public_key, burn::classify(&cfg.public_key));
            println!("proposals {}", cfg.proposals.len());
        }
        Cmd::PatchConfigKey { boc, key } => {
            let cell = Boc::decode_base64(boc.trim())?;
            println!("{}", Boc::encode_base64(config::patch_account_key(&cell, &parse_key(&key)?)?));
        }
    }
    Ok(())
}

impl ChangeArgs {
    fn hash(&self) -> Result<Option<HashBytes>> {
        self.proposal.as_deref().map(parse_key).transpose()
    }

    fn change(&self) -> Result<Change> {
        let load = proposal::load_boc;
        Ok(if self.burn {
            Change::Dictator(burn::derive(&self.label).0)
        } else if let Some(key) = &self.dictator {
            Change::Dictator(parse_key(key)?)
        } else if let Some(id) = self.param {
            let value = match (&self.value_json, &self.value_boc) {
                (Some(json), None) => proposal::param_from_json(id, json)?,
                (None, Some(boc)) => load(boc)?,
                _ => bail!("--param needs --value-json or --value-boc (the contract never accepts removing a param)"),
            };
            Change::Param { id, value }
        } else if let Some(code) = &self.config_code {
            Change::ConfigCode { code: load(code)? }
        } else if let Some(code) = &self.elector_code {
            let data = match &self.elector_data {
                Some(d) => load(d)?,
                None => Cell::empty_cell(),
            };
            Change::ElectorCode { code: load(code)?, data }
        } else {
            bail!("this command needs the change itself, not a proposal hash")
        })
    }

    fn plan(&self, cfg: &ConfigAccount) -> Result<Plan> {
        self.change()?.plan(cfg, self.if_hash, Allow { usable_key: self.allow_usable_key, dangerous: self.force })
    }
}

fn options(w: Wallets, l: LoopArgs) -> Options {
    Options {
        propose: false,
        vote: true,
        once: false,
        payer: w.from,
        yes: false,
        ttl: w.ttl,
        relay: w.relay,
        external: w.external,
        poll: Duration::from_secs(l.poll),
        settle: l.settle,
        timeout: l.timeout.map(Duration::from_secs),
    }
}

fn parse_key(s: &str) -> Result<HashBytes> {
    let bytes: [u8; 32] = hex::decode(s.trim())?.try_into().map_err(|_| anyhow::anyhow!("expected 32 bytes of hex"))?;
    Ok(HashBytes(bytes))
}

pub fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs()
}

/// Asks `question` on the terminal: true for y or yes. Without a terminal nobody can answer, so it fails.
pub fn confirm(question: &str) -> Result<bool> {
    use std::io::{BufRead, IsTerminal, Write};
    ensure!(std::io::stdin().is_terminal(), "{question}: no terminal to ask in; pass --yes to pay without asking");
    print!("{question} [y/N] ");
    std::io::stdout().flush()?;
    let mut answer = String::new();
    std::io::stdin().lock().read_line(&mut answer)?;
    Ok(matches!(answer.trim().to_lowercase().as_str(), "y" | "yes"))
}

pub fn ever(nano: u128) -> String {
    format!("{}.{:09}", nano / EVER, nano % EVER)
}

pub fn short(h: &HashBytes) -> String {
    h.to_string()[..12].to_string()
}

pub fn stamp() -> String {
    let t = now();
    format!("[{:02}:{:02}:{:02}Z]", t / 3600 % 24, t / 60 % 60, t % 60)
}

pub fn wait_for(timeout: Duration, mut check: impl FnMut() -> Result<bool>) -> Result<()> {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if check()? {
            return Ok(());
        }
        sleep(Duration::from_secs(3));
    }
    bail!("timed out after {timeout:?}")
}

