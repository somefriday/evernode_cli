//! The config contract run offline under ever-node's own executor (`ever_executor` 1.18.22 / `ever_vm`
//! 2.2.21, the tags ever-node e4068621 builds with) on its real code and state: a dry run of every message
//! before it is paid for or sent, a whole simulated vote for `simulate`, and the contract tests below.
//!
//! The sandbox keeps one account, the config contract, and publishes its params as the block config before
//! every transaction, as the collator does; replacing p34 plays out a new round.

use std::sync::Arc;
use std::sync::atomic::AtomicU64;

use anyhow::{Context, Result, bail};
use ed25519_dalek::{Signer, SigningKey};
use ever_block::{
    Account, AccountId, CommonMessage, ConfigParam34, ConfigParamEnum, ConfigParams, CurrencyCollection, Deserializable,
    ExternalInboundMessageHeader, HashmapE, HashmapType, InternalMessageHeader, Message, MsgAddressExt, MsgAddressInt, OutAction,
    OutActions, Serializable, SigPubKey, SliceData, TrActionPhase, TrComputePhase, TransactionDescr, TransactionTickTock, UInt256, ValidatorDescr,
};
use ever_executor::{
    BlockchainConfig, ExecuteParams, ExecutorError, OrdinaryTransactionExecutor, TickTockTransactionExecutor, TransactionExecutor,
};
use ever_vm::SmartContractInfo;
use ever_vm::executor::Engine;
use ever_vm::executor::gas::gas_state::Gas;
use ever_vm::stack::savelist::SaveList;
use ever_vm::stack::{Stack, StackItem};
use tycho_types::models::StdAddr;
use tycho_types::prelude::*;

use crate::config::{self, ConfigAccount, Proposal};
use crate::proposal::{from_ever, to_ever};

pub struct Sandbox {
    account: ever_block::Cell,
    address: MsgAddressInt,
    pub now: u32,
    lt: u64,
}

/// What a message did to the config contract.
#[derive(Debug)]
pub struct TxResult {
    /// external messages: the contract accepted it (otherwise nothing happened on chain)
    pub accepted: bool,
    pub success: bool,
    pub exit_code: i32,
    pub out: Vec<OutMsg>,
    /// the action phase sent fewer messages than it was told to: with "ignore errors", a transfer the balance
    /// does not cover is dropped (counted as neither created nor skipped), and the transaction still succeeds
    pub dropped: bool,
}

/// A message the contract sent: answers start with `op:uint32 query_id:uint64`.
#[derive(Debug)]
pub struct OutMsg {
    pub dst: Option<HashBytes>,
    pub op: Option<u32>,
}

impl TxResult {
    /// The op of the first answer (the config answers every proposal and vote with one message;
    /// a failed internal message bounces with op 0xffffffff).
    pub fn answer(&self) -> Option<u32> {
        self.out.first().and_then(|m| m.op)
    }
}

fn std_addr(addr: &StdAddr) -> Result<MsgAddressInt> {
    MsgAddressInt::with_standart(None, addr.workchain, AccountId::from(UInt256::with_array(addr.address.0)))
}

fn to_hash(addr: &MsgAddressInt) -> HashBytes {
    let bytes = addr.address().get_bytestring(0);
    HashBytes(bytes.try_into().unwrap_or_default())
}

impl Sandbox {
    pub fn new(cfg: &ConfigAccount, now: u32) -> Result<Self> {
        Ok(Self { account: to_ever(&cfg.account)?, address: std_addr(&cfg.address)?, now, lt: 1_000_000 })
    }

    pub fn state(&self) -> Result<ConfigAccount> {
        ConfigAccount::parse(&from_ever(&self.account)?)
    }

    /// The block config: the contract's own params, as the collator publishes them.
    pub fn block_config(&self) -> Result<ConfigParams> {
        params_of(&self.account, &self.address)
    }

    /// Replaces p34 in the contract's params (what an election does) and moves the clock to its start.
    pub fn set_vset(&mut self, utime_since: u32, utime_until: u32, validators: Vec<ValidatorDescr>) -> Result<()> {
        let main = validators.len() as u16;
        let vset = ever_block::ValidatorSet::new(utime_since, utime_until, main, validators)?;
        let mut config = self.block_config()?;
        config.set_config(ConfigParamEnum::ConfigParam34(ConfigParam34 { cur_validators: vset }))?;
        let mut acc = Account::construct_from_cell(self.account.clone())?;
        acc.update_config_smc(&config)?;
        self.account = acc.serialize()?;
        self.now = self.now.max(utime_since);
        Ok(())
    }

    pub fn internal(&mut self, src: &StdAddr, value: u128, body: Cell) -> Result<TxResult> {
        let msg = internal_message(src, &self.address, value, body)?;
        self.execute(Some(msg))
    }

    pub fn external(&mut self, body: Cell) -> Result<TxResult> {
        let hdr = ExternalInboundMessageHeader { src: MsgAddressExt::AddrNone, dst: self.address.clone(), import_fee: Default::default() };
        self.execute(Some(Message::with_ext_in_header_and_body(hdr, SliceData::load_cell(to_ever(&body)?)?)))
    }

    /// The tock transaction every masterchain block runs on the config contract (`run_ticktock`).
    pub fn tock(&mut self) -> Result<TxResult> {
        self.execute(None)
    }

    /// Registers `proposal` from `from` with `value` attached.
    pub fn register(&mut self, from: &StdAddr, proposal: &Proposal, critical: bool, ttl: u32, value: u128) -> Result<TxResult> {
        self.internal(from, value, config::new_proposal_body(1, ttl, proposal, critical)?)
    }

    pub fn vote(&mut self, from: &StdAddr, value: u128, key: &SigningKey, idx: u16, phash: &HashBytes) -> Result<TxResult> {
        let sig = key.sign(&config::vote_sign_data(idx, phash)).to_bytes();
        self.internal(from, value, config::vote_body(7, &sig, idx, phash)?)
    }

    fn execute(&mut self, msg: Option<Message>) -> Result<TxResult> {
        let config = self.block_config()?;
        self.lt += 1_000;
        let mut root = self.account.clone();
        let result = run_tx(&config, &mut root, msg, self.now, self.lt)?;
        if result.accepted {
            self.account = root;
        }
        Ok(result)
    }
}

/// The params in a config account, as the collator publishes them.
fn params_of(config_account: &ever_block::Cell, address: &MsgAddressInt) -> Result<ConfigParams> {
    let acc = Account::construct_from_cell(config_account.clone())?;
    let data = acc.get_data().context("config account has no data")?;
    let root = SliceData::load_cell(data)?.reference(0)?;
    Ok(ConfigParams { config_addr: UInt256::with_array(to_hash(address).0), config_params: HashmapE::with_hashmap(32, Some(root)) })
}

fn network_config(cfg: &ConfigAccount) -> Result<ConfigParams> {
    params_of(&to_ever(&cfg.account)?, &std_addr(&cfg.address)?)
}

/// A wallet's dry run: the transaction external `message` (with the StateInit of the wallet it deploys, if
/// any) causes on `account` (an `Account` cell) under the network's config, and the account afterwards.
pub fn run_external(cfg: &ConfigAccount, account: &Cell, message: &Cell, now: u32) -> Result<(TxResult, Cell)> {
    let mut root = to_ever(account)?;
    let lt = Account::construct_from_cell(root.clone())?.last_tr_time().unwrap_or(0) + 1_000;
    let result = run_tx(&network_config(cfg)?, &mut root, Some(Message::construct_from_cell(to_ever(message)?)?), now, lt)?;
    Ok((result, from_ever(&root)?))
}

