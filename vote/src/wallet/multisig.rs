//! SafeMultisig and its variants (SetcodeMultisig, SurfMultisig; ABI 2.0): their transfer and getter
//! functions are the same (`res/SafeMultisigWallet.abi.json`).
//!
//! A single custodian sends with `sendTransaction`. With several, a transfer is a multisig transaction: a
//! custodian submits it (`submitTransaction`, which counts as its confirmation), others confirm it
//! (`confirmTransaction`), and the confirmation that reaches the required count sends it. A run confirms
//! with every key it holds. Custodians on other machines run the same command with their own keys: it
//! confirms the pending transaction with the same destination, value and payload instead of submitting
//! another. The wallet's state is read with its own getters, run locally on the account (`emulate.rs`).

use std::sync::LazyLock;
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, bail, ensure};
use ed25519_dalek::SigningKey;
use num_traits::ToPrimitive;
use tycho_types::abi::{AbiValue, Contract, Function, NamedAbiValue};
use tycho_types::models::{AnyAddr, SignatureContext, StdAddr};
use tycho_types::prelude::*;

use super::ever_wallet::TTL;
use super::{Message, Transfer, Wallet};
use crate::config::ConfigAccount;
use crate::graphql::Graphql;
use crate::{now, short};

static ABI: LazyLock<Contract> =
    LazyLock::new(|| serde_json::from_str(include_str!("../../res/SafeMultisigWallet.abi.json")).expect("the multisig ABI parses"));

fn function(name: &str) -> &'static Function {
    ABI.functions.get(name).expect("a function of the multisig ABI")
}

fn public(key: &SigningKey) -> HashBytes {
    HashBytes(key.verifying_key().to_bytes())
}

struct Custodian {
    index: u8,
    pubkey: HashBytes,
}

/// A multisig transaction waiting for confirmations.
struct Pending {
    id: u64,
    mask: u32,
    required: u8,
    received: u8,
    dest: StdAddr,
    value: u128,
    payload: Cell,
}

/// A call of function `name`, signed by custodian `key`.
fn call(key: &SigningKey, wallet: &StdAddr, name: &str, tokens: &[NamedAbiValue]) -> Result<Message> {
    let time = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis() as u64;
    let expire_at = (time / 1000) as u32 + TTL;
    let message = function(name)
        .encode_external(tokens)
        .with_time(time)
        .with_expire_at(expire_at)
        .with_pubkey(&key.verifying_key())
        .build_message(wallet)?
        .sign(key, SignatureContext::EMPTY)?;
    Ok(Message { cell: CellBuilder::build_from(message)?, time, expire_at })
}

/// What getter `name` answers on `account`.
fn get(cfg: &ConfigAccount, wallet: &StdAddr, account: &Cell, name: &str) -> Result<AbiValue> {
    let f = function(name);
    let time = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis() as u64;
    let (_, message) = f.encode_external(&[]).with_time(time).with_expire_at(now() as u32 + 60).build_message_without_signature(wallet)?;
    let bodies = crate::emulate::run_getter(cfg, account, &CellBuilder::build_from(message)?, now() as u32)?;
    let body = bodies.first().with_context(|| format!("{name} answered nothing"))?;
    let mut values = f.decode_output(body.as_slice()?)?;
    ensure!(!values.is_empty(), "{name} answered no value");
    Ok(values.swap_remove(0).value)
}

/// The tuples in an array answer.
fn tuples(value: AbiValue) -> Result<Vec<Vec<NamedAbiValue>>> {
    let AbiValue::Array(_, items) = value else { bail!("expected an array, got {value:?}") };
    items
        .into_iter()
        .map(|item| match item {
            AbiValue::Tuple(fields) => Ok(fields),
            other => bail!("expected a tuple, got {other:?}"),
        })
        .collect()
}

fn field<'a>(fields: &'a [NamedAbiValue], name: &str) -> Result<&'a AbiValue> {
    fields.iter().find(|f| &*f.name == name).map(|f| &f.value).with_context(|| format!("no field {name}"))
}

fn uint(fields: &[NamedAbiValue], name: &str) -> Result<u128> {
    match field(fields, name)? {
        AbiValue::Uint(_, v) => v.to_u128().with_context(|| format!("{name} out of range")),
        other => bail!("{name}: expected an integer, got {other:?}"),
    }
}

