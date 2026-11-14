//! The config contract Everscale runs at `-1:5555…`: TON's FunC config contract with the "dictator"
//! (config master key) and validator voting on proposals. Layouts and rules follow `res/ton-config-code.fc`
//! (the April 2020 source differs only in a getter), checked against the deployed code in `emulate.rs`:
//!
//! - storage: `cfg_dict:^Cell seqno:uint32 public_key:uint256 vote_dict:(HashmapE 256 ConfigProposalStatus)`
//! - `public_key` is the dictator: any external message signed with it can change any param, replace the
//!   config or elector code, or appoint a new key (`perform_action`)
//! - a proposal sets one param; params `-999` (dictator), `-1000` (config code) and `-1001` (elector code)
//!   also act (`perform_proposed_action`). Critical params (p10) need p11 `critical_params.min_wins`
//!   rounds in which validators holding more than 3/4 of the weight voted, others `normal_params.min_wins`.

use std::collections::HashSet;

use anyhow::{Context, Result, bail, ensure};
use tycho_types::models::{Account, AccountState, OptionalAccount, StdAddr};
use tycho_types::prelude::*;

pub use crate::timeline::{ElectionTiming, Timeline};

/// "nVPR": new voting proposal (internal message from the masterchain, with the storage price attached).
pub const OP_NEW_PROPOSAL: u32 = 0x6e565052;
/// "Vote": a validator's vote, internal or external.
pub const OP_VOTE: u32 = 0x566f7465;
/// "VotE": the tag inside the signed part of an internal vote.
pub const VOTE_SIGN_TAG: u32 = 0x566f7445;
/// The config's answer to a registered proposal; a rejected one is answered with the error code.
pub const ANSWER_PROPOSAL_OK: u32 = 0xee565052;
/// The config answers a vote with `status + 0xd6745240` (see [`vote_status`]).
pub const ANSWER_VOTE_BASE: u32 = 0xd6745240;
/// Param ids that act when accepted (`perform_proposed_action`).
pub const PARAM_DICTATOR: i32 = -999;
pub const PARAM_CONFIG_CODE: i32 = -1000;
pub const PARAM_ELECTOR_CODE: i32 = -1001;
/// `register_voting_proposal` rejects values this deep (the TON mainnet build allows 256).
pub const MAX_VALUE_DEPTH: u16 = 127;
/// `compute_data_size(param_val, 1024)` throws above this many distinct cells.
pub const MAX_VALUE_CELLS: u64 = 1024;

pub const DEFAULT_CONFIG_ADDR: &str = "-1:5555555555555555555555555555555555555555555555555555555555555555";

pub struct ConfigAccount {
    /// the `Account` cell as read from the network (for the emulator)
    pub account: Cell,
    pub address: StdAddr,
    pub balance: u128,
    pub code_hash: HashBytes,
    pub params: Dict<u32, Cell>,
    pub seqno: u32,
    /// The dictator.
    pub public_key: HashBytes,
    pub proposals: Vec<(HashBytes, ProposalStatus)>,
    /// The logical time of the contract's last transaction: its version, which only grows. The contract
    /// runs a tick-tock transaction in every masterchain block, so this is about the block it was read at.
    pub last_trans_lt: u64,
}

impl ConfigAccount {
    /// Parses the config contract from an `Account` cell (`getaccountstate`).
    pub fn parse(account_cell: &Cell) -> Result<Self> {
        let account = account_cell.parse::<OptionalAccount>()?.0.context("config account does not exist")?;
        let address = match &account.address {
            tycho_types::models::IntAddr::Std(a) => a.clone(),
            _ => bail!("config address is not a std address"),
        };
        let AccountState::Active(state) = &account.state else { bail!("config account is not active") };
        let code_hash = *state.code.as_ref().context("config account has no code")?.repr_hash();
        let data = state.data.as_ref().context("config account has no data")?;
        let mut cs = data.as_slice()?;
        let params = Dict::<u32, Cell>::from_raw(Some(cs.load_reference_cloned()?));
        let seqno = cs.load_u32()?;
        let public_key = cs.load_u256()?;
        let votes = Dict::<HashBytes, ProposalStatus>::load_from(&mut cs)?;
        ensure!(cs.is_data_empty() && cs.is_refs_empty(), "unexpected trailing data in config storage");
        let proposals = votes.iter().collect::<Result<Vec<_>, _>>()?;
        Ok(Self {
            account: account_cell.clone(),
            address,
            balance: account.balance.tokens.into_inner(),
            code_hash,
            params,
            seqno,
            public_key,
            proposals,
            last_trans_lt: account.last_trans_lt,
        })
    }

