//! The test network's elector (TON Labs' TVM Solidity port, `res/Elector.abi.json` from ever-node's `bin/`):
//! only what a validator needs to keep staking, so that validator sets — and with them voting rounds — keep
//! changing. Mainnet runs a FunC elector, whose storage this does not read.

use std::sync::OnceLock;

use anyhow::{Context, Result, bail};
use num_traits::ToPrimitive;
use tycho_types::abi::{AbiValue, Contract, NamedAbiValue};
use tycho_types::models::{AccountState, OptionalAccount};
use tycho_types::prelude::*;

const ABI: &str = include_str!("../res/Elector.abi.json");

fn contract() -> &'static Contract {
    static C: OnceLock<Contract> = OnceLock::new();
    C.get_or_init(|| serde_json::from_str(ABI).expect("valid elector ABI"))
}

fn field<'a>(fields: &'a [NamedAbiValue], name: &str) -> Result<&'a AbiValue> {
    fields.iter().find(|f| f.name.as_ref() == name).map(|f| &f.value).with_context(|| format!("elector field {name} missing"))
}

/// The open election's id (`elect_at`), 0 when none is open, read from the elector's storage fields
/// (no TVM needed). Only `m_election_open` and `m_cur_elect.elect_at` are used: ever-node's bundled ABI
/// describes a newer elector (7-field `Member`, with BLS keys) than the template's, whose `Member` has 5
/// fields (ton-labs-contracts `solidity/elector/Elector.sol`), so nothing after `elect_close` is read.
pub fn active_election(account: &Cell) -> Result<u32> {
    let account = account.parse::<OptionalAccount>()?.0.context("elector does not exist")?;
    let AccountState::Active(state) = &account.state else { bail!("elector is not active") };
    let data = state.data.as_ref().context("elector has no data")?;
    let c = contract();
    let fields = NamedAbiValue::load_tuple_ext(&c.fields[..4], c.abi_version, false, true, &mut data.as_slice()?)?;
    let AbiValue::Bool(open) = field(&fields, "m_election_open")? else { bail!("m_election_open is not bool") };
    let AbiValue::Tuple(cur) = field(&fields, "m_cur_elect")? else { bail!("m_cur_elect is not a tuple") };
    let AbiValue::Uint(_, elect_at) = field(cur, "elect_at")? else { bail!("elect_at is not uint") };
    Ok(if *open { elect_at.to_u32().context("elect_at")? } else { 0 })
}
