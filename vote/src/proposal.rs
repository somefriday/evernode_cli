//! What a proposal changes, the checks it has to pass before anyone pays for it or votes on it, and how to
//! tell that it took effect. Every check mirrors a rule of the config contract (`res/ton-config-code.fc`)
//! or ever-node's own param parser (ever-block `ConfigParamEnum`), so a proposal that passes here is one the
//! contract registers, can accept, and a node can read.

use anyhow::{Context, Result, bail, ensure};
use tycho_types::prelude::*;

use crate::burn::{self, KeyKind};
use crate::config::{
    self, ConfigAccount, MAX_VALUE_CELLS, MAX_VALUE_DEPTH, PARAM_CONFIG_CODE, PARAM_DICTATOR, PARAM_ELECTOR_CODE, Proposal,
};

/// What validators are asked to accept.
#[derive(Clone)]
pub enum Change {
    /// `-999`: make `key` the dictator; a burn key burns the dictator
    Dictator(HashBytes),
    /// set config param `id` (>= 0) to `value`
    Param { id: i32, value: Cell },
    /// `-1000`: replace the config contract's code (the value's first reference)
    ConfigCode { code: Cell },
    /// `-1001`: replace the elector's code; the config forwards `upgrade_code(query_id, code, data)` to it
    ElectorCode { code: Cell, data: Cell },
}

/// Which `if_hash_equal` the proposal carries.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum IfHash {
    /// the default: the current value for params, none for the dictator and code upgrades
    Auto,
    /// only apply if the param still has its current value (0 if it is unset)
    Current,
    None,
    Hash(HashBytes),
}

impl std::str::FromStr for IfHash {
    type Err = anyhow::Error;
    fn from_str(s: &str) -> Result<Self> {
        Ok(match s {
            "auto" => Self::Auto,
            "current" => Self::Current,
            "none" => Self::None,
            hex => Self::Hash(HashBytes(hex::decode(hex)?.try_into().map_err(|_| anyhow::anyhow!("a hash is 32 bytes of hex"))?)),
        })
    }
}

#[derive(Default, Clone, Copy)]
pub struct Allow {
    /// appoint an ordinary key as dictator (that appoints, it does not burn)
    pub usable_key: bool,
    /// change params that steer governance, addresses, validator sets or code
    pub dangerous: bool,
}

/// What a change needs the operator's leave for, to be proposed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Needs {
    /// `--allow-usable-key`: an ordinary key becomes the dictator
    UsableKey,
    /// `--force`: governance params, system addresses, validator sets, code, or a param nothing reads
    Force,
}

/// `propose`'s checks for a proposal voted on by its hash, whoever registered it: what `propose` refuses
/// outright is refused here too (and a removal), and what it takes a flag for comes back as warnings.
pub fn check_registered(p: &Proposal) -> Result<Vec<String>> {
    Ok(Change::registered(p)?.check()?.into_iter().map(|(_, text)| text).collect())
}

/// A checked proposal, ready to register and vote on.
pub struct Plan {
    pub change: Change,
    pub proposal: Proposal,
    pub hash: HashBytes,
    pub critical: bool,
    pub warnings: Vec<String>,
}

/// Params whose change can take governance, the network's system contracts or its validator sets away
/// from the validators voting on it; proposing them needs `--force`.
fn danger(id: i32) -> Option<&'static str> {
    Some(match id {
        0 => "moves the config contract: governance continues wherever p0 points",
        1 => "moves the elector: validator sets come from wherever p1 points",
        8 => "switches global capabilities and the block version for every node",
        9 => "changes which params are mandatory",
        10 => "changes which params are critical (and so how hard they are to change)",
        11 => "changes the voting rules themselves (p11 is not critical on this network)",
        12 => "changes workchain descriptions",
        // seen on a test network: one address without an account, and no masterchain block followed
        31 => "lists the system contracts: ever-node stops the masterchain while an address in it has no account",
        32 | 34 | 36 => "validator sets are written by the elector; a voted value would fight it",
        PARAM_CONFIG_CODE => "replaces the config contract's code: whatever the new code does is the new governance",
        PARAM_ELECTOR_CODE => "replaces the elector's code: elections and stakes follow the new code",
        _ => return None,
    })
}