    pub fn param(&self, id: i32) -> Result<Option<Cell>> {
        Ok(self.params.get(id as u32)?)
    }

    pub fn required(&self, id: i32) -> Result<Cell> {
        self.param(id)?.with_context(|| format!("config param {id} is missing"))
    }

    /// `if_hash_equal` of the param's current value as the contract computes it: 0 when unset.
    pub fn param_hash(&self, id: i32) -> Result<HashBytes> {
        Ok(self.param(id)?.map(|c| *c.repr_hash()).unwrap_or(HashBytes::ZERO))
    }

    pub fn current_vset(&self) -> Result<ValidatorSet> {
        ValidatorSet::parse(self.required(34)?)
    }

    /// p36: the next validator set, once elected; it becomes p34 at its `utime_since`.
    pub fn next_vset(&self) -> Result<Option<ValidatorSet>> {
        self.param(36)?.map(ValidatorSet::parse).transpose()
    }

    /// p15: how long a round lasts and when its elections open and close.
    pub fn election_timing(&self) -> Result<ElectionTiming> {
        let p15 = self.required(15)?;
        let mut cs = p15.as_slice()?;
        Ok(ElectionTiming { elected_for: cs.load_u32()?, start_before: cs.load_u32()?, end_before: cs.load_u32()? })
    }

    /// When the current round ends and where the elections are, at `now`.
    pub fn timeline(&self, now: u32) -> Result<Timeline> {
        Ok(Timeline::new(&self.current_vset()?, self.next_vset()?.as_ref(), &self.election_timing()?, now))
    }

    /// p10: proposals for these params must be critical and use the critical setup.
    pub fn is_critical(&self, id: i32) -> Result<bool> {
        param_set_contains(&self.required(10)?, id)
    }

    /// p9: these params can be neither removed nor proposed without `if_hash_equal`.
    pub fn is_mandatory(&self, id: i32) -> Result<bool> {
        param_set_contains(&self.required(9)?, id)
    }

    /// p11: the normal or critical voting setup.
    pub fn vote_setup(&self, critical: bool) -> Result<VoteSetup> {
        let p11 = self.required(11)?;
        let mut cs = p11.as_slice()?;
        ensure!(cs.load_u8()? == 0x91, "p11: bad tag");
        let normal = cs.load_reference()?;
        let critical_cell = cs.load_reference()?;
        VoteSetup::parse(if critical { critical_cell } else { normal })
    }

    /// p8 capabilities (ever-block numbering).
    pub fn capabilities(&self) -> Result<u64> {
        let p8 = self.required(8)?;
        let mut cs = p8.as_slice()?;
        ensure!(cs.load_u8()? == 0xc4, "p8: bad tag");
        cs.load_u32()?;
        Ok(cs.load_u64()?)
    }

    pub fn elector_address(&self) -> Result<StdAddr> {
        Ok(StdAddr::new(-1, self.required(1)?.as_slice()?.load_u256()?))
    }

    pub fn proposal(&self, hash: &HashBytes) -> Option<&ProposalStatus> {
        self.proposals.iter().find(|(h, _)| h == hash).map(|(_, p)| p)
    }
}

/// p9 and p10: `HashmapE 32 True` keyed by signed param ids.
fn param_set_contains(set: &Cell, id: i32) -> Result<bool> {
    Ok(Dict::<u32, ()>::from_raw(Some(set.clone())).get(id as u32)?.is_some())
}

/// `cfg_proposal_status#ce expires:uint32 proposal:^ConfigProposal is_critical:Bool
///  voters:(HashmapE 16 uint32) remaining_weight:int64 validator_set_id:uint256
///  rounds_remaining:uint8 wins:uint8 losses:uint8`
#[derive(Debug, Clone)]
pub struct ProposalStatus {
    pub expires: u32,
    pub proposal: Proposal,
    pub critical: bool,
    /// validator indices (in the p34 of `vset_id`) that voted in that round
    pub voters: Vec<u16>,
    pub weight_remaining: i64,
    pub vset_id: HashBytes,
    pub rounds_remaining: u8,
    pub wins: u8,
    pub losses: u8,
}