fn custodians(cfg: &ConfigAccount, wallet: &StdAddr, account: &Cell) -> Result<Vec<Custodian>> {
    tuples(get(cfg, wallet, account, "getCustodians")?)?
        .iter()
        .map(|c| {
            let AbiValue::Uint(_, pubkey) = field(c, "pubkey")? else { bail!("pubkey: expected an integer") };
            let bytes = pubkey.to_bytes_be();
            ensure!(bytes.len() <= 32, "pubkey out of range");
            let mut key = [0u8; 32];
            key[32 - bytes.len()..].copy_from_slice(&bytes);
            Ok(Custodian { index: uint(c, "index")? as u8, pubkey: HashBytes(key) })
        })
        .collect()
}

fn pending(cfg: &ConfigAccount, wallet: &StdAddr, account: &Cell) -> Result<Vec<Pending>> {
    tuples(get(cfg, wallet, account, "getTransactions")?)?
        .iter()
        .map(|t| {
            let AbiValue::Address(dest) = field(t, "dest")? else { bail!("dest: expected an address") };
            let AnyAddr::Std(dest) = dest.as_ref() else { bail!("dest: not a standard address") };
            let AbiValue::Cell(payload) = field(t, "payload")? else { bail!("payload: expected a cell") };
            Ok(Pending {
                id: uint(t, "id")? as u64,
                mask: uint(t, "confirmationsMask")? as u32,
                required: uint(t, "signsRequired")? as u8,
                received: uint(t, "signsReceived")? as u8,
                dest: dest.clone(),
                value: uint(t, "value")?,
                payload: payload.clone(),
            })
        })
        .collect()
}

pub(super) fn transfer(wallet: &Wallet, chain: &Graphql, cfg: &ConfigAccount, dest: &StdAddr, value: u128, payload: Cell) -> Result<Transfer> {
    let addr = &wallet.address;
    let mut account = chain.account(addr)?.with_context(|| format!("{}: no such account", wallet.name))?;
    // every step adds a confirmation, so this ends
    loop {
        let custodians = custodians(cfg, addr, &account)?;
        let ours: Vec<(u8, &SigningKey)> = wallet
            .keys
            .iter()
            .filter_map(|k| custodians.iter().find(|c| c.pubkey == public(k)).map(|c| (c.index, k)))
            .collect();
        ensure!(!ours.is_empty(), "{}: none of its keys is a custodian of {addr}", wallet.name);
        // what this message does, for the multi-step transfers of several custodians
        let (step, message) = if custodians.len() == 1 {
            let tokens = [
                AbiValue::address(dest.clone()).named("dest"),
                AbiValue::uint(128, value).named("value"),
                AbiValue::Bool(true).named("bounce"),
                AbiValue::uint(8, 3u8).named("flags"),
                AbiValue::Cell(payload.clone()).named("payload"),
            ];
            (None, call(ours[0].1, addr, "sendTransaction", &tokens)?)
        } else {
            let same = pending(cfg, addr, &account)?
                .into_iter()
                .find(|p| p.dest == *dest && p.value == value && p.payload.repr_hash() == payload.repr_hash());
            match same {
                None => {
                    let tokens = [
                        AbiValue::address(dest.clone()).named("dest"),
                        AbiValue::uint(128, value).named("value"),
                        AbiValue::Bool(true).named("bounce"),
                        AbiValue::Bool(false).named("allBalance"),
                        AbiValue::Cell(payload.clone()).named("payload"),
                    ];
                    let key = ours[0].1;
                    let step = format!("submitted by custodian {}", short(&public(key)));
                    (Some(step), call(key, addr, "submitTransaction", &tokens)?)
                }
                Some(p) => match ours.iter().find(|(index, _)| p.mask & (1 << index) == 0) {
                    None => return Ok(Transfer::Pending { id: p.id, received: p.received, required: p.required }),
                    Some(&(_, key)) => {
                        let tokens = [AbiValue::uint(64, p.id).named("transactionId")];
                        let step = format!("transaction {} confirmed by custodian {} ({} of {})", p.id, short(&public(key)), p.received + 1, p.required);
                        (Some(step), call(key, addr, "confirmTransaction", &tokens)?)
                    }
                },
            }
        };
        let tx = super::execute(chain, cfg, wallet, &account, message, dest)?;
        if let Some(step) = step {
            println!("{} {}: {step}", crate::stamp(), wallet.name);
        }
        if let Some(sent) = tx.sent_to(dest) {
            return Ok(Transfer::Sent(sent.hash));
        }
        ensure!(custodians.len() > 1, "{}: sendTransaction ran but sent nothing (balance too low?)", wallet.name);
        account = chain.account_after(addr, tx.lt)?;
    }
}

