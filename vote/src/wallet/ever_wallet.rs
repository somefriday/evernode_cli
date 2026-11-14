//! EverWallet (ABI 2.3): one key, `sendTransaction`, and an address that follows from the key, so a new
//! wallet only needs funds: its first message deploys it. Also mints accounts for a test zerostate.

use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, bail, ensure};
use ed25519_dalek::SigningKey;
use tycho_types::abi::{AbiHeaderType, AbiType, AbiValue, AbiVersion, Function};
use tycho_types::models::{
    Account, AccountState, CurrencyCollection, OptionalAccount, SignatureContext, StateInit, StdAddr, StorageUsed,
};
use tycho_types::num::VarUint56;
use tycho_types::prelude::*;

use super::{Message, Transfer, Wallet};
use crate::config::ConfigAccount;
use crate::graphql::Graphql;

const CODE: &[u8] = include_bytes!("../../res/ever_wallet_code.boc");

/// Seconds a wallet message stays valid: messages can wait in the validators' queues for a while.
pub const TTL: u32 = 120;

fn state_init(public: &HashBytes) -> Result<StateInit> {
    Ok(StateInit {
        split_depth: None,
        special: None,
        code: Some(Boc::decode(CODE)?),
        data: Some(CellBuilder::build_from((public, 0u64))?),
        libraries: Dict::new(),
    })
}

/// The masterchain address of the EverWallet with this key.
pub fn address(public: &HashBytes) -> Result<StdAddr> {
    Ok(StdAddr::new(-1, *CellBuilder::build_from(&state_init(public)?)?.repr_hash()))
}

/// An EverWallet account in the masterchain with `balance` nanotokens, ready for a zerostate.
pub fn mint(public: &HashBytes, balance: u128) -> Result<(StdAddr, Cell)> {
    let mut account = Account {
        address: address(public)?.into(),
        storage_stat: Default::default(),
        last_trans_lt: 0,
        balance: CurrencyCollection::new(balance),
        state: AccountState::Active(state_init(public)?),
    };
    account.storage_stat.used = storage_used(&account)?;
    Ok((address(public)?, CellBuilder::build_from(OptionalAccount(Some(account)))?))
}

pub fn storage_used(account: &Account) -> Result<StorageUsed> {
    let cx = Cell::empty_context();
    let mut storage = CellBuilder::new();
    storage.store_u64(account.last_trans_lt)?;
    account.balance.store_into(&mut storage, cx)?;
    account.state.store_into(&mut storage, cx)?;
    let stats = storage.build_ext(cx)?.compute_unique_stats(usize::MAX).context("account is too big")?;
    Ok(StorageUsed { cells: VarUint56::new(stats.cell_count), bits: VarUint56::new(stats.bit_count) })
}

fn send_transaction_abi() -> Function {
    Function::builder(AbiVersion::V2_3, "sendTransaction")
        .with_headers([AbiHeaderType::PublicKey, AbiHeaderType::Time, AbiHeaderType::Expire])
        .with_inputs([
            ("dest", AbiType::Address),
            ("value", AbiType::Uint(128)),
            ("bounce", AbiType::Bool),
            ("flags", AbiType::Uint(8)),
            ("payload", AbiType::Cell),
        ])
        .build()
}

/// `sendTransaction(dest, value, bounce, flags 3, payload)`, signed: flags 3 pay fees separately and
/// ignore errors in the action phase. `init` deploys the wallet with this message.
pub fn send_transaction(
    key: &SigningKey,
    wallet: &StdAddr,
    dest: &StdAddr,
    value: u128,
    payload: Cell,
    init: Option<StateInit>,
) -> Result<Message> {
    let tokens = [
        AbiValue::address(dest.clone()).named("dest"),
        AbiValue::uint(128, value).named("value"),
        AbiValue::Bool(true).named("bounce"),
        AbiValue::uint(8, 3u8).named("flags"),
        AbiValue::Cell(payload).named("payload"),
    ];
    let time = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis() as u64;
    let expire_at = (time / 1000) as u32 + TTL;
    let message = send_transaction_abi()
        .encode_external(&tokens)
        .with_time(time)
        .with_expire_at(expire_at)
        .with_pubkey(&key.verifying_key())
        .build_message(wallet)?
        .with_state_init_opt(init)
        .sign(key, SignatureContext::EMPTY)?;
    Ok(Message { cell: CellBuilder::build_from(message)?, time, expire_at })
}