/// Runs external `message` on `account` outside any transaction, as ever-sdk's `run_tvm` does: no
/// acceptance, no fees, nothing kept. This is how a TVM Solidity getter is asked; the answer is in the
/// bodies of the external messages it sends.
pub fn run_getter(cfg: &ConfigAccount, account: &Cell, message: &Cell, now: u32) -> Result<Vec<Cell>> {
    let config = network_config(cfg)?;
    let capabilities = BlockchainConfig::with_config(config.clone())?.capabilites();
    let acc = Account::construct_from_cell(to_ever(account)?)?;
    let (Some(code), Some(data), Some(addr), Some(balance)) = (acc.get_code(), acc.get_data(), acc.get_addr(), acc.balance()) else {
        bail!("not an active account")
    };
    let msg_cell = to_ever(message)?;
    let msg = Message::construct_from_cell(msg_cell.clone())?;
    let mut info = SmartContractInfo::with_myself(SliceData::load_cell(addr.serialize()?)?);
    info.unix_time = now;
    info.balance = balance.clone();
    info.config_params = config.config_params.data().cloned();
    info.capabilities = capabilities;
    info.set_mycode(code.clone());
    let mut ctrls = SaveList::new();
    ctrls.put(4, &mut StackItem::Cell(data))?;
    ctrls.put(7, &mut info.into_temp_data_item())?;
    let mut stack = Stack::new();
    stack
        .push(StackItem::int(balance.grams.as_u128()))
        .push(StackItem::int(0))
        .push(StackItem::Cell(msg_cell))
        .push(StackItem::Slice(msg.body().unwrap_or_default()))
        .push(StackItem::int(-1)); // an external message
    let mut engine = Engine::with_capabilities(capabilities).setup(SliceData::load_cell(code)?, Some(ctrls), Some(stack), Some(Gas::test()));
    if let Err(e) = engine.execute() {
        bail!("the getter fails with exit code {}", ever_vm::error::tvm_exception_or_custom_code(&e));
    }
    let actions = OutActions::construct_from_cell(engine.get_actions().as_cell()?.clone())?;
    let mut bodies = Vec::new();
    for action in actions.iter() {
        if let OutAction::SendMsg { out_msg, .. } = action
            && let Some(body) = out_msg.body()
        {
            bodies.push(from_ever(&body.into_cell())?);
        }
    }
    Ok(bodies)
}

fn internal_message(src: &StdAddr, dst: &MsgAddressInt, value: u128, body: Cell) -> Result<Message> {
    let hdr = InternalMessageHeader::with_addresses_and_bounce(std_addr(src)?, dst.clone(), CurrencyCollection::with_grams(value.try_into()?), true);
    Ok(Message::with_int_header_and_body(hdr, SliceData::load_cell(to_ever(&body)?)?))
}

/// Runs one transaction on `root` under `config`: ordinary with `msg`, or a tock without.
fn run_tx(config: &ConfigParams, root: &mut ever_block::Cell, msg: Option<Message>, now: u32, lt: u64) -> Result<TxResult> {
    let blockchain = BlockchainConfig::with_config(config.clone())?;
    let params = ExecuteParams {
        block_unixtime: now,
        block_lt: lt,
        last_tr_lt: Arc::new(AtomicU64::new(lt)),
        seed_block: UInt256::with_array(rand::random()),
        ..Default::default()
    };
    let result = match msg {
        Some(msg) => OrdinaryTransactionExecutor::new(blockchain).execute_with_libs_and_params(Some(&CommonMessage::Std(msg)), root, params),
        None => TickTockTransactionExecutor::new(blockchain, TransactionTickTock::Tock).execute_with_libs_and_params(None, root, params),
    };
    let tx = match result {
        Ok(tx) => tx,
        // an external message the contract did not accept leaves no transaction
        Err(e) => match e.downcast_ref::<ExecutorError>() {
            Some(ExecutorError::NoAcceptError(code, _)) => {
                return Ok(TxResult { accepted: false, success: false, exit_code: *code, out: vec![], dropped: false });
            }
            // no code to run (an account not deployed), or no funds to import the message
            Some(ExecutorError::ExtMsgComputeSkipped(_) | ExecutorError::NoFundsToImportMsg) => {
                return Ok(TxResult { accepted: false, success: false, exit_code: -1, out: vec![], dropped: false });
            }
            _ => return Err(e.context("executor")),
        },
    };
    let dropped = |action: &Option<TrActionPhase>| action.as_ref().is_some_and(|a| a.skipped_actions > 0 || a.msgs_created + a.spec_actions < a.tot_actions);
    let (compute, aborted, dropped) = match tx.read_description()? {
        TransactionDescr::Ordinary(d) => (d.compute_ph, d.aborted, dropped(&d.action)),
        TransactionDescr::TickTock(d) => (d.compute_ph, d.aborted, dropped(&d.action)),
        _ => bail!("unexpected transaction type"),
    };
    let (success, exit_code) = match &compute {
        TrComputePhase::Vm(vm) => (vm.success, vm.exit_code),
        TrComputePhase::Skipped(_) => (false, -1),
    };
    let mut out = Vec::new();
    tx.iterate_out_msgs(|msg| {
        let msg = msg.get_std()?;
        let mut op = None;
        if let Some(mut body) = msg.body()
            && body.remaining_bits() >= 32
        {
            op = Some(body.get_next_u32()?);
        }
        out.push(OutMsg { dst: msg.dst_ref().map(to_hash), op });
        Ok(true)
    })?;
    Ok(TxResult { accepted: true, success: success && !aborted, exit_code, out, dropped })
}

/// Delivers an internal message to another account (`account`, an `Account` cell) under `config`; returns
/// what happened and the account afterwards. Used to see what the elector does with a forwarded upgrade.
pub fn deliver(config: &ConfigParams, account: &Cell, src: &StdAddr, value: u128, body: Cell, now: u32) -> Result<(TxResult, Cell)> {
    let mut root = to_ever(account)?;
    let acc = Account::construct_from_cell(root.clone())?;
    let dst = acc.get_addr().context("the account has no address")?.clone();
    let result = run_tx(config, &mut root, Some(internal_message(src, &dst, value, body)?), now, 2_000_000)?;
    Ok((result, from_ever(&root)?))
}

/// The body the config sends the elector for an accepted `-1001` (`change_elector_code`): `upgrade_code`'s
/// function id, a query id, and the proposal value's references (code, data).
pub fn elector_upgrade_body(value: &Cell, query_id: u64) -> Result<Cell> {
    let mut b = CellBuilder::new();
    b.store_u32(0x4e436f64)?;
    b.store_u64(query_id)?;
    b.store_slice(value.as_slice()?)?;
    Ok(b.build()?)
}

/// Stand-in validators for simulations: equal weights, a new p34 every round, in a rotated order (as
/// elections reshuffle indices), each entry with an ADNL address (tag 0x73, as the elector writes them).
pub struct Validators {
    pub keys: Vec<SigningKey>,
    pub start: u32,
    pub round: u32,
}

impl Validators {
    pub fn random(n: usize, start: u32, round: u32) -> Self {
        Self { keys: (0..n).map(|_| SigningKey::from_bytes(&rand::random())).collect(), start, round }
    }

    pub fn install(&self, sb: &mut Sandbox, n: u32) -> Result<()> {
        let since = self.start + n * self.round;
        let mut keys: Vec<_> = self.keys.iter().collect();
        let len = keys.len();
        keys.rotate_left(n as usize % len);
        let list = keys.iter().map(|k| descriptor(&HashBytes(k.verifying_key().to_bytes()), 1 << 40)).collect::<Result<_>>()?;
        sb.set_vset(since, since + self.round, list)
    }