#[cfg(test)]
mod tests {
    //! The multisig's real code (`res/SafeMultisigWallet.tvc`, the SafeMultisig mainnet validators use:
    //! code hash 80d6c47c…) in the emulator: the calls this module signs, and the getters it reads.

    use std::sync::Arc;

    use num_bigint::BigUint;
    use tycho_types::abi::AbiType;
    use tycho_types::models::{Account, AccountState, CurrencyCollection, OptionalAccount, StateInit};

    use super::*;
    use crate::emulate::run_external;
    use crate::emulate::tests::config_account;

    const TVC: &[u8] = include_bytes!("../../res/SafeMultisigWallet.tvc");
    const EVER: u128 = 1_000_000_000;

    fn keys(n: u8) -> Vec<SigningKey> {
        (1..=n).map(|i| SigningKey::from_bytes(&[0x40 + i; 32])).collect()
    }

    fn dest() -> StdAddr {
        StdAddr::new(-1, HashBytes([0x55; 32]))
    }

    /// A multisig with custodians `keys` and `required` confirmations, deployed by its constructor.
    fn deploy(cfg: &ConfigAccount, keys: &[SigningKey], required: u8) -> (StdAddr, Cell) {
        let tvc = Boc::decode(TVC).unwrap().parse::<StateInit>().unwrap();
        let data = ABI.update_init_data(Some(&keys[0].verifying_key()), &[], tvc.data.as_ref().unwrap()).unwrap();
        let init = StateInit { data: Some(data), ..tvc };
        let addr = StdAddr::new(-1, *CellBuilder::build_from(&init).unwrap().repr_hash());
        let account = Account {
            address: addr.clone().into(),
            storage_stat: Default::default(),
            last_trans_lt: 0,
            balance: CurrencyCollection::new(1000 * EVER),
            state: AccountState::Uninit,
        };
        let account = CellBuilder::build_from(OptionalAccount(Some(account))).unwrap();
        let owners = keys.iter().map(|k| AbiValue::uint(256, BigUint::from_bytes_be(&k.verifying_key().to_bytes()))).collect();
        let tokens = [AbiValue::Array(Arc::new(AbiType::Uint(256)), owners).named("owners"), AbiValue::uint(8, required).named("reqConfirms")];
        let time = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64;
        let message = function("constructor")
            .encode_external(&tokens)
            .with_time(time)
            .with_expire_at(now() as u32 + 60)
            .with_pubkey(&keys[0].verifying_key())
            .build_message(&addr)
            .unwrap()
            .with_state_init(init)
            .sign(&keys[0], SignatureContext::EMPTY)
            .unwrap();
        let (r, account) = run_external(cfg, &account, &CellBuilder::build_from(message).unwrap(), now() as u32).unwrap();
        assert!(r.success, "constructor: exit code {}", r.exit_code);
        later();
        (addr, account)
    }

    /// Replay protection wants every message newer than the last one accepted, to the millisecond, getter
    /// calls included.
    fn later() {
        std::thread::sleep(std::time::Duration::from_millis(2));
    }

    /// Runs a message on the wallet, then lets the clock move on.
    fn run(cfg: &ConfigAccount, account: &Cell, message: Message) -> (crate::emulate::TxResult, Cell) {
        let result = run_external(cfg, account, &message.cell, now() as u32).unwrap();
        later();
        result
    }

    fn submit(key: &SigningKey, addr: &StdAddr, value: u128, payload: Cell) -> Message {
        later();
        let tokens = [
            AbiValue::address(dest()).named("dest"),
            AbiValue::uint(128, value).named("value"),
            AbiValue::Bool(true).named("bounce"),
            AbiValue::Bool(false).named("allBalance"),
            AbiValue::Cell(payload).named("payload"),
        ];
        call(key, addr, "submitTransaction", &tokens).unwrap()
    }

    fn confirm(key: &SigningKey, addr: &StdAddr, id: u64) -> Message {
        later();
        call(key, addr, "confirmTransaction", &[AbiValue::uint(64, id).named("transactionId")]).unwrap()
    }