impl<'a> Load<'a> for ProposalStatus {
    fn load_from(cs: &mut CellSlice<'a>) -> Result<Self, tycho_types::error::Error> {
        if cs.load_u8()? != 0xce {
            return Err(tycho_types::error::Error::InvalidTag);
        }
        let expires = cs.load_u32()?;
        let proposal = Proposal::parse(cs.load_reference()?)?;
        let critical = cs.load_bit()?;
        let voters = Dict::<u16, u32>::load_from(cs)?;
        let voters = voters.keys().collect::<Result<Vec<_>, _>>()?;
        Ok(Self {
            expires,
            proposal,
            critical,
            voters,
            weight_remaining: cs.load_u64()? as i64,
            vset_id: cs.load_u256()?,
            rounds_remaining: cs.load_u8()?,
            wins: cs.load_u8()?,
            losses: cs.load_u8()?,
        })
    }
}

/// A proposal as the contract will see it at its next vote in the current round.
///
/// The contract updates a proposal lazily: the first vote after p34 changed (or a tick-tock that happens
/// to scan it) counts the previous round as lost if it was not won, spends a round and resets the
/// voters — once, however many rounds went by (`register_vote`, `update_proposal_status`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RoundView {
    /// the stored state belongs to the current p34
    pub current: bool,
    pub voters: Vec<u16>,
    pub weight_remaining: i64,
    pub rounds_remaining: i32,
    pub wins: u8,
    pub losses: u8,
    pub fate: Fate,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Fate {
    Alive,
    /// `expires <= now`: deleted at the next vote
    Expired,
    /// more than `max_losses` lost rounds, or out of rounds: deleted at the next vote
    Discarded,
}

impl RoundView {
    /// The proposal already collected more than 3/4 of the weight this round.
    pub fn won_this_round(&self) -> bool {
        self.weight_remaining < 0
    }
}

impl ProposalStatus {
    pub fn view(&self, vset: &ValidatorSet, setup: &VoteSetup, now: u32) -> RoundView {
        let mut v = RoundView {
            current: self.vset_id == vset.id(),
            voters: self.voters.clone(),
            weight_remaining: self.weight_remaining,
            rounds_remaining: self.rounds_remaining as i32,
            wins: self.wins,
            losses: self.losses,
            fate: Fate::Alive,
        };
        if self.expires <= now {
            v.fate = Fate::Expired;
            return v;
        }
        if !v.current {
            if self.weight_remaining >= 0 {
                v.losses = v.losses.saturating_add(1);
            }
            v.rounds_remaining -= 1;
            if v.losses > setup.max_losses || v.rounds_remaining < 0 {
                v.fate = Fate::Discarded;
            }
            v.voters.clear();
            v.weight_remaining = vset.win_threshold() as i64;
        }
        v
    }
}

/// `cfg_proposal#f3 param_id:int32 param_value:(Maybe ^Cell) if_hash_equal:(Maybe uint256)`
#[derive(Debug, Clone)]
pub struct Proposal {
    pub param_id: i32,
    pub value: Option<Cell>,
    pub if_hash_equal: Option<HashBytes>,
}

impl Proposal {
    pub fn parse(cell: &DynCell) -> Result<Self, tycho_types::error::Error> {
        let mut cs = cell.as_slice()?;
        if cs.load_u8()? != 0xf3 {
            return Err(tycho_types::error::Error::InvalidTag);
        }
        let param_id = cs.load_u32()? as i32;
        let value = if cs.load_bit()? { Some(cs.load_reference_cloned()?) } else { None };
        let if_hash_equal = if cs.load_bit()? { Some(cs.load_u256()?) } else { None };
        Ok(Self { param_id, value, if_hash_equal })
    }

    pub fn build(&self) -> Result<Cell> {
        let mut b = CellBuilder::new();
        b.store_u8(0xf3)?;
        b.store_u32(self.param_id as u32)?;
        match &self.value {
            Some(v) => {
                b.store_bit_one()?;
                b.store_reference(v.clone())?;
            }
            None => b.store_bit_zero()?,
        }
        match &self.if_hash_equal {
            Some(h) => {
                b.store_bit_one()?;
                b.store_u256(h)?;
            }
            None => b.store_bit_zero()?,
        }
        Ok(b.build()?)
    }