    /// Every validator of the current p34 votes once.
    pub fn vote_all(&self, sb: &mut Sandbox, from: &StdAddr, value: u128, phash: &HashBytes) -> Result<Vec<TxResult>> {
        let vset = sb.state()?.current_vset()?;
        let mut results = Vec::new();
        for v in &vset.list {
            let key = self.keys.iter().find(|k| k.verifying_key().to_bytes() == v.public_key.0).context("unknown validator")?;
            results.push(sb.vote(from, value, key, v.idx, phash)?);
        }
        Ok(results)
    }
}

pub fn descriptor(public: &HashBytes, weight: u64) -> Result<ValidatorDescr> {
    Ok(ValidatorDescr::with_params(SigPubKey::from_bytes(public.as_slice())?, weight, Some(UInt256::with_array([0x11; 32])), None))
}

#[cfg(test)]
pub(crate) mod tests {
    //! The contract's rules, checked on the code and data of the config contract in the zerostate template
    //! (`testnet/template`, the network `testnet/gen.sh` builds), with three validators of equal weight.

    use super::*;
    use crate::burn;
    use crate::config::{Fate, PARAM_DICTATOR};
    use crate::proposal::{Allow, Change, IfHash, Plan};

    const NOW: u32 = 1_800_000_000;
    const ROUND: u32 = 300;
    const EVER: u128 = 1_000_000_000;

    fn validators() -> Validators {
        Validators { keys: (1..=3u8).map(|i| SigningKey::from_bytes(&[i; 32])).collect(), start: NOW, round: ROUND }
    }

    fn giver() -> StdAddr {
        StdAddr::new(-1, HashBytes([0x77; 32]))
    }

    /// The template's zerostate with three test validators in p34.
    fn template_state() -> ever_block::ShardStateUnsplit {
        let raw = std::fs::read_to_string("testnet/template/zero_state_blanc_1.json").unwrap()
            + r#"{"public_key": "00", "weight": "1"}"#
            + &std::fs::read_to_string("testnet/template/zero_state_blanc_2.json").unwrap();
        let raw = raw.replace("nowdate", &NOW.to_string()).replace("p34_total_weight", "1").replace("p34_total", "1");
        let mut json: serde_json::Value = serde_json::from_str(&raw).unwrap();
        let key = hex::encode(validators().keys[0].verifying_key().to_bytes());
        json["master"]["config"]["p34"] = serde_json::json!({"utime_since": NOW, "utime_until": NOW + ROUND, "total": 1, "main": 1,
            "total_weight": 1, "list": [{"public_key": key, "weight": "1"}]});
        ever_block_json::parse_state(json.as_object().unwrap()).unwrap()
    }

    /// An account of the template's zerostate (an `Account` cell).
    fn template_account(addr: &UInt256) -> Cell {
        let acc = template_state().read_accounts().unwrap().account(&AccountId::from(addr.clone())).unwrap().unwrap();
        from_ever(&acc.read_account().unwrap().serialize().unwrap()).unwrap()
    }

    /// The template's config account with its params synced into storage and `dictator` as the key,
    /// in round 0 of [`validators`].
    fn template(dictator: &HashBytes) -> Sandbox {
        let state = template_state();
        let config = state.read_custom().unwrap().unwrap().config;
        let shard_acc = state.read_accounts().unwrap().account(&AccountId::from(config.config_addr.clone())).unwrap().unwrap();
        let mut acc = shard_acc.read_account().unwrap();
        acc.update_config_smc(&config).unwrap();
        let cell = config::patch_account_key(&from_ever(&acc.serialize().unwrap()).unwrap(), dictator).unwrap();
        let mut sb = Sandbox::new(&ConfigAccount::parse(&cell).unwrap(), NOW).unwrap();
        validators().install(&mut sb, 0).unwrap();
        sb
    }

    /// The template's config account: the block config for other contracts' tests.
    pub(crate) fn config_account() -> ConfigAccount {
        template(&HashBytes([1; 32])).state().unwrap()
    }

    impl Sandbox {
        /// A vote by external message, with the contract's current seqno.
        fn vote_external(&mut self, key: &SigningKey, idx: u16, phash: &HashBytes) -> Result<TxResult> {
            let signed = config::external_vote(self.state()?.seqno, self.now + 60, idx, phash)?;
            let sig = key.sign(signed.data()).to_bytes();
            self.external(config::signed_body(&sig, &signed)?)
        }
    }

    /// Sets a number in ever-node's JSON by its `_dec` field, leaving its hex form out.
    fn set_dec(json: &mut serde_json::Value, field: &str, value: &str) {
        json[format!("{field}_dec")] = value.into();
        json.as_object_mut().unwrap().remove(field);
    }

    fn plan(sb: &Sandbox, change: Change) -> Plan {
        change.plan(&sb.state().unwrap(), IfHash::Auto, Allow { usable_key: false, dangerous: true }).unwrap()
    }

    fn register(sb: &mut Sandbox, proposal: &Proposal, critical: bool, extra: u128) -> TxResult {
        let setup = sb.state().unwrap().vote_setup(critical).unwrap();
        let price = setup.price(proposal.value.as_ref(), setup.min_store_sec).unwrap();
        sb.register(&giver(), proposal, critical, setup.min_store_sec, price + extra).unwrap()
    }

    fn vote(sb: &mut Sandbox, key: &SigningKey, idx: u16, phash: &HashBytes) -> TxResult {
        sb.vote(&giver(), 2 * EVER, key, idx, phash).unwrap()
    }

    fn key_of(public: &HashBytes) -> SigningKey {
        validators().keys.into_iter().find(|k| k.verifying_key().to_bytes() == public.0).unwrap()
    }

    fn burn_plan(sb: &Sandbox) -> Plan {
        plan(sb, Change::Dictator(burn::derive(burn::DEFAULT_LABEL).0))
    }

    fn dictator_cmd(sb: &mut Sandbox, key: &SigningKey) -> TxResult {
        let st = sb.state().unwrap();
        let signed = config::dictator_action(0, st.seqno, sb.now + 60, None).unwrap();
        let sig = key.sign(signed.repr_hash().as_slice()).to_bytes();
        sb.external(config::signed_body(&sig, &signed).unwrap()).unwrap()
    }

    /// Every validator votes in each round until the proposal is gone; checks RoundView against the
    /// contract at every step. Returns the number of rounds voted.
    fn vote_until_gone(sb: &mut Sandbox, phash: &HashBytes, max_rounds: u32) -> u32 {
        for n in 1..=max_rounds {
            let st = sb.state().unwrap();
            let Some(p) = st.proposal(phash) else { return n - 1 };
            let vset = st.current_vset().unwrap();
            let setup = st.vote_setup(p.critical).unwrap();
            let predicted = p.view(&vset, &setup, sb.now);
            validators().vote_all(sb, &giver(), 2 * EVER, phash).unwrap();
            if let Some(p) = sb.state().unwrap().proposal(phash) {
                // the contract's lazy round update is what RoundView predicts, and a full round is a win
                assert_eq!((p.losses, p.rounds_remaining as i32, p.wins), (predicted.losses, predicted.rounds_remaining, predicted.wins + 1));
            }
            validators().install(sb, n).unwrap();
        }
        max_rounds
    }