impl Change {
    pub fn param_id(&self) -> i32 {
        match self {
            Self::Dictator(_) => PARAM_DICTATOR,
            Self::Param { id, .. } => *id,
            Self::ConfigCode { .. } => PARAM_CONFIG_CODE,
            Self::ElectorCode { .. } => PARAM_ELECTOR_CODE,
        }
    }

    /// The `param_value` cell, as `perform_proposed_action` reads it.
    pub fn value(&self) -> Result<Cell> {
        Ok(match self {
            Self::Dictator(key) => CellBuilder::build_from(key)?,
            Self::Param { value, .. } => value.clone(),
            Self::ConfigCode { code } => {
                let mut b = CellBuilder::new();
                b.store_reference(code.clone())?;
                b.build()?
            }
            // change_elector_code forwards the value's slice after `0x4e436f64 query_id`: code and data refs
            Self::ElectorCode { code, data } => {
                let mut b = CellBuilder::new();
                b.store_reference(code.clone())?;
                b.store_reference(data.clone())?;
                b.build()?
            }
        })
    }

    pub fn describe(&self) -> String {
        match self {
            Self::Dictator(key) => format!("appoint dictator {key} — {}", burn::classify(key)),
            Self::Param { id, value } => match param_json(*id, value) {
                Some(json) => format!("set p{id} = {json}"),
                None => format!("set p{id} = cell {}", value.repr_hash()),
            },
            Self::ConfigCode { code } => format!("replace the config code with {}", code.repr_hash()),
            Self::ElectorCode { code, .. } => format!("replace the elector code with {}", code.repr_hash()),
        }
    }

    /// The change a registered proposal makes, in the form `propose` builds it. A removal has no change: the
    /// contract can never accept it.
    pub fn registered(p: &Proposal) -> Result<Self> {
        let value = p.value.clone().with_context(|| format!("it removes p{}, and the contract can never accept a removal", p.param_id))?;
        let change = match p.param_id {
            PARAM_DICTATOR => Self::Dictator(p.dictator_key().unwrap_or_default()),
            PARAM_CONFIG_CODE => Self::ConfigCode { code: value.reference_cloned(0).context("the config-code value holds no code")? },
            PARAM_ELECTOR_CODE => Self::ElectorCode {
                code: value.reference_cloned(0).context("the elector-code value holds no code")?,
                data: value.reference_cloned(1).context("the elector-code value holds no upgrade data")?,
            },
            id => Self::Param { id, value: value.clone() },
        };
        ensure!(change.value()?.repr_hash() == value.repr_hash(), "p{}'s value is not in the form propose builds: {}", p.param_id, change.describe());
        Ok(change)
    }

    /// The checks that depend only on the change, whoever registers it. Refused outright: a weak key, a param
    /// that acts when accepted, a value ever-node would not read exactly as written, or one the contract cannot
    /// store. Returned: what needs a flag to propose ([`Needs`]), and plain warnings.
    pub fn check(&self) -> Result<Vec<(Option<Needs>, String)>> {
        let id = self.param_id();
        let value = self.value()?;
        let mut found = Vec::new();
        match self {
            Self::Dictator(key) => match burn::classify(key) {
                KeyKind::Unusable => {}
                KeyKind::Weak => bail!("{key} is a small-order point: anyone could forge the dictator's signature"),
                KeyKind::Usable => found.push((Some(Needs::UsableKey), format!("{key} is an ordinary key: its holder becomes the dictator"))),
            },
            Self::Param { id, value } => {
                ensure!(
                    ![PARAM_DICTATOR, PARAM_CONFIG_CODE, PARAM_ELECTOR_CODE].contains(id),
                    "param {id} acts when accepted: use the dictator or code-upgrade change instead"
                );
                if *id < 0 {
                    found.push((Some(Needs::Force), format!("param {id} is negative: the contract stores it but nothing reads it")));
                } else {
                    let mut warnings = Vec::new();
                    check_param_value(*id as u32, value, &mut warnings)?;
                    found.extend(warnings.into_iter().map(|w| (None, w)));
                }
            }
            Self::ConfigCode { .. } | Self::ElectorCode { .. } => {}
        }
        if let Some(why) = danger(id) {
            found.push((Some(Needs::Force), format!("param {id}: {why}")));
        }
        // register_voting_proposal
        ensure!(value.repr_depth() <= MAX_VALUE_DEPTH, "the value is {} cells deep; the contract rejects 128 and more", value.repr_depth());
        config::data_size(&value, MAX_VALUE_CELLS).context("the value has more than 1024 distinct cells; the contract cannot price it")?;
        Ok(found)
    }