    /// The proposal id the contract uses: the hash of the proposal cell.
    pub fn hash(&self) -> Result<HashBytes> {
        Ok(*self.build()?.repr_hash())
    }

    /// What the chain shows once the proposal is in effect: the new master key (-999), the new code's hash
    /// (-1000, -1001), or the param's new value hash. A proposal without a value can never be accepted.
    pub fn target(&self) -> HashBytes {
        match self.param_id {
            PARAM_DICTATOR => self.dictator_key().unwrap_or_default(),
            PARAM_CONFIG_CODE | PARAM_ELECTOR_CODE => {
                self.value.as_ref().and_then(|v| v.reference_cloned(0)).map(|c| *c.repr_hash()).unwrap_or_default()
            }
            _ => self.value.as_ref().map(|v| *v.repr_hash()).unwrap_or_default(),
        }
    }

    /// The key a `-999` proposal appoints.
    pub fn dictator_key(&self) -> Option<HashBytes> {
        if self.param_id != PARAM_DICTATOR {
            return None;
        }
        Some(match &self.value {
            Some(v) => v.as_slice().and_then(|mut cs| cs.load_u256()).unwrap_or_default(),
            None => HashBytes::ZERO,
        })
    }
}

/// `cfg_vote_cfg#36 min_tot_rounds:uint8 max_tot_rounds:uint8 min_wins:uint8 max_losses:uint8
///  min_store_sec:uint32 max_store_sec:uint32 bit_price:uint32 cell_price:uint32`
/// (`min_tot_rounds` is not used by the contract)
#[derive(Debug, Clone, Copy)]
pub struct VoteSetup {
    pub max_tot_rounds: u8,
    pub min_wins: u8,
    pub max_losses: u8,
    pub min_store_sec: u32,
    pub max_store_sec: u32,
    pub bit_price: u32,
    pub cell_price: u32,
}

impl VoteSetup {
    fn parse(cell: &DynCell) -> Result<Self> {
        let mut cs = cell.as_slice()?;
        ensure!(cs.load_u8()? == 0x36, "p11: bad ConfigProposalSetup tag");
        cs.load_u8()?;
        Ok(Self {
            max_tot_rounds: cs.load_u8()?,
            min_wins: cs.load_u8()?,
            max_losses: cs.load_u8()?,
            min_store_sec: cs.load_u32()?,
            max_store_sec: cs.load_u32()?,
            bit_price: cs.load_u32()?,
            cell_price: cs.load_u32()?,
        })
    }

    /// The ttl the contract will store: at least `min_store_sec` (or it rejects), at most `max_store_sec`.
    pub fn clamp_ttl(&self, ttl: Option<u32>) -> u32 {
        ttl.unwrap_or(self.min_store_sec).clamp(self.min_store_sec, self.max_store_sec.max(self.min_store_sec))
    }

    /// `register_voting_proposal`: `(bit_price·(bits+1024) + cell_price·(refs+2)) · ttl`, with bits and
    /// refs from `compute_data_size(param_val, 1024)`.
    pub fn price(&self, value: Option<&Cell>, ttl: u32) -> Result<u128> {
        let (_, bits, refs) = match value {
            Some(v) => data_size(v, MAX_VALUE_CELLS).context("proposal value has more than 1024 distinct cells")?,
            None => (0, 0, 0),
        };
        let ttl = ttl.min(self.max_store_sec) as u128;
        let pps = self.bit_price as u128 * (bits as u128 + 1024) + self.cell_price as u128 * (refs as u128 + 2);
        Ok(pps * ttl)
    }
}

/// TVM `CDATASIZE`: distinct cells, their data bits and their references, `None` above `max_cells`.
pub fn data_size(root: &Cell, max_cells: u64) -> Option<(u64, u64, u64)> {
    let mut seen = HashSet::new();
    let mut stack = vec![root.clone()];
    let (mut cells, mut bits, mut refs) = (0u64, 0u64, 0u64);
    while let Some(cell) = stack.pop() {
        if !seen.insert(*cell.repr_hash()) {
            continue;
        }
        cells += 1;
        if cells > max_cells {
            return None;
        }
        bits += cell.bit_len() as u64;
        refs += cell.reference_count() as u64;
        stack.extend(cell.references().cloned());
    }
    Some((cells, bits, refs))
}