    fn sends_to_dest(r: &crate::emulate::TxResult) -> bool {
        r.out.iter().any(|m| m.dst == Some(dest().address))
    }

    #[test]
    fn a_transfer_needs_the_required_confirmations() {
        let cfg = config_account();
        let keys = keys(3);
        let (addr, account) = deploy(&cfg, &keys, 2);
        let custodians = custodians(&cfg, &addr, &account).unwrap();
        assert_eq!(custodians.len(), 3);
        assert!(keys.iter().all(|k| custodians.iter().any(|c| c.pubkey.0 == k.verifying_key().to_bytes())));

        let payload = CellBuilder::build_from(0x6e565052u32).unwrap();
        let (r, account) = run(&cfg, &account, submit(&keys[0], &addr, 5 * EVER, payload.clone()));
        assert!(r.success && !sends_to_dest(&r), "a submission alone does not send");
        assert!(!r.dropped, "and drops nothing: the dry run accepts it");
        let p = pending(&cfg, &addr, &account).unwrap();
        assert_eq!(p.len(), 1);
        assert_eq!((p[0].received, p[0].required, p[0].value), (1, 2, 5 * EVER));
        assert_eq!((&p[0].dest, p[0].payload.repr_hash()), (&dest(), payload.repr_hash()));
        let submitter = custodians.iter().find(|c| c.pubkey.0 == keys[0].verifying_key().to_bytes()).unwrap();
        assert_ne!(p[0].mask & (1 << submitter.index), 0, "the submission counts as the submitter's confirmation");

        let (r, _) = run(&cfg, &account, confirm(&keys[0], &addr, p[0].id));
        assert!(!r.accepted && r.exit_code == 103, "a custodian confirms once: exit code {}", r.exit_code);
        let (r, account) = run(&cfg, &account, confirm(&keys[2], &addr, p[0].id));
        assert!(r.success && sends_to_dest(&r) && !r.dropped, "the second confirmation sends it");
        assert!(pending(&cfg, &addr, &account).unwrap().is_empty());
    }

    #[test]
    fn a_single_custodian_sends_directly() {
        let cfg = config_account();
        let keys = keys(1);
        let (addr, account) = deploy(&cfg, &keys, 1);
        let tokens = [
            AbiValue::address(dest()).named("dest"),
            AbiValue::uint(128, 5 * EVER).named("value"),
            AbiValue::Bool(true).named("bounce"),
            AbiValue::uint(8, 3u8).named("flags"),
            AbiValue::Cell(Cell::empty_cell()).named("payload"),
        ];
        let (r, _) = run(&cfg, &account, call(&keys[0], &addr, "sendTransaction", &tokens).unwrap());
        assert!(r.success && sends_to_dest(&r) && !r.dropped);
        // more than the balance: the multisig "succeeds" and sends nothing, which the dry run shows
        let mut tokens = tokens;
        tokens[1] = AbiValue::uint(128, 5000 * EVER).named("value");
        let (r, _) = run(&cfg, &account, call(&keys[0], &addr, "sendTransaction", &tokens).unwrap());
        assert!(r.success && !sends_to_dest(&r) && r.dropped, "{r:?}");
    }

    /// A chain of `n` cells.
    fn payload(n: u32) -> Cell {
        let mut cell = CellBuilder::build_from(n).unwrap();
        for i in 1..n {
            let mut b = CellBuilder::new();
            b.store_u32(i).unwrap();
            b.store_reference(cell).unwrap();
            cell = b.build().unwrap();
        }
        cell
    }

    #[test]
    fn several_custodians_carry_at_most_four_cells_and_no_stranger() {
        let cfg = config_account();
        let keys = keys(3);
        let (addr, account) = deploy(&cfg, &keys, 2);
        let try_submit = |key: &SigningKey, cells: u32| run(&cfg, &account, submit(key, &addr, 5 * EVER, payload(cells))).0;
        // submitTransaction measures the payload before it accepts the message, on the 10,000 gas credit of
        // an external message: from 5 cells on it runs out of gas (-14) before its own limit (121, 8 cells)
        assert!(try_submit(&keys[0], 4).success);
        let r = try_submit(&keys[0], 5);
        assert_eq!((r.accepted, r.exit_code), (false, -14));
        let r = try_submit(&SigningKey::from_bytes(&[0x99; 32]), 1);
        assert_eq!((r.accepted, r.exit_code), (false, 100));
    }
}