    /// Checks the change against the current config and the contract's rules and builds the proposal.
    pub fn plan(self, cfg: &ConfigAccount, if_hash: IfHash, allow: Allow) -> Result<Plan> {
        let id = self.param_id();
        let value = self.value()?;
        let mut warnings = Vec::new();
        for (needs, text) in self.check()? {
            match needs {
                Some(Needs::UsableKey) if !allow.usable_key => bail!("{text} (pass --allow-usable-key to allow it)"),
                Some(Needs::Force) if !allow.dangerous => bail!("{text} (pass --force to allow it)"),
                _ => warnings.push(text),
            }
        }
        let mandatory = cfg.is_mandatory(id)?;
        let if_hash_equal = match if_hash {
            IfHash::Auto if matches!(self, Self::Param { .. }) || mandatory => Some(cfg.param_hash(id)?),
            IfHash::Auto | IfHash::None => None,
            IfHash::Current => Some(cfg.param_hash(id)?),
            IfHash::Hash(h) => Some(h),
        };
        // the contract only checks this when the proposal has no if_hash_equal, whatever the value is
        ensure!(!mandatory || if_hash_equal.is_some(), "param {id} is mandatory (p9): the contract only registers it with if_hash_equal");
        if let Some(h) = if_hash_equal {
            ensure!(h == cfg.param_hash(id)?, "if_hash_equal {h} is not the current value's hash: the contract rejects the proposal");
        }
        let critical = cfg.is_critical(id)?;
        let proposal = Proposal { param_id: id, value: Some(value), if_hash_equal };
        Ok(Plan { hash: proposal.hash()?, change: self, proposal, critical, warnings })
    }

    /// Has the change taken effect? `elector_code` is the elector's current code hash (only read for
    /// elector upgrades).
    pub fn in_effect(&self, cfg: &ConfigAccount, elector_code: Option<HashBytes>) -> Result<bool> {
        Ok(match self {
            Self::Dictator(key) => cfg.public_key == *key,
            Self::Param { id, value } => cfg.param(*id)?.is_some_and(|v| v.repr_hash() == value.repr_hash()),
            Self::ConfigCode { code } => cfg.code_hash == *code.repr_hash(),
            Self::ElectorCode { code, .. } => elector_code == Some(*code.repr_hash()),
        })
    }
}

/// A value for param `id` must parse with ever-node's own parser and serialize back to the same cell,
/// so the network reads exactly what validators voted for.
pub fn check_param_value(id: u32, value: &Cell, warnings: &mut Vec<String>) -> Result<()> {
    let parsed = ever_block::ConfigParamEnum::construct_from_cell_and_number(to_ever(value)?, id)
        .with_context(|| format!("the value is not a valid p{id} for ever-node"))?;
    if let ever_block::ConfigParamEnum::ConfigParamAny(..) = parsed {
        warnings.push(format!("p{id} is not a param ever-node knows: it is stored but nothing reads it"));
        return Ok(());
    }
    let mut b = ever_block::BuilderData::new();
    parsed.write_to_cell(&mut b)?;
    let canonical = b.references().first().context("ever-block wrote no param cell")?.clone();
    ensure!(
        canonical.repr_hash().as_slice() == value.repr_hash().as_slice(),
        "the value is not in canonical form: ever-node reads it as {}",
        param_json(id as i32, &from_ever(&canonical)?).unwrap_or_default()
    );
    Ok(())
}

/// Code that behaves exactly like FunC-compiled `code`: it makes `code` the current code (c3) and jumps into
/// it with the stack untouched (`PUSHREFCONT code; DUP; POP c3; JMPX`). Selectors, get-methods, tick-tock
/// and calls through c3 all end up in `code`; only the code hash differs. For testing code-upgrade votes
/// live without changing what the contract does. Not for TVM Solidity code, which keeps its function
/// dictionary in c3 rather than the code root.
pub fn delegating_code(code: &Cell) -> Result<Cell> {
    let mut b = CellBuilder::new();
    b.store_u8(0x8a)?; // PUSHREFCONT
    b.store_reference(code.clone())?;
    b.store_u8(0x20)?; // DUP
    b.store_u16(0xed53)?; // POP c3
    b.store_u8(0xd9)?; // JMPX
    Ok(b.build()?)
}