/// The `time` header (ms) of the last external message the wallet accepted: EverWallet keeps it after its
/// public key (`_pubkey:uint256 _timestamp:uint64`) and rejects any message that is not newer.
pub fn last_time(account: &Cell) -> Result<u64> {
    let account = account.parse::<OptionalAccount>()?.0.context("the wallet does not exist")?;
    let AccountState::Active(state) = account.state else { bail!("the wallet is not active") };
    let mut cs = state.data.as_ref().context("the wallet has no data")?.as_slice()?;
    cs.load_u256()?;
    Ok(cs.load_u64()?)
}

pub(super) fn transfer(wallet: &Wallet, chain: &Graphql, cfg: &ConfigAccount, dest: &StdAddr, value: u128, payload: Cell) -> Result<Transfer> {
    let key = &wallet.keys[0];
    let account = chain.account(&wallet.address)?.with_context(|| format!("{}: no such account; send it some EVER first", wallet.name))?;
    let init = match account.parse::<OptionalAccount>()?.0.map(|a| a.state) {
        Some(AccountState::Active(_)) => None,
        // funded but never used: its first message deploys it
        Some(AccountState::Uninit) => {
            let public = HashBytes(key.verifying_key().to_bytes());
            ensure!(address(&public)? == wallet.address, "{}: not the EverWallet address of its key", wallet.name);
            Some(state_init(&public)?)
        }
        _ => bail!("{}: the account is frozen or gone", wallet.name),
    };
    let message = send_transaction(key, &wallet.address, dest, value, payload, init)?;
    let tx = super::execute(chain, cfg, wallet, &account, message, dest)?;
    let sent = tx.sent_to(dest).with_context(|| format!("{}: sendTransaction ran but sent nothing (balance too low?)", wallet.name))?;
    Ok(Transfer::Sent(sent.hash))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::emulate::run_external;
    use crate::emulate::tests::config_account;

    #[test]
    fn a_funded_wallet_is_deployed_by_its_first_message() {
        let cfg = config_account();
        let key = SigningKey::from_bytes(&[0x21; 32]);
        let public = HashBytes(key.verifying_key().to_bytes());
        let wallet = address(&public).unwrap();
        let account = Account {
            address: wallet.clone().into(),
            storage_stat: Default::default(),
            last_trans_lt: 0,
            balance: CurrencyCollection::new(100_000_000_000),
            state: AccountState::Uninit,
        };
        let account = CellBuilder::build_from(OptionalAccount(Some(account))).unwrap();
        let dest = StdAddr::new(-1, HashBytes([0x55; 32]));
        let now = crate::now() as u32;
        let without = send_transaction(&key, &wallet, &dest, 1_000_000_000, Cell::empty_cell(), None).unwrap();
        let (r, _) = run_external(&cfg, &account, &without.cell, now).unwrap();
        assert!(!r.accepted, "an uninit account cannot run without its code");
        let init = Some(state_init(&public).unwrap());
        let with = send_transaction(&key, &wallet, &dest, 1_000_000_000, Cell::empty_cell(), init).unwrap();
        let (r, after) = run_external(&cfg, &account, &with.cell, now).unwrap();
        assert!(r.success && r.out.iter().any(|m| m.dst == Some(dest.address)), "exit code {}", r.exit_code);
        assert_eq!(last_time(&after).unwrap(), with.time);
    }
}