    #[test]
    fn burn_by_vote_and_the_old_key_stops_working() {
        let dictator = SigningKey::from_bytes(&[42; 32]);
        let mut sb = template(&HashBytes(dictator.verifying_key().to_bytes()));
        assert!(dictator_cmd(&mut sb, &dictator).accepted, "the dictator key works before the burn");

        let plan = burn_plan(&sb);
        assert!(plan.critical);
        assert_eq!(register(&mut sb, &plan.proposal, true, 2 * EVER).answer(), Some(config::ANSWER_PROPOSAL_OK));
        let setup = sb.state().unwrap().vote_setup(true).unwrap();
        assert_eq!(vote_until_gone(&mut sb, &plan.hash, 10), setup.min_wins as u32);

        let st = sb.state().unwrap();
        assert!(plan.change.in_effect(&st, None).unwrap());
        let r = dictator_cmd(&mut sb, &dictator);
        assert!(!r.accepted, "the old key is rejected after the burn");
        // CHKSIGNU on a key that is not a curve point: false (exit 34) with CapsTvmBugfixes2022, a VM fatal
        // error (12) without it, as on this network; either way the message is never accepted
        assert!([12, 34].contains(&r.exit_code), "exit code {}", r.exit_code);
        // the value is also stored as param -999, which every node then carries in its config
        assert_eq!(st.param_hash(PARAM_DICTATOR).unwrap(), *plan.proposal.value.unwrap().repr_hash());
        BlockchainConfig::with_config(sb.block_config().unwrap()).unwrap();
    }

    /// Key 0 — what a -999 proposal without a value would set — lets anyone command the config.
    #[test]
    fn zero_dictator_key_is_forgeable_on_the_contract() {
        let forge = |sb: &mut Sandbox| {
            let seqno = sb.state().unwrap().seqno;
            (0..64).any(|i| {
                // R = identity, s = 0 verifies for small-order keys whenever H(R, A, m) is divisible by 4
                let signed = config::dictator_action(0, seqno, sb.now + 60 + i, None).unwrap();
                let mut forged = [0u8; 64];
                forged[0] = 1;
                sb.external(config::signed_body(&forged, &signed).unwrap()).unwrap().accepted
            })
        };
        assert!(forge(&mut template(&HashBytes::ZERO)), "ever-vm accepts a forged command for key 0");
        assert!(!forge(&mut template(&burn::derive(burn::DEFAULT_LABEL).0)), "nothing verifies for the burn key");
    }

    #[test]
    fn mandatory_param_needs_hash_and_critical_flag() {
        let mut sb = template(&HashBytes([1; 32]));
        let st = sb.state().unwrap();
        let mut p17 = crate::proposal::param_json(17, &st.required(17).unwrap()).unwrap();
        set_dec(&mut p17, "min_stake", "2000000000000");
        let value = crate::proposal::param_from_json(17, &p17.to_string()).unwrap();
        let bare = Proposal { param_id: 17, value: Some(value.clone()), if_hash_equal: None };
        assert_eq!(register(&mut sb, &bare, true, 2 * EVER).answer(), Some(0xcd506e6c));
        let plan = plan(&sb, Change::Param { id: 17, value });
        assert!(plan.proposal.if_hash_equal.is_some() && plan.critical);
        assert_eq!(register(&mut sb, &plan.proposal, false, 2 * EVER).answer(), Some(0xc3726954));
        assert_eq!(register(&mut sb, &plan.proposal, true, EVER).answer(), Some(0xf0617924));
        assert_eq!(register(&mut sb, &plan.proposal, true, 2 * EVER).answer(), Some(config::ANSWER_PROPOSAL_OK));
        let setup = sb.state().unwrap().vote_setup(true).unwrap();
        assert_eq!(vote_until_gone(&mut sb, &plan.hash, 10), setup.min_wins as u32);
        assert!(plan.change.in_effect(&sb.state().unwrap(), None).unwrap());
        BlockchainConfig::with_config(sb.block_config().unwrap()).unwrap();
    }

    /// With if_hash_equal, a value changed while the vote ran makes the accepted proposal a no-op.
    #[test]
    fn stale_if_hash_is_accepted_but_not_applied() {
        let dictator = SigningKey::from_bytes(&[42; 32]);
        let mut sb = template(&HashBytes(dictator.verifying_key().to_bytes()));
        let st = sb.state().unwrap();
        let mut p17 = crate::proposal::param_json(17, &st.required(17).unwrap()).unwrap();
        set_dec(&mut p17, "min_stake", "2000000000000");
        let plan = plan(&sb, Change::Param { id: 17, value: crate::proposal::param_from_json(17, &p17.to_string()).unwrap() });
        register(&mut sb, &plan.proposal, true, 2 * EVER);
        // the dictator changes p17 meanwhile (action 0x43665021: param index, value ref)
        set_dec(&mut p17, "min_stake", "3000000000000");
        let other = crate::proposal::param_from_json(17, &p17.to_string()).unwrap();
        let mut args = CellBuilder::new();
        args.store_u32(17).unwrap();
        args.store_reference(other.clone()).unwrap();
        let signed = config::dictator_action(0x43665021, st.seqno, sb.now + 60, Some(args)).unwrap();
        let sig = dictator.sign(signed.repr_hash().as_slice()).to_bytes();
        assert!(sb.external(config::signed_body(&sig, &signed).unwrap()).unwrap().accepted);
        vote_until_gone(&mut sb, &plan.hash, 10);
        let st = sb.state().unwrap();
        assert!(st.proposal(&plan.hash).is_none(), "accepted and removed");
        assert_eq!(st.param_hash(17).unwrap(), *other.repr_hash(), "but not applied");
    }

    #[test]
    fn price_matches_the_contract() {
        let mut sb = template(&HashBytes([1; 32]));
        // p31 (fundamental smc list) is neither mandatory nor critical; its value spans several cells
        let st = sb.state().unwrap();
        let p31 = st.required(31).unwrap();
        let proposal = Proposal { param_id: 31, value: Some(p31.clone()), if_hash_equal: Some(*p31.repr_hash()) };
        let setup = st.vote_setup(false).unwrap();
        let price = setup.price(Some(&p31), setup.min_store_sec).unwrap();
        let r = register(&mut sb, &proposal, false, 2 * EVER);
        assert_eq!(r.answer(), Some(config::ANSWER_PROPOSAL_OK));
        // raw_reserve(price, 4) keeps exactly the price; the rest goes back with the answer
        assert_eq!(sb.state().unwrap().balance - st.balance, price);
    }

    /// D6: the dry run passes only when the contract keeps exactly the price shown.
    #[test]
    fn the_dry_run_proves_the_price_shown_is_the_price_taken() {
        let sb = template(&HashBytes([1; 32]));
        let cfg = sb.state().unwrap();
        let plan = burn_plan(&sb);
        let mut r = crate::campaign::Registration::new(&cfg, &plan, None).unwrap();
        r.dry_run(&cfg, &giver()).unwrap();
        r.price += 1;
        let err = format!("{:#}", r.dry_run(&cfg, &giver()).unwrap_err());
        assert!(err.contains("would keep") && err.contains("not the price shown"), "{err}");
    }

    #[test]
    fn null_value_can_never_be_accepted() {
        let mut sb = template(&HashBytes([1; 32]));
        // p31 is neither mandatory nor critical, so a "remove p31" proposal registers (and its price is paid)
        let remove = Proposal { param_id: 31, value: None, if_hash_equal: None };
        let phash = remove.hash().unwrap();
        assert_eq!(register(&mut sb, &remove, false, 2 * EVER).answer(), Some(config::ANSWER_PROPOSAL_OK));
        let setup = sb.state().unwrap().vote_setup(false).unwrap();
        for n in 1..=setup.min_wins as u32 + 1 {
            let results = validators().vote_all(&mut sb, &giver(), 2 * EVER, &phash).unwrap();
            if n >= setup.min_wins as u32 {
                // every vote that would accept it throws in accept_proposal (idict_set_ref with null, exit 7)
                let last = results.last().unwrap();
                assert_eq!((last.success, last.exit_code), (false, 7));
            }
            validators().install(&mut sb, n).unwrap();
        }
        let st = sb.state().unwrap();
        assert!(st.proposal(&phash).is_some(), "the proposal stays, never accepted");
        assert!(st.param(31).unwrap().is_some());
    }