/// Code that behaves exactly like `code` with another hash: a NOP after the root cell's last instruction.
/// Both electors' roots end in a throw (FunC: `THROWARG 11`, TVM Solidity: `THROW 11`), so the NOP never
/// runs, and the references (FunC's method table, Solidity's c3) are unchanged. For testing elector upgrades
/// by vote: the test network's Solidity elector installs it (`emulate.rs`, and live), and so does mainnet's
/// FunC elector in `simulate`.
pub fn equivalent_code(code: &Cell) -> Result<Cell> {
    let mut b = CellBuilder::new();
    b.store_slice(code.as_slice()?)?;
    b.store_u8(0x00)?; // NOP
    Ok(b.build()?)
}

/// Builds the value of param `id` from JSON in the zerostate format: `{"p15": {...}}` or just the inner value.
///
/// ever-node's reader is lenient: it ignores fields it does not know, reads a number's `_dec` twin instead
/// of its hex field, and wraps a number too big for its field, all without an error. So the value it built
/// must serialize back, with ever-node's own writer, to exactly what was written; otherwise the JSON is
/// refused, naming each field that would be read differently. A number's hex field may be left out next to
/// its `_dec` twin, which is then the one read.
pub fn param_from_json(id: i32, json: &str) -> Result<Cell> {
    ensure!(id >= 0, "JSON values exist only for params 0 and up");
    let value: serde_json::Value = serde_json::from_str(json).context("parse the value JSON")?;
    let key = format!("p{id}");
    let inner = match value {
        serde_json::Value::Object(ref m) if m.len() == 1 && m.contains_key(&key) => m[&key].clone(),
        other => other,
    };
    let mut map = serde_json::Map::new();
    map.insert(key.clone(), inner.clone());
    let params = ever_block_json::parse_config(&map)
        .map_err(|e| anyhow::anyhow!(without_source(&e)))
        .with_context(|| format!("p{id}: the JSON does not describe this param (write each field as `evernode vote param {id}` prints it: numbers as strings)"))?;
    let entry = params.config_params.get(ever_block::Serializable::write_to_bitstring(&(id as u32))?)?;
    let mut entry = entry.with_context(|| format!("p{id}: the JSON parser does not know this param; pass the value as a BOC"))?;
    let value = from_ever(&entry.checked_drain_reference()?)?;
    let read = param_json(id, &value).with_context(|| format!("p{id}: ever-node cannot write this value back as JSON; pass it as a BOC"))?;
    let differences = json_differences(&key, &inner, &read);
    ensure!(
        !differences.iter().any(|d| d.ends_with(".adnl_addr: not a field ever-node reads")),
        "{key}: ever-node's JSON reader drops each validator's adnl_addr, so a validator set cannot be given as JSON: pass it as a BOC (--value-boc @file; a validator set also needs --force)"
    );
    ensure!(
        differences.is_empty(),
        "{key}: ever-node would read this JSON differently from how it is written:\n  {}\nWrite each field as `evernode vote param {id}` prints it; a number's hex field may be left out next to its `_dec` field",
        differences.join("\n  ")
    );
    Ok(value)
}

/// ever-block's errors end with the Rust source file and line that raised them, which mean nothing to an
/// operator.
fn without_source(e: &impl std::fmt::Display) -> String {
    let text = e.to_string();
    match text.rsplit_once(' ') {
        Some((message, place)) if place.contains(".rs:") => message.to_string(),
        _ => text,
    }
}

/// Where JSON `written` differs from `read`, what ever-node made of it, by field path.
fn json_differences(path: &str, written: &serde_json::Value, read: &serde_json::Value) -> Vec<String> {
    use serde_json::Value;
    match (written, read) {
        (Value::Object(w), Value::Object(r)) => {
            let mut out = Vec::new();
            for (k, wv) in w {
                match r.get(k) {
                    Some(rv) => out.extend(json_differences(&format!("{path}.{k}"), wv, rv)),
                    None => out.push(format!("{path}.{k}: not a field ever-node reads")),
                }
            }
            for (k, rv) in r {
                // a hex field left out next to its decimal twin
                if !w.contains_key(k) && !w.contains_key(&format!("{k}_dec")) {
                    out.push(format!("{path}.{k}: missing; ever-node would use {rv}"));
                }
            }
            out
        }
        (Value::Array(w), Value::Array(r)) if w.len() == r.len() => {
            w.iter().zip(r).enumerate().flat_map(|(i, (a, b))| json_differences(&format!("{path}[{i}]"), a, b)).collect()
        }
        (w, r) if w == r => Vec::new(),
        (w, r) => vec![format!("{path}: written {w}, ever-node reads {r}")],
    }
}