/// `validators_ext#12 utime_since:uint32 utime_until:uint32 total:uint16 main:uint16 total_weight:uint64
///  list:(HashmapE 16 ValidatorDescr)`
pub struct ValidatorSet {
    /// the contract identifies a round by the hash of this cell (`validator_set_id`)
    pub cell: Cell,
    pub utime_since: u32,
    pub utime_until: u32,
    pub total_weight: u64,
    pub list: Vec<Validator>,
}

pub struct Validator {
    pub idx: u16,
    pub public_key: HashBytes,
    pub weight: u64,
    /// the config contract can read this entry; it rejects votes from any other (exit 41)
    pub votable: bool,
    pub tag: u8,
}

impl ValidatorSet {
    pub fn parse(cell: Cell) -> Result<Self> {
        let mut cs = cell.as_slice()?;
        ensure!(cs.load_u8()? == 0x12, "p34 is not validators_ext#12");
        let utime_since = cs.load_u32()?;
        let utime_until = cs.load_u32()?;
        cs.load_u16()?;
        cs.load_u16()?;
        let total_weight = cs.load_u64()?;
        let dict = Dict::<u16, ValidatorDescr>::load_from(&mut cs)?;
        let mut list = Vec::new();
        for item in dict.iter() {
            let (idx, d) = item?;
            list.push(Validator { idx, public_key: d.public_key, weight: d.weight, votable: d.tag & !0x20 == 0x53, tag: d.tag });
        }
        Ok(Self { cell, utime_since, utime_until, total_weight, list })
    }

    pub fn id(&self) -> HashBytes {
        *self.cell.repr_hash()
    }

    /// `muldiv(total_weight, 3, 4)`: a round is won when the voters' weight exceeds this.
    pub fn win_threshold(&self) -> u64 {
        ((self.total_weight as u128 * 3) / 4) as u64
    }

    /// The weight of validators whose entries the contract can read; a round needs more than
    /// [`Self::win_threshold`] of it.
    pub fn votable_weight(&self) -> u64 {
        self.list.iter().filter(|v| v.votable).map(|v| v.weight).sum()
    }
}

/// The descriptors ever-block writes: `validator#53 public_key:SigPubKey weight:uint64`, `#73` with an ADNL
/// address, `#93` with an ADNL address and `mc_seq_no_since`, `#74` with a BLS key. The config contract reads
/// only `#53` and `#73` (`unpack_validator_descr`: `tag & ~0x20 == 0x53`).
struct ValidatorDescr {
    tag: u8,
    public_key: HashBytes,
    weight: u64,
}

impl<'a> Load<'a> for ValidatorDescr {
    fn load_from(cs: &mut CellSlice<'a>) -> Result<Self, tycho_types::error::Error> {
        let tag = cs.load_u8()?;
        if ![0x53, 0x73, 0x93, 0x74].contains(&tag) || cs.load_u32()? != 0x8e81278a {
            return Err(tycho_types::error::Error::InvalidTag);
        }
        let public_key = cs.load_u256()?;
        let weight = cs.load_u64()?;
        // the rest (adnl address, since-seqno, BLS key) is not needed
        let rest = cs.size_bits();
        cs.skip_first(rest, cs.size_refs())?;
        Ok(Self { tag, public_key, weight })
    }
}

/// Body of an internal message that registers a proposal:
/// `0x6e565052 query_id:uint64 expire_at:uint32 proposal:^ConfigProposal critical:Bool`.
/// `ttl` below 2^30 is relative to the time the config processes the message.
pub fn new_proposal_body(query_id: u64, ttl: u32, proposal: &Proposal, critical: bool) -> Result<Cell> {
    ensure!(ttl < 1 << 30, "ttl must be relative (below 2^30)");
    let mut b = CellBuilder::new();
    b.store_u32(OP_NEW_PROPOSAL)?;
    b.store_u64(query_id)?;
    b.store_u32(ttl)?;
    b.store_reference(proposal.build()?)?;
    b.store_bit(critical)?;
    Ok(b.build()?)
}