    #[test]
    fn vote_rules() {
        let mut sb = template(&HashBytes([1; 32]));
        let plan = burn_plan(&sb);
        register(&mut sb, &plan.proposal, true, 2 * EVER);
        let vset = sb.state().unwrap().current_vset().unwrap();
        let v0 = &vset.list[0];
        let k0 = key_of(&v0.public_key);
        let other = key_of(&vset.list[1].public_key);
        let status = |r: TxResult| config::vote_status(r.answer().unwrap()).unwrap().0;
        // a signature by another validator's key for this index: exit 34, bounced
        let r = vote(&mut sb, &other, v0.idx, &plan.hash);
        assert_eq!((r.success, r.exit_code), (false, 34));
        assert_eq!(status(vote(&mut sb, &k0, v0.idx, &plan.hash)), 2);
        assert_eq!(status(vote(&mut sb, &k0, v0.idx, &plan.hash)), -2, "a second vote in a round is ignored");
        assert!(!vote(&mut sb, &k0, 77, &plan.hash).success, "an index outside p34");
        assert_eq!(status(vote(&mut sb, &k0, v0.idx, &HashBytes([5; 32]))), -1, "an unknown proposal");
        // after it expires a vote deletes it
        sb.now = sb.state().unwrap().proposal(&plan.hash).unwrap().expires;
        assert_eq!(status(vote(&mut sb, &k0, v0.idx, &plan.hash)), -1);
        assert!(sb.state().unwrap().proposal(&plan.hash).is_none());
    }

    #[test]
    fn lost_rounds_discard_the_proposal() {
        let mut sb = template(&HashBytes([1; 32]));
        let plan = burn_plan(&sb);
        register(&mut sb, &plan.proposal, true, 2 * EVER);
        let setup = sb.state().unwrap().vote_setup(true).unwrap();
        // one vote of three per round never reaches 3/4 of the weight
        for n in 1..=(setup.max_losses as u32 + 2) {
            let st = sb.state().unwrap();
            let vset = st.current_vset().unwrap();
            let predicted = st.proposal(&plan.hash).map(|p| p.view(&vset, &setup, sb.now));
            let v0 = &vset.list[0];
            vote(&mut sb, &key_of(&v0.public_key), v0.idx, &plan.hash);
            let after = sb.state().unwrap().proposal(&plan.hash).cloned();
            match predicted.map(|v| v.fate) {
                Some(Fate::Discarded) => {
                    assert!(after.is_none(), "deleted at the vote RoundView marked as fatal");
                    assert_ne!(sb.state().unwrap().public_key, burn::derive(burn::DEFAULT_LABEL).0);
                    return;
                }
                Some(_) => assert!(after.is_some()),
                None => panic!("gone before RoundView predicted it"),
            }
            validators().install(&mut sb, n).unwrap();
        }
        panic!("the proposal survived {} lost rounds", setup.max_losses + 2);
    }

    /// Validators whose p34 entry carries a BLS key (tag 0x74) cannot vote: the contract reads #53/#73 only.
    #[test]
    fn bls_descriptors_cannot_vote() {
        let mut sb = template(&HashBytes([1; 32]));
        let vs = validators();
        let mut list: Vec<ValidatorDescr> = vs.keys.iter().map(|k| descriptor(&HashBytes(k.verifying_key().to_bytes()), 1 << 40).unwrap()).collect();
        list[0].bls_public_key = Some([3; 48]);
        sb.set_vset(NOW + ROUND, NOW + 2 * ROUND, list).unwrap();
        let plan = burn_plan(&sb);
        register(&mut sb, &plan.proposal, true, 2 * EVER);
        let vset = sb.state().unwrap().current_vset().unwrap();
        assert!(!vset.list[0].votable && vset.list[1].votable);
        assert!(vset.votable_weight() <= vset.win_threshold(), "this round cannot be won");
        let r = vote(&mut sb, &vs.keys[0], 0, &plan.hash);
        assert_eq!((r.success, r.exit_code), (false, 41));
        assert!(vote(&mut sb, &vs.keys[1], 1, &plan.hash).success);
    }

    /// A config code upgrade by vote, to code that behaves like the old one: the contract keeps working —
    /// tock, proposals, votes and the dictator — on the new code.
    #[test]
    fn config_code_upgrade_keeps_the_contract_working() {
        let dictator = SigningKey::from_bytes(&[42; 32]);
        let mut sb = template(&HashBytes(dictator.verifying_key().to_bytes()));
        let code = crate::proposal::delegating_code(&from_ever(&Account::construct_from_cell(to_ever(&sb.state().unwrap().account).unwrap()).unwrap().get_code().unwrap()).unwrap()).unwrap();
        let plan = plan(&sb, Change::ConfigCode { code: code.clone() });
        register(&mut sb, &plan.proposal, true, 2 * EVER);
        vote_until_gone(&mut sb, &plan.hash, 10);
        let st = sb.state().unwrap();
        assert_eq!(st.code_hash, *code.repr_hash(), "the config runs the new code");
        assert!(sb.tock().unwrap().success, "tick-tock still runs");
        assert!(dictator_cmd(&mut sb, &dictator).accepted, "the dictator still commands it");
        let next = burn_plan(&sb);
        assert_eq!(register(&mut sb, &next.proposal, true, 2 * EVER).answer(), Some(config::ANSWER_PROPOSAL_OK));
        vote_until_gone(&mut sb, &next.hash, 10);
        assert!(next.change.in_effect(&sb.state().unwrap(), None).unwrap(), "and a burn by vote on the new code works");
    }

    /// The template's Solidity elector reaches its new code's upgrade hook (function id 2) through the
    /// function dictionary in the new code's first reference (TVM Solidity layout). FunC-style delegating code
    /// has the old *root* there, whose selector has no function 2: exit 11, nothing installed. (Mainnet's FunC
    /// elector is the mirror image: it calls method 1666 of the new code's root table.)
    #[test]
    fn elector_refuses_code_without_a_solidity_function_dictionary() {
        let sb = template(&HashBytes([1; 32]));
        let st = sb.state().unwrap();
        let elector = template_account(&UInt256::with_array(st.elector_address().unwrap().address.0));
        let old_code = match elector.parse::<tycho_types::models::OptionalAccount>().unwrap().0.unwrap().state {
            tycho_types::models::AccountState::Active(s) => s.code.unwrap(),
            _ => panic!("elector not active"),
        };
        let change = Change::ElectorCode { code: crate::proposal::delegating_code(&old_code).unwrap(), data: Cell::empty_cell() };
        let body = elector_upgrade_body(&change.value().unwrap(), NOW as u64).unwrap();
        let (r, after) = deliver(&sb.block_config().unwrap(), &elector, &st.address, 1 << 30, body, NOW).unwrap();
        assert_eq!((r.success, r.exit_code), (false, 11), "the elector's upgrade fails");
        let code_after = match after.parse::<tycho_types::models::OptionalAccount>().unwrap().0.unwrap().state {
            tycho_types::models::AccountState::Active(s) => s.code.unwrap(),
            _ => panic!("elector not active"),
        };
        assert_eq!(code_after.repr_hash(), old_code.repr_hash(), "the elector keeps its code");
    }