/// What a change of param `id` from `now` to `new` changes, field by field (`field: now → new`), from
/// ever-node's JSON of both. A number is shown once, by its `_dec` field; a value ever-node has no JSON for
/// is shown by its cell hash.
pub fn field_changes(id: i32, now: Option<&Cell>, new: &Cell) -> Vec<String> {
    let json = |c: &Cell| param_json(id, c);
    match (now.map(json), json(new)) {
        (Some(Some(a)), Some(b)) => {
            let mut out = Vec::new();
            diff_fields("", &a, &b, &mut out);
            out
        }
        (None, Some(b)) => vec![format!("unset → {}", shown(&b))],
        _ => vec![format!(
            "{} → cell {}",
            now.map(|c| format!("cell {}", c.repr_hash())).unwrap_or_else(|| "unset".into()),
            new.repr_hash()
        )],
    }
}

fn diff_fields(path: &str, a: &serde_json::Value, b: &serde_json::Value, out: &mut Vec<String>) {
    use serde_json::Value;
    let field = |k: &str| if path.is_empty() { k.to_string() } else { format!("{path}.{k}") };
    match (a, b) {
        (Value::Object(x), Value::Object(y)) => {
            let keys: std::collections::BTreeSet<&String> = x.keys().chain(y.keys()).collect();
            for k in keys {
                // the hex twin of a `_dec` field says the same
                if x.contains_key(&format!("{k}_dec")) || y.contains_key(&format!("{k}_dec")) {
                    continue;
                }
                let name = field(k.strip_suffix("_dec").unwrap_or(k));
                match (x.get(k), y.get(k)) {
                    (Some(p), Some(q)) => diff_fields(&name, p, q, out),
                    (Some(p), None) => out.push(format!("{name}: {} → unset", shown(p))),
                    (None, Some(q)) => out.push(format!("{name}: unset → {}", shown(q))),
                    (None, None) => {}
                }
            }
        }
        (Value::Array(x), Value::Array(y)) if x.len() == y.len() => {
            for (i, (p, q)) in x.iter().zip(y).enumerate() {
                diff_fields(&format!("{path}[{i}]"), p, q, out);
            }
        }
        (p, q) if p == q => {}
        (p, q) => out.push(format!("{}: {} → {}", if path.is_empty() { "value" } else { path }, shown(p), shown(q))),
    }
}