/// What the config answered a registration with (`register_voting_proposal`; errors are negated codes).
pub fn registration_answer(tag: u32) -> &'static str {
    match tag {
        ANSWER_PROPOSAL_OK => "registered",
        0xe2646356 => "if_hash_equal does not match the current value",
        0xcd506e6c => "the param is mandatory (p9): it needs if_hash_equal and cannot be removed",
        0xc2616456 => "the value is too deep (cell depth >= 128)",
        0xc3726954 => "the param is critical (p10): the proposal must be critical",
        0xc5787069 => "ttl is below p11 min_store_sec",
        0xc3726955 => "the proposal exists with a different criticality",
        0xc16c7245 => "the proposal already exists and expires later",
        0xf0617924 => "not enough value: price + 2^30 nanotokens are needed",
        _ => "unknown answer",
    }
}

/// What the config answered a vote with: `status + 0xd6745240` (`proceed_register_vote`).
pub fn vote_status(tag: u32) -> Option<(i32, &'static str)> {
    let status = tag.wrapping_sub(ANSWER_VOTE_BASE) as i32;
    let text = match status {
        -1 => "proposal not found, expired or discarded",
        -2 => "already voted in this round",
        2 => "vote counted",
        6 => "accepted (normal)",
        7 => "accepted (critical)",
        _ => return None,
    };
    Some((status, text))
}

/// The bytes a validator signs for an internal vote: `"VotE" idx:uint16 proposal_hash:uint256`
/// (`check_data_signature(msg_body, …)`, 38 bytes).
pub fn vote_sign_data(idx: u16, proposal_hash: &HashBytes) -> Vec<u8> {
    let mut data = Vec::with_capacity(38);
    data.extend_from_slice(&VOTE_SIGN_TAG.to_be_bytes());
    data.extend_from_slice(&idx.to_be_bytes());
    data.extend_from_slice(proposal_hash.as_slice());
    data
}

/// Body of an internal vote: `0x566f7465 query_id:uint64 signature:bits512 "VotE" idx:uint16 phash:uint256`.
/// Any masterchain account can deliver it; the signature binds it to the validator.
pub fn vote_body(query_id: u64, signature: &[u8; 64], idx: u16, proposal_hash: &HashBytes) -> Result<Cell> {
    let mut b = CellBuilder::new();
    b.store_u32(OP_VOTE)?;
    b.store_u64(query_id)?;
    b.store_raw(signature, 512)?;
    b.store_raw(&vote_sign_data(idx, proposal_hash), 38 * 8)?;
    Ok(b.build()?)
}

/// The signed part of an external vote: `"Vote" seqno:uint32 valid_until:uint32 idx:uint16 phash:uint256`
/// (46 bytes; `check_data_signature(in_msg, …)` covers its data). External votes need no wallet, but they
/// use the contract's seqno — the dictator's — so they go one at a time.
pub fn external_vote(seqno: u32, valid_until: u32, idx: u16, proposal_hash: &HashBytes) -> Result<Cell> {
    let mut b = CellBuilder::new();
    b.store_u32(OP_VOTE)?;
    b.store_u32(seqno)?;
    b.store_u32(valid_until)?;
    b.store_u16(idx)?;
    b.store_u256(proposal_hash)?;
    Ok(b.build()?)
}

/// An external message to `dst` with `body`.
pub fn external_message(dst: &StdAddr, body: Cell) -> Result<Cell> {
    use tycho_types::models::{ExtInMsgInfo, MsgInfo, OwnedMessage};
    let msg = OwnedMessage {
        info: MsgInfo::ExtIn(ExtInMsgInfo { src: None, dst: dst.clone().into(), import_fee: Default::default() }),
        init: None,
        body: (tycho_types::cell::CellSliceRange::full(body.as_ref()), body.clone()),
        layout: None,
    };
    Ok(CellBuilder::build_from(msg)?)
}

/// The part of a dictator's external message that its signature covers (`slice_hash(in_msg)` after the
/// signature): `action:uint32 seqno:uint32 valid_until:uint32 …`. Action 0 is a no-op that still
/// needs a valid signature and bumps the seqno, so it proves the key works without changing anything.
pub fn dictator_action(action: u32, seqno: u32, valid_until: u32, args: Option<CellBuilder>) -> Result<Cell> {
    let mut b = CellBuilder::new();
    b.store_u32(action)?;
    b.store_u32(seqno)?;
    b.store_u32(valid_until)?;
    if let Some(args) = args {
        b.store_builder(&args)?;
    }
    Ok(b.build()?)
}