    /// External votes need no wallet; each must carry the contract's current seqno.
    #[test]
    fn external_votes_count_and_take_turns() {
        let mut sb = template(&HashBytes([1; 32]));
        let plan = burn_plan(&sb);
        register(&mut sb, &plan.proposal, true, 2 * EVER);
        let setup = sb.state().unwrap().vote_setup(true).unwrap();
        for n in 1..=setup.min_wins as u32 {
            let vset = sb.state().unwrap().current_vset().unwrap();
            for v in &vset.list {
                let key = key_of(&v.public_key);
                // a vote signed for a stale seqno is not accepted (exit 33)
                let seqno = sb.state().unwrap().seqno;
                let stale = config::external_vote(seqno.wrapping_sub(1), sb.now + 60, v.idx, &plan.hash).unwrap();
                let sig = key.sign(stale.data()).to_bytes();
                let r = sb.external(config::signed_body(&sig, &stale).unwrap()).unwrap();
                assert_eq!((r.accepted, r.exit_code), (false, 33));
                assert!(sb.vote_external(&key, v.idx, &plan.hash).unwrap().accepted);
            }
            validators().install(&mut sb, n).unwrap();
        }
        assert!(plan.change.in_effect(&sb.state().unwrap(), None).unwrap());
    }

    /// A vote signs only (idx, proposal): replayed in a later round it counts again for a validator that
    /// kept its key and index, and fails (exit 34) once keys rotate, as they do every election here.
    #[test]
    fn vote_replay_needs_the_same_key_at_the_same_index() {
        let mut sb = template(&HashBytes([1; 32]));
        let plan = burn_plan(&sb);
        register(&mut sb, &plan.proposal, true, 2 * EVER);
        let vset = sb.state().unwrap().current_vset().unwrap();
        let v0 = &vset.list[0];
        let sig = key_of(&v0.public_key).sign(&config::vote_sign_data(v0.idx, &plan.hash)).to_bytes();
        let body = config::vote_body(7, &sig, v0.idx, &plan.hash).unwrap();
        assert_eq!(config::vote_status(sb.internal(&giver(), 2 * EVER, body.clone()).unwrap().answer().unwrap()).unwrap().0, 2);
        // next round, same validators in the same order: anyone can replay the old vote
        let list = vset.list.iter().map(|v| descriptor(&v.public_key, v.weight).unwrap()).collect();
        sb.set_vset(NOW + ROUND, NOW + 2 * ROUND, list).unwrap();
        assert_eq!(config::vote_status(sb.internal(&giver(), 2 * EVER, body.clone()).unwrap().answer().unwrap()).unwrap().0, 2);
        // next round, rotated: the index now belongs to another key
        validators().install(&mut sb, 2).unwrap();
        let r = sb.internal(&giver(), 2 * EVER, body).unwrap();
        assert_eq!((r.success, r.exit_code), (false, 34));
    }

    /// The elector runs the *new* code's upgrade hook (function id 2, reached through the new code's
    /// function dictionary): new code whose hook finishes successfully is installed. Here the hook is just
    /// `THROW 0`, a successful exit that keeps the code change.
    #[test]
    fn elector_accepts_an_upgrade_whose_hook_succeeds() {
        let sb = template(&HashBytes([1; 32]));
        let st = sb.state().unwrap();
        let elector = template_account(&UInt256::with_array(st.elector_address().unwrap().address.0));
        // TVM Solidity code keeps its function dictionary in the root's first reference, and
        // `tvm.setCurrentCode` installs that reference as c3: here a "dictionary" that is just `THROW 0`
        let mut hook = CellBuilder::new();
        hook.store_u16(0xf200).unwrap(); // THROW 0
        let mut root = CellBuilder::new();
        root.store_reference(hook.build().unwrap()).unwrap();
        let code = root.build().unwrap();
        let change = Change::ElectorCode { code: code.clone(), data: Cell::empty_cell() };
        let body = elector_upgrade_body(&change.value().unwrap(), NOW as u64).unwrap();
        let (r, after) = deliver(&sb.block_config().unwrap(), &elector, &st.address, 1 << 30, body, NOW).unwrap();
        let installed = match after.parse::<tycho_types::models::OptionalAccount>().unwrap().0.unwrap().state {
            tycho_types::models::AccountState::Active(s) => s.code.unwrap(),
            _ => panic!("elector not active"),
        };
        assert!(r.success, "exit {}", r.exit_code);
        assert_eq!(installed.repr_hash(), code.repr_hash(), "the elector now runs the new code");
    }

    /// An elector code upgrade by vote, to the elector's own code with an unreachable NOP after the root's
    /// code (`proposal::equivalent_code`): the template's elector installs it, keeps its storage and
    /// answers the config with success. (The published source's hook, a FunC → Solidity migration pinned to
    /// code hash 0xcb6eb3…, is not what the template elector runs.)
    #[test]
    fn elector_upgrade_by_vote_keeps_its_storage() {
        let sb = template(&HashBytes([1; 32]));
        let st = sb.state().unwrap();
        let elector = template_account(&UInt256::with_array(st.elector_address().unwrap().address.0));
        let parts = |a: &Cell| match a.parse::<tycho_types::models::OptionalAccount>().unwrap().0.unwrap().state {
            tycho_types::models::AccountState::Active(s) => (s.code.unwrap(), *s.data.unwrap().repr_hash()),
            _ => panic!("elector not active"),
        };
        let (old, data) = parts(&elector);
        let code = crate::proposal::equivalent_code(&old).unwrap();
        let change = Change::ElectorCode { code: code.clone(), data: Cell::empty_cell() };
        let body = elector_upgrade_body(&change.value().unwrap(), NOW as u64).unwrap();
        let (r, after) = deliver(&sb.block_config().unwrap(), &elector, &st.address, 1 << 30, body, NOW).unwrap();
        assert!(r.success);
        assert_eq!(r.answer(), Some(0xce436f64), "upgrade_code answered with success");
        let (installed, data_after) = parts(&after);
        assert_eq!(installed.repr_hash(), code.repr_hash(), "the elector runs the new code");
        assert_eq!(data_after, data, "and keeps its storage");
        crate::elector::active_election(&after).unwrap();
    }

    /// Review 1: the fixes, checked on the contract's and the wallets' real code.
    mod review1 {
        use super::*;
        use crate::campaign::{self, Ballot};
        use crate::node::{Net, Node};

        fn node(name: &str) -> Node {
            serde_json::from_value(serde_json::json!({ "name": name })).unwrap()
        }