/// A JSON value as text: strings without their quotes.
fn shown(v: &serde_json::Value) -> String {
    match v {
        serde_json::Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// What a registered proposal changes, in words.
pub fn describe_registered(p: &Proposal) -> String {
    let code = || p.value.as_ref().and_then(|v| v.reference_cloned(0)).map(|c| c.repr_hash().to_string()).unwrap_or_default();
    match (p.dictator_key(), &p.value) {
        (Some(k), _) => format!("appoint dictator {k} — {}", burn::classify(&k)),
        (None, Some(_)) if p.param_id == PARAM_CONFIG_CODE => format!("replace the config code with {}", code()),
        (None, Some(_)) if p.param_id == PARAM_ELECTOR_CODE => format!("replace the elector code with {}", code()),
        (None, Some(v)) => format!(
            "set p{} = {}",
            p.param_id,
            param_json(p.param_id, v).map(|j| j.to_string()).unwrap_or_else(|| format!("cell {}", v.repr_hash()))
        ),
        (None, None) => format!("remove p{} (the contract can never accept it)", p.param_id),
    }
}

/// What a registered proposal changes from the current config, line by line: the param's fields, or the key
/// or code it replaces.
pub fn registered_changes(cfg: &ConfigAccount, elector_code: Option<HashBytes>, p: &Proposal) -> Result<Vec<String>> {
    Ok(match (p.dictator_key(), &p.value) {
        (Some(k), _) => vec![format!("master key: {} → {} — {}", cfg.public_key, k, burn::classify(&k))],
        (None, Some(_)) if p.param_id == PARAM_CONFIG_CODE => vec![format!("config code: {} → {}", cfg.code_hash, p.target())],
        (None, Some(_)) if p.param_id == PARAM_ELECTOR_CODE => {
            vec![format!("elector code: {} → {}", elector_code.map(|h| h.to_string()).unwrap_or_else(|| "unknown".into()), p.target())]
        }
        (None, Some(v)) => field_changes(p.param_id, cfg.param(p.param_id)?.as_ref(), v),
        (None, None) => vec![format!("p{}: removed (the contract can never accept it)", p.param_id)],
    })
}

/// Is the change a proposal with `target` (see [`Proposal::target`]) makes to `param` in effect?
pub fn target_in_effect(cfg: &ConfigAccount, elector_code: Option<HashBytes>, param: i32, target: &HashBytes) -> Result<bool> {
    Ok(match param {
        PARAM_DICTATOR => cfg.public_key == *target,
        PARAM_CONFIG_CODE => cfg.code_hash == *target,
        PARAM_ELECTOR_CODE => elector_code == Some(*target),
        id => cfg.param(id)?.is_some_and(|v| v.repr_hash() == target),
    })
}

/// The value as ever-node's JSON (as in the zerostate), if it knows the param.
pub fn param_json(id: i32, value: &Cell) -> Option<serde_json::Value> {
    if id < 0 {
        return None;
    }
    let mut slice = ever_block::SliceData::load_cell(to_ever(value).ok()?).ok()?;
    ever_block_json::serialize_known_config_param(id as u32, &mut slice, ever_block_json::SerializationMode::Standart).ok()?
}

/// A BOC given as base64 or as `@path` (binary or base64 file).
pub fn load_boc(arg: &str) -> Result<Cell> {
    use base64::Engine;
    let bytes = match arg.strip_prefix('@') {
        Some(path) => {
            let raw = std::fs::read(path).with_context(|| format!("read {path}"))?;
            match base64::engine::general_purpose::STANDARD.decode(String::from_utf8_lossy(&raw).trim()) {
                Ok(decoded) => decoded,
                Err(_) => raw,
            }
        }
        None => base64::engine::general_purpose::STANDARD.decode(arg.trim()).context("the BOC is not base64")?,
    };
    Ok(Boc::decode(bytes)?)
}

pub fn to_ever(cell: &Cell) -> Result<ever_block::Cell> {
    ever_block::read_single_root_boc(Boc::encode(cell))
}

pub fn from_ever(cell: &ever_block::Cell) -> Result<Cell> {
    Ok(Boc::decode(ever_block::write_boc(cell)?)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Mainnet p17, read 2026-11-08.
    const P17: &str = r#"{"min_stake_dec":"10000000000000","min_stake":"0a9184e72a000","max_stake_dec":"10000000000000000","max_stake":"0d2386f26fc10000","min_total_stake_dec":"100000000000000","min_total_stake":"0b5af3107a4000","max_stake_factor":196608}"#;

    /// bd-m, 2026-11-13: an address in p31 with no account stopped the masterchain ("Can't find account
    /// data"); proposing a p31 change takes --force, and a voter joining one is warned.
    #[test]
    fn a_change_to_the_system_contracts_needs_force_and_warns_voters() {
        let cfg = crate::emulate::tests::config_account();
        let value = cfg.param(31).unwrap().expect("the template has p31");
        let change = || Change::Param { id: 31, value: value.clone() };
        let refused = format!("{:#}", change().plan(&cfg, IfHash::Auto, Allow::default()).err().expect("refused"));
        assert!(refused.contains("system contracts") && refused.contains("pass --force"), "{refused}");
        let plan = change().plan(&cfg, IfHash::Auto, Allow { dangerous: true, ..Allow::default() }).unwrap();
        assert!(check_registered(&plan.proposal).unwrap().iter().any(|w| w.contains("system contracts")));
    }

    /// A p18 entry dated in the future is an ordinary change: the tool works from the config as it is and
    /// leaves the timing of a price change to the validators who vote for it.
    #[test]
    fn storage_prices_dated_in_the_future_are_an_ordinary_change() {
        let cfg = crate::emulate::tests::config_account();
        let entry = |since: u64, mc_bit: &str| {
            format!(r#"{{"utime_since":{since},"bit_price_ps_dec":"1","cell_price_ps_dec":"500","mc_bit_price_ps_dec":"{mc_bit}","mc_cell_price_ps_dec":"500000"}}"#)
        };
        let future = param_from_json(18, &format!("[{},{}]", entry(0, "1000"), entry(crate::now() + 86400, "2000"))).unwrap();
        let plan = Change::Param { id: 18, value: future }.plan(&cfg, IfHash::Auto, Allow::default()).unwrap();
        assert!(plan.warnings.is_empty(), "{:?}", plan.warnings);
        assert!(check_registered(&plan.proposal).unwrap().is_empty());
    }

    /// Review 1, #26: a field of the wrong type names the field, not the Rust source line that noticed it.
    #[test]
    fn a_json_error_names_no_source_file() {
        let e = format!("{:#}", param_from_json(20, r#"{"flat_gas_limit": 5, "flat_gas_price": "1000"}"#).unwrap_err());
        assert!(e.contains("gas_price must be the integer") && !e.contains(".rs:"), "{e}");
    }

    fn p17(edit: impl FnOnce(&mut serde_json::Map<String, serde_json::Value>)) -> String {
        let mut v: serde_json::Value = serde_json::from_str(P17).unwrap();
        edit(v.as_object_mut().unwrap());
        v.to_string()
    }

    fn refusal(json: &str) -> String {
        format!("{:#}", param_from_json(17, json).unwrap_err())
    }

    #[test]
    fn every_param_ever_node_writes_reads_back_the_same() {
        let cfg = crate::emulate::tests::config_account();
        let mut checked = 0;
        let mut lossy = Vec::new();
        for id in 0..=100 {
            let Some(cell) = cfg.param(id).unwrap() else { continue };
            let Some(json) = param_json(id, &cell) else { continue };
            match param_from_json(id, &json.to_string()) {
                Ok(back) => assert_eq!(back.repr_hash(), cell.repr_hash(), "p{id}"),
                Err(e) => lossy.push(format!("p{id}: {e:#}")),
            }
            checked += 1;
        }
        assert!(checked > 20, "only {checked} params checked");
        // ever-node's reader drops a validator set's `adnl_addr`: such a value goes as a BOC (and proposing a
        // validator set needs --force anyway)
        assert_eq!(lossy.len(), 1, "{}", lossy.join("\n"));
        assert!(lossy[0].starts_with("p34:") && lossy[0].contains("drops each validator's adnl_addr") && lossy[0].contains("as a BOC"), "{}", lossy[0]);
    }

    #[test]
    fn json_that_ever_node_would_read_differently_is_refused() {
        // the three mainnet dry runs of 2026-11-08, each of which the config would have registered
        let hex_only = refusal(&p17(|m| {
            m.insert("min_stake".into(), format!("{:x}", 20_000_000_000_000u64).into());
        }));
        assert!(hex_only.contains("p17.min_stake: written"), "{hex_only}");
        let overflow = refusal(&p17(|m| {
            m.insert("max_stake_factor".into(), 4_294_967_297u64.into());
        }));
        assert!(overflow.contains("p17.max_stake_factor: written 4294967297, ever-node reads 1"), "{overflow}");
        let typo = refusal(&p17(|m| {
            m.insert("max_stake_factr".into(), 262_144.into());
        }));
        assert!(typo.contains("p17.max_stake_factr: not a field ever-node reads"), "{typo}");
        // a number written as a string where ever-node writes a number
        let typed = refusal(&p17(|m| {
            m.insert("max_stake_factor".into(), "196608".into());
        }));
        assert!(typed.contains("written \"196608\", ever-node reads 196608"), "{typed}");
    }

    #[test]
    fn a_change_by_dec_fields_alone_is_read_as_written_and_listed_field_by_field() {
        let now = param_from_json(17, P17).unwrap();
        let new = param_from_json(
            17,
            &p17(|m| {
                m.insert("min_stake_dec".into(), "20000000000000".into());
                m.remove("min_stake");
                m.remove("max_stake");
                m.remove("min_total_stake");
                m.insert("max_stake_factor".into(), 262_144.into());
            }),
        )
        .unwrap();
        assert_eq!(field_changes(17, Some(&now), &new), vec!["max_stake_factor: 196608 → 262144", "min_stake: 10000000000000 → 20000000000000"]);
        assert!(field_changes(17, Some(&now), &now).is_empty());
    }
}