/// The dictator's "set param" action (`0x43665021`): `param_index:int32 param_value:^Cell`.
pub fn set_param_args(id: i32, value: Cell) -> Result<CellBuilder> {
    let mut args = CellBuilder::new();
    args.store_u32(id as u32)?;
    args.store_reference(value)?;
    Ok(args)
}

/// `signature:bits512` followed by the signed action, as one external message body.
pub fn signed_body(signature: &[u8; 64], signed: &Cell) -> Result<Cell> {
    let mut b = CellBuilder::new();
    b.store_raw(signature, 512)?;
    b.store_slice(signed.as_slice()?)?;
    Ok(b.build()?)
}

/// Replaces the dictator key in a config `Account` (zerostate generation and tests only).
pub fn patch_account_key(account: &Cell, key: &HashBytes) -> Result<Cell> {
    let mut acc: Account = account.parse::<OptionalAccount>()?.0.context("no account")?;
    // the patched account must serialize exactly like the original apart from the key
    ensure!(
        CellBuilder::build_from(OptionalAccount(Some(acc.clone())))?.repr_hash() == account.repr_hash(),
        "config account does not round-trip through tycho-types"
    );
    let AccountState::Active(state) = &mut acc.state else { bail!("config account is not active") };
    let data = state.data.as_ref().context("config account has no data")?;
    let mut cs = data.as_slice()?;
    let cfg = cs.load_reference_cloned()?;
    let seqno = cs.load_u32()?;
    cs.load_u256()?;
    let mut b = CellBuilder::new();
    b.store_reference(cfg)?;
    b.store_u32(seqno)?;
    b.store_u256(key)?;
    b.store_slice(cs)?;
    state.data = Some(b.build()?);
    acc.storage_stat.used = crate::wallet::ever_wallet::storage_used(&acc)?;
    Ok(CellBuilder::build_from(OptionalAccount(Some(acc)))?)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn leaf(bits: u8) -> Cell {
        CellBuilder::build_from(bits).unwrap()
    }

    #[test]
    fn data_size_counts_refs_per_distinct_cell() {
        // root -> (a, a): one distinct child referenced twice, as CDATASIZE counts it
        let a = leaf(7);
        let mut b = CellBuilder::new();
        b.store_u32(1).unwrap();
        b.store_reference(a.clone()).unwrap();
        b.store_reference(a).unwrap();
        let root = b.build().unwrap();
        assert_eq!(data_size(&root, 10), Some((2, 32 + 8, 2)));
        assert_eq!(data_size(&root, 1), None);
    }

    #[test]
    fn proposal_layout() {
        let p = Proposal { param_id: PARAM_DICTATOR, value: Some(leaf(1)), if_hash_equal: Some(HashBytes([7; 32])) };
        let cell = p.build().unwrap();
        // tag + id + maybe-ref bit + maybe-hash bit + hash
        assert_eq!(cell.bit_len(), 8 + 32 + 1 + 1 + 256);
        assert_eq!(cell.reference_count(), 1);
        let back = Proposal::parse(cell.as_ref()).unwrap();
        assert_eq!((back.param_id, back.if_hash_equal), (PARAM_DICTATOR, Some(HashBytes([7; 32]))));
    }

    #[test]
    fn vote_body_layout() {
        let body = vote_body(5, &[9; 64], 3, &HashBytes([1; 32])).unwrap();
        assert_eq!(body.bit_len(), 32 + 64 + 512 + 32 + 16 + 256);
        let mut cs = body.as_slice().unwrap();
        assert_eq!(cs.load_u32().unwrap(), OP_VOTE);
        cs.skip_first(64 + 512, 0).unwrap();
        assert_eq!(cs.load_u32().unwrap(), VOTE_SIGN_TAG);
        assert_eq!(cs.load_u16().unwrap(), 3);
    }

    #[test]
    fn answers() {
        assert_eq!(vote_status(ANSWER_VOTE_BASE + 7).unwrap().0, 7);
        assert_eq!(vote_status(ANSWER_VOTE_BASE.wrapping_sub(2)).unwrap().0, -2);
        assert_eq!(registration_answer(0xcd506e6c), "the param is mandatory (p9): it needs if_hash_equal and cannot be removed");
    }
}