        /// A net file in a fresh directory whose one node reads `keyring.json`, written with `keys`.
        fn net_with_ring(tag: &str, keys: &[&SigningKey]) -> (std::path::PathBuf, Net) {
            let dir = std::env::temp_dir().join(format!("review1-{tag}-{}", std::process::id()));
            std::fs::create_dir_all(&dir).unwrap();
            std::fs::write(dir.join("net.json"), r#"{"nodes": [{"name": "vdp4", "keyring": "keyring.json"}]}"#).unwrap();
            write_ring(&dir, keys);
            let net = Net::load(&dir.join("net.json")).unwrap();
            (dir, net)
        }

        fn write_ring(dir: &std::path::Path, keys: &[&SigningKey]) {
            use base64::Engine;
            let b64 = base64::engine::general_purpose::STANDARD;
            let ring: serde_json::Map<String, serde_json::Value> = keys
                .iter()
                .map(|k| {
                    let id = crate::node::key_id(&HashBytes(k.verifying_key().to_bytes()));
                    (b64.encode(id.as_slice()), serde_json::json!({ "pvt_key": b64.encode(k.to_bytes()) }))
                })
                .collect();
            std::fs::write(dir.join("keyring.json"), serde_json::json!({ "validator_key_ring": ring }).to_string()).unwrap();
        }

        /// #3: a network whose p8 enables signature ids is refused when its config is read.
        #[test]
        fn a_network_with_signature_ids_is_refused() {
            let mut sb = template(&HashBytes([1; 32]));
            assert!(crate::node::supported(&sb.state().unwrap()).is_ok());
            let mut config = sb.block_config().unwrap();
            let mut gv = config.get_global_version().unwrap();
            gv.capabilities |= 1 << 26;
            config.set_config(ConfigParamEnum::ConfigParam8(ever_block::ConfigParam8 { global_version: gv })).unwrap();
            let mut acc = Account::construct_from_cell(sb.account.clone()).unwrap();
            acc.update_config_smc(&config).unwrap();
            sb.account = acc.serialize().unwrap();
            let e = format!("{:#}", crate::node::supported(&sb.state().unwrap()).unwrap_err());
            assert!(e.contains("CapSignatureWithId"), "{e}");
        }

        /// #8 and #15: a vote is predicted from its dry run and judged from the state after it. A vote that
        /// lands after the expiry deletes the proposal and counts nothing; one that is counted is.
        #[test]
        fn a_vote_counts_only_from_evidence() {
            let mut sb = template(&HashBytes([1; 32]));
            let plan = burn_plan(&sb);
            register(&mut sb, &plan.proposal, true, 2 * EVER);
            let before = sb.state().unwrap();
            let vset = before.current_vset().unwrap();
            let (v0, key) = (&vset.list[0], key_of(&vset.list[0].public_key));
            let n = node("vdp4");
            let ballot = Ballot { node: &n, vset: vset.id(), idx: v0.idx, key: v0.public_key, proposal: plan.hash };
            let mut now_sb = Sandbox::new(&before, sb.now).unwrap();
            let dry = now_sb.vote_external(&key, v0.idx, &plan.hash).unwrap();
            let after = now_sb.state().unwrap();
            assert!(!campaign::predict(&before, &after, &dry, &ballot, sb.now).unwrap(), "counted, not accepting");
            let expires = before.proposal(&plan.hash).unwrap().expires;
            assert!(campaign::effect(&after, &ballot, None, false, sb.now, expires).unwrap().starts_with("counted"));
            // the same vote landing after the expiry: the contract deletes the proposal, counting nothing
            let mut late = Sandbox::new(&before, expires + 5).unwrap();
            assert!(late.vote_external(&key, v0.idx, &plan.hash).unwrap().accepted);
            let gone = late.state().unwrap();
            assert!(gone.proposal(&plan.hash).is_none());
            let e = format!("{:#}", campaign::effect(&gone, &ballot, None, false, expires + 5, expires).unwrap_err());
            assert!(e.contains("not counted"), "{e}");
            // even a vote predicted to accept it is not counted once the proposal has expired
            assert!(campaign::effect(&gone, &ballot, None, true, expires + 5, expires).is_err());
        }

        /// A vote its dry run shows accepting the proposal can find it closed when it runs: another
        /// validator's vote accepted it first. The config's answer to a relayed vote says so; an external
        /// vote gets no answer, so a proposal gone after it is not called counted either.
        #[test]
        fn a_vote_another_beat_to_the_proposal_is_not_counted() {
            let mut sb = template(&HashBytes([1; 32]));
            let plan = burn_plan(&sb);
            register(&mut sb, &plan.proposal, true, 2 * EVER);
            let wins = sb.state().unwrap().vote_setup(true).unwrap().min_wins as u32;
            for n in 1..wins {
                validators().vote_all(&mut sb, &giver(), 2 * EVER, &plan.hash).unwrap();
                validators().install(&mut sb, n).unwrap();
            }
            // the last round: once the heavy validator voted, either light one completes it
            let keys = validators().keys;
            let unit = 1u64 << 40;
            let list = [unit, unit, 3 * unit]
                .iter()
                .zip(&keys)
                .map(|(&weight, key)| descriptor(&HashBytes(key.verifying_key().to_bytes()), weight))
                .collect::<Result<_>>()
                .unwrap();
            sb.set_vset(NOW + wins * ROUND, NOW + (wins + 1) * ROUND, list).unwrap();
            vote(&mut sb, &keys[2], 2, &plan.hash);
            let before = sb.state().unwrap();
            let expires = before.proposal(&plan.hash).unwrap().expires;
            let n = node("vdp4");
            let ballot = Ballot {
                node: &n,
                vset: before.current_vset().unwrap().id(),
                idx: 0,
                key: HashBytes(keys[0].verifying_key().to_bytes()),
                proposal: plan.hash,
            };
            let mut dry = Sandbox::new(&before, sb.now).unwrap();
            let predicted = dry.vote(&giver(), 2 * EVER, &keys[0], 0, &plan.hash).unwrap();
            assert!(campaign::predict(&before, &dry.state().unwrap(), &predicted, &ballot, sb.now).unwrap(), "it would accept it");
            let accepting = config::vote_status(predicted.answer().unwrap());
            assert!(campaign::effect(&dry.state().unwrap(), &ballot, accepting, true, sb.now, expires).unwrap().contains("accepted with it"));
            // validator #1's vote runs first and accepts it
            assert!(matches!(config::vote_status(vote(&mut sb, &keys[1], 1, &plan.hash).answer().unwrap()), Some((6 | 7, _))));
            let late = config::vote_status(vote(&mut sb, &keys[0], 0, &plan.hash).answer().unwrap());
            let after = sb.state().unwrap();
            assert!(after.proposal(&plan.hash).is_none());
            let e = format!("{:#}", campaign::effect(&after, &ballot, late, true, sb.now, expires).unwrap_err());
            assert!(e.contains("not counted"), "{e}");
            let external = campaign::effect(&after, &ballot, None, true, sb.now, expires).unwrap();
            assert!(!external.contains("counted"), "{external}");
        }

        /// #15: the external vote that would accept a removal fails after taking the seqno (exit 7): refused
        /// before sending.
        #[test]
        fn the_removals_winning_vote_is_refused_before_sending() {
            let mut sb = template(&HashBytes([1; 32]));
            let remove = Proposal { param_id: 31, value: None, if_hash_equal: None };
            let phash = remove.hash().unwrap();
            register(&mut sb, &remove, false, 2 * EVER);
            let n = node("vdp4");
            let mut refused = None;
            for round in 1..=3u32 {
                let vset = sb.state().unwrap().current_vset().unwrap();
                for v in &vset.list {
                    let before = sb.state().unwrap();
                    let mut dry_sb = Sandbox::new(&before, sb.now).unwrap();
                    let dry = dry_sb.vote_external(&key_of(&v.public_key), v.idx, &phash).unwrap();
                    let ballot = Ballot { node: &n, vset: vset.id(), idx: v.idx, key: v.public_key, proposal: phash };
                    match campaign::predict(&before, &dry_sb.state().unwrap(), &dry, &ballot, sb.now) {
                        Ok(_) => assert!(sb.vote_external(&key_of(&v.public_key), v.idx, &phash).unwrap().accepted),
                        Err(e) => refused = Some(format!("{e:#}")),
                    }
                }
                if refused.is_some() {
                    break;
                }
                validators().install(&mut sb, round).unwrap();
            }
            let refused = refused.expect("the winning vote was predicted to count");
            assert!(refused.contains("exit code 7"), "{refused}");
        }

        /// #10: a proposal voted on by hash gets propose's checks.
        #[test]
        fn a_registered_proposal_gets_proposes_checks() {
            let st = config_account();
            let mut b = CellBuilder::new();
            b.store_u8(0xff).unwrap();
            let bad = Proposal { param_id: 20, value: Some(b.build().unwrap()), if_hash_equal: None };
            let e = format!("{:#}", crate::proposal::check_registered(&bad).unwrap_err());
            assert!(e.contains("not a valid p20"), "{e}");
            let removal = Proposal { param_id: 31, value: None, if_hash_equal: None };
            assert!(format!("{:#}", crate::proposal::check_registered(&removal).unwrap_err()).contains("removal"));
            let weak = Proposal { param_id: -999, value: Some(Change::Dictator(HashBytes::ZERO).value().unwrap()), if_hash_equal: None };
            assert!(format!("{:#}", crate::proposal::check_registered(&weak).unwrap_err()).contains("small-order"));
            let p34 = Proposal { param_id: 34, value: st.param(34).unwrap(), if_hash_equal: None };
            let warnings = crate::proposal::check_registered(&p34).unwrap();
            assert!(warnings.iter().any(|w| w.contains("validator sets are written by the elector")), "{warnings:?}");
            let burn = burn_plan(&template(&HashBytes([1; 32]))).proposal;
            assert!(crate::proposal::check_registered(&burn).unwrap().is_empty());
        }

        /// #13: the rules line says what decides a normal param: two wins, two losses allowed, four rounds.
        #[test]
        fn the_rules_count_the_round_of_registration() {
            let setup = config_account().vote_setup(false).unwrap();
            assert_eq!(crate::report::rules(&setup), "2 winning rounds needed, 2 lost rounds allowed, at most 4 rounds");
        }

        /// #22: an EverWallet transfer its balance does not cover is skipped by the action phase: the dry run
        /// shows that, so it is refused before sending.
        #[test]
        fn an_underfunded_transfer_shows_in_the_dry_run() {
            use crate::wallet::ever_wallet;
            let cfg = config_account();
            let key = SigningKey::from_bytes(&[0x31; 32]);
            let public = HashBytes(key.verifying_key().to_bytes());
            let (wallet, account) = ever_wallet::mint(&public, 100_000_000).unwrap(); // 0.1 EVER
            let dest = StdAddr::new(-1, HashBytes([0x55; 32]));
            let send = |value| ever_wallet::send_transaction(&key, &wallet, &dest, value, Cell::empty_cell(), None).unwrap();
            let (r, _) = run_external(&cfg, &account, &send(1_500_000_000).cell, crate::now() as u32).unwrap();
            assert!(r.accepted && r.success && r.dropped && r.out.is_empty(), "{r:?}");
            let (r, _) = run_external(&cfg, &account, &send(10_000_000).cell, crate::now() as u32).unwrap();
            assert!(r.success && !r.dropped && r.out.len() == 1, "{r:?}");
        }

        /// #25 and #28: key rings are read at every pass, and a key whose p34 entry the contract cannot read is
        /// named, not voted with.
        #[test]
        fn key_rings_are_read_every_pass_and_unvotable_keys_are_left_out() {
            let mut sb = template(&HashBytes([1; 32]));
            let vs = validators();
            let mut list: Vec<ValidatorDescr> = vs.keys.iter().map(|k| descriptor(&HashBytes(k.verifying_key().to_bytes()), 1 << 40).unwrap()).collect();
            list[0].bls_public_key = Some([3; 48]);
            sb.set_vset(NOW + ROUND, NOW + 2 * ROUND, list).unwrap();
            let vset = sb.state().unwrap().current_vset().unwrap();
            let old = SigningKey::from_bytes(&[9; 32]);
            let (dir, net) = net_with_ring("keys", &[&old]);
            let mut cache = std::collections::HashMap::new();
            assert!(campaign::held_keys(&net, &vset, &mut cache).ours.is_empty(), "an old copy holds no current key");
            write_ring(&dir, &[&vs.keys[0], &vs.keys[1]]);
            let keys = campaign::held_keys(&net, &vset, &mut cache);
            std::fs::remove_dir_all(&dir).unwrap();
            assert_eq!(keys.ours.iter().map(|o| o.1).collect::<Vec<_>>(), vec![1], "the refreshed ring is read; #0 cannot vote");
            assert!(keys.unvotable.len() == 1 && keys.unvotable[0].contains("tag 0x74"), "{:?}", keys.unvotable);
        }

        /// #20: a pass says CANNOT WIN when the validators the contract can read hold no more than 3/4.
        #[test]
        fn a_pass_says_when_no_round_can_be_won() {
            let mut sb = template(&HashBytes([1; 32]));
            let vs = validators();
            let mut list: Vec<ValidatorDescr> = vs.keys.iter().map(|k| descriptor(&HashBytes(k.verifying_key().to_bytes()), 1 << 40).unwrap()).collect();
            list[0].bls_public_key = Some([3; 48]);
            sb.set_vset(NOW + ROUND, NOW + 2 * ROUND, list).unwrap();
            let plan = burn_plan(&sb);
            register(&mut sb, &plan.proposal, true, 2 * EVER);
            let st = sb.state().unwrap();
            let vset = st.current_vset().unwrap();
            let view = st.proposal(&plan.hash).unwrap().view(&vset, &st.vote_setup(true).unwrap(), sb.now);
            let keys = campaign::Keys::default();
            let line = crate::follow::result_line(&st.timeline(sb.now).unwrap(), &vset, &keys, 0, 0, &view, false, &[]);
            assert!(line.starts_with("CANNOT WIN"), "{line}");
        }

        /// #30: a proposal last read one vote short in its last allowed round is accepted by others. Read again
        /// in the same set, the reason is "accepted"; after a switch or its expiry, both reasons are named.
        #[test]
        fn a_removed_proposals_reason_is_named_only_when_known() {
            let mut sb = template(&HashBytes([1; 32]));
            let setup = sb.state().unwrap().vote_setup(false).unwrap();
            let mut b = CellBuilder::new();
            b.store_bit_zero().unwrap();
            let plan = plan(&sb, Change::Param { id: 31, value: b.build().unwrap() });
            register(&mut sb, &plan.proposal, false, 2 * EVER);
            let lone = |sb: &mut Sandbox| {
                let vset = sb.state().unwrap().current_vset().unwrap();
                vote(sb, &key_of(&vset.list[0].public_key), vset.list[0].idx, &plan.hash);
            };
            lone(&mut sb);
            validators().install(&mut sb, 1).unwrap();
            lone(&mut sb);
            validators().install(&mut sb, 2).unwrap();
            validators().vote_all(&mut sb, &giver(), 2 * EVER, &plan.hash).unwrap();
            validators().install(&mut sb, 3).unwrap();
            lone(&mut sb);
            let last = sb.state().unwrap().proposal(&plan.hash).cloned().unwrap();
            let set4 = sb.state().unwrap().current_vset().unwrap();
            validators().vote_all(&mut sb, &giver(), 2 * EVER, &plan.hash).unwrap();
            assert!(sb.state().unwrap().proposal(&plan.hash).is_none(), "accepted by the others");
            assert_eq!(campaign::why_removed(&last, &setup, set4.id(), &set4, sb.now), "accepted");
            assert!(campaign::why_removed(&last, &setup, set4.id(), &set4, last.expires).starts_with("accepted, or expired"));
            validators().install(&mut sb, 4).unwrap();
            let set5 = sb.state().unwrap().current_vset().unwrap();
            assert!(campaign::why_removed(&last, &setup, set4.id(), &set5, sb.now).starts_with("accepted, or lost its last round"));
        }
    }
}
