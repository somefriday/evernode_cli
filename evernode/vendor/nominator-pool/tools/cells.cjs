// Cell tooling for the vendored nominator-pool contract (TON nominator-pool v1, unchanged).
//
// This builds and inspects cells only. It never reads a private key, never
// signs and never broadcasts: contract execution belongs to the node, and
// signing to the Safe. Its command line matches the single-nominator tool's
// where the two meet ('account' names the wallet the contract takes orders
// from, 'body stake' splices a bid), so the kit's own checks read a pool the
// way they read a single-nominator contract.
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');
const {Address, beginCell, Cell, Dictionary, contractAddress, storeMessage, internal,
       external, storeStateInit, loadStateInit, loadAccount} = require('@ton/core');
const {compileFunc} = require('@ton-community/func-js');

const read = p => Cell.fromBoc(fs.readFileSync(p))[0];
// Written without the optional CRC, as ever-cli and a Safe's own transaction list
// serialize a cell: a pending transaction is recognised by comparing payloads.
const write = (p, c) => fs.writeFileSync(p, c.toBoc({idx: false, crc32: false}));
const serialize = msg => beginCell().store(storeMessage(msg)).endCell();
const hex = n => n.toString(16).padStart(64, '0');
const pinned = () => JSON.parse(fs.readFileSync(path.join(root, 'PINNED.json'), 'utf8'));

// Operations of pool.fc. Text comments ('d', 'w') are op 0.
const OP = {
  topUp: 1, payQueue: 2, payOne: 3, depositValidator: 4, withdrawValidator: 5,
  updateValidatorSet: 6, newStake: 0x4e73744b, recover: 0x47657424,
};

async function compile() {
  const result = await compileFunc({
    targets: ['contracts/stdlib.fc', 'contracts/pool.fc'],
    sources: p => fs.readFileSync(path.join(root, p), 'utf8'),
  });
  if (result.status !== 'ok') throw Error(result.message);
  return Cell.fromBase64(result.codeBoc);
}

const masterchainHash = address => {
  const parsed = Address.parseRaw(address);
  if (parsed.workChain !== -1) throw Error('the validator wallet must be in the masterchain: ' + address);
  return BigInt('0x' + parsed.hash.toString('hex'));
};

// pool.fc's save_data layout with nothing in it yet: idle, no stakers, no stake
// sent, no validator funds. stake_at is only reported, and the first bid
// overwrites it, so the two pools of a pair differ there and nowhere else:
// that gives each its own address.
function poolData(validator, share, maxStakers, minValidator, minStaker, salt) {
  const config = beginCell()
    .storeUint(masterchainHash(validator), 256)
    .storeUint(Number(share), 16)
    .storeUint(Number(maxStakers), 16)
    .storeCoins(BigInt(minValidator))
    .storeCoins(BigInt(minStaker))
    .endCell();
  return beginCell()
    .storeUint(0, 8).storeUint(0, 16).storeCoins(0).storeCoins(0)
    .storeRef(config)
    .storeBit(0).storeBit(0)
    .storeUint(Number(salt), 32)
    .storeUint(0, 256).storeUint(0, 8).storeUint(0, 32).storeUint(0, 32)
    .storeBit(0)
    .endCell();
}

const NOMINATOR = {serialize() { throw Error('read only'); }, parse: s => ({amount: s.loadCoins(), pending: s.loadCoins()})};
const PRESENT = {serialize() { throw Error('read only'); }, parse: () => true};

// The pool's storage as pool.fc's load_data reads it.
function decodePool(data) {
  const s = data.beginParse();
  const out = {state: s.loadUint(8), nominatorsCount: s.loadUint(16)};
  out.stakeAmountSent = s.loadCoins().toString();
  out.validatorAmount = s.loadCoins().toString();
  const c = s.loadRef().beginParse();
  out.config = {
    validator: '-1:' + hex(c.loadUintBig(256)), rewardShare: c.loadUint(16),
    maxNominators: c.loadUint(16), minValidatorStake: c.loadCoins().toString(),
    minNominatorStake: c.loadCoins().toString(),
  };
  const nominators = s.loadMaybeRef(), requests = s.loadMaybeRef();
  const nd = nominators ? Dictionary.loadDirect(Dictionary.Keys.BigUint(256), NOMINATOR, nominators) : null;
  const rd = requests ? Dictionary.loadDirect(Dictionary.Keys.BigUint(256), PRESENT, requests) : null;
  const queued = new Set(rd ? rd.keys().map(k => k.toString()) : []);
  let total = 0n, pending = 0n;
  out.nominators = (nd ? nd.keys() : []).map(k => {
    const v = nd.get(k);
    total += v.amount + v.pending; pending += v.pending;
    return {address: '0:' + hex(k), amount: v.amount.toString(), pending: v.pending.toString(), withdrawRequested: queued.has(k.toString())};
  });
  out.nominatorsTotal = total.toString();
  out.pendingTotal = pending.toString();
  out.withdrawRequests = queued.size;
  out.stakeAt = s.loadUint(32);
  out.validatorSetHash = hex(s.loadUintBig(256));
  out.validatorSetChanges = s.loadUint(8);
  out.validatorSetChangeTime = s.loadUint(32);
  out.stakeHeldFor = s.loadUint(32);
  const votings = s.loadMaybeRef();
  out.votings = votings ? Dictionary.loadDirect(Dictionary.Keys.BigUint(256), PRESENT, votings).size : 0;
  return out;
}

// The deployer: a wallet's ordinary transfer cannot carry a StateInit. Its data
// holds one prepared message and its code can only send that message and
// delete itself, so anyone may trigger it and nobody can redirect its funds.
const FACTORY_SOURCE =
  '() recv_internal(slice body) impure { return (); }\n' +
  '() recv_external(slice body) impure {\n' +
  '  accept_message();\n' +
  '  send_raw_message(get_data().begin_parse().preload_ref(), 160);\n' +
  '}\n';

const query = value => BigInt(value === undefined ? 0 : value);

async function main() {
  const [command, ...rest] = process.argv.slice(2);
  if (command === 'compile') {
    const code = await compile();
    write(rest[0], code);
    console.log(JSON.stringify({
      codeHash: code.hash().toString('hex'),
      codeHashBase64: code.hash().toString('base64'),
      compiler: require('@ton-community/func-js/package.json').version,
      compilerBinary: require('@ton-community/func-js-bin/package.json').version,
    }));
  } else if (command === 'address' || command === 'init') {
    // CODE VALIDATOR SHARE MAX_STAKERS MIN_VALIDATOR MIN_STAKER SALT [OUT]
    const [codeFile, validator, share, maxStakers, minValidator, minStaker, salt, out] = rest;
    const init = {code: read(codeFile), data: poolData(validator, share, maxStakers, minValidator, minStaker, salt)};
    if (command === 'init') write(out, beginCell().store(storeStateInit(init)).endCell());
    console.log(contractAddress(-1, init).toRawString());
  } else if (command === 'factory') {
    // INIT VALUE OUT: the pool is created by a top-up (op 1), which it accepts.
    const init = loadStateInit(read(rest[0]).beginParse());
    const outgoing = serialize(internal({
      to: contractAddress(-1, init), value: BigInt(rest[1]), bounce: false, init,
      body: beginCell().storeUint(OP.topUp, 32).storeUint(0, 64).endCell(),
    }));
    const compiled = await compileFunc({
      targets: ['stdlib.fc', 'factory.fc'],
      sources: p => p === 'factory.fc' ? FACTORY_SOURCE
        : fs.readFileSync(path.join(root, 'contracts/stdlib.fc'), 'utf8'),
    });
    if (compiled.status !== 'ok') throw Error(compiled.message);
    const factory = {code: Cell.fromBase64(compiled.codeBoc), data: beginCell().storeRef(outgoing).endCell()};
    const address = contractAddress(-1, factory);
    // The node's external message queue keys messages by their body's hash and
    // drops all but one of a kind, so every trigger carries 128 random bits.
    write(rest[2], serialize(external({
      to: address, init: factory,
      body: beginCell().storeBuffer(crypto.randomBytes(16)).endCell(),
    })));
    console.log(address.toRawString());
  } else if (command === 'body') {
    const [kind, out, ...args] = rest;
    const b = beginCell();
    if (kind === 'comment') {
      // 'd' deposits, 'w' withdraws everything: op 0 and the text in the same cell.
      b.storeUint(0, 32).storeStringTail(args[0]);
    } else if (kind === 'top-up') {
      b.storeUint(OP.topUp, 32).storeUint(query(args[0]), 64);
    } else if (kind === 'pay') {
      const limit = Number(args[0]);
      if (!(limit >= 1 && limit <= 255)) throw Error('a payout limit is 1 to 255 requests');
      b.storeUint(OP.payQueue, 32).storeUint(query(args[1]), 64).storeUint(limit, 8);
    } else if (kind === 'pay-one') {
      const staker = Address.parseRaw(args[0]);
      if (staker.workChain !== 0) throw Error('stakers are in the basechain (0:): ' + args[0]);
      b.storeUint(OP.payOne, 32).storeUint(query(args[1]), 64)
       .storeUint(BigInt('0x' + staker.hash.toString('hex')), 256);
    } else if (kind === 'deposit-validator') {
      b.storeUint(OP.depositValidator, 32).storeUint(query(args[0]), 64);
    } else if (kind === 'withdraw-validator') {
      const amount = BigInt(args[0]);
      if (amount <= 0n) throw Error('a withdrawal must be positive');
      b.storeUint(OP.withdrawValidator, 32).storeUint(query(args[1]), 64).storeCoins(amount);
    } else if (kind === 'update') {
      b.storeUint(OP.updateValidatorSet, 32).storeUint(query(args[0]), 64);
    } else if (kind === 'recover') {
      b.storeUint(OP.recover, 32).storeUint(query(args[0]), 64);
    } else if (kind === 'stake') {
      // QUERY_BOC AMOUNT [QUERY]: the node's console builds the elector's own
      // new_stake body; the pool needs the amount it should send inserted after
      // the query id and forwards the rest to the elector untouched. It refuses
      // a query id of 0, so one is replaced with the election the bid is for.
      const source = read(args[0]).beginParse();
      const op = source.loadUint(32);
      if (op !== OP.newStake) throw Error('not a new_stake body: op ' + op);
      let id = source.loadUintBig(64);
      const rest = source.clone();
      rest.skip(256);
      const stakeAt = BigInt(rest.loadUint(32));
      if (args[2] !== undefined) id = BigInt(args[2]);
      if (id === 0n) id = stakeAt;
      b.storeUint(op, 32).storeUint(id, 64).storeCoins(BigInt(args[1])).storeSlice(source);
    } else {
      throw Error('unknown body: ' + kind);
    }
    write(out, b.endCell());
  } else if (command === 'stake-info') {
    const s = read(rest[0]).beginParse();
    const info = {op: s.loadUint(32), query: s.loadUintBig(64).toString()};
    if (rest[1] === 'pool') info.amount = s.loadCoins().toString();
    Object.assign(info, {key: hex(s.loadUintBig(256)), at: s.loadUint(32),
      factor: s.loadUint(32), adnl: hex(s.loadUintBig(256))});
    console.log(JSON.stringify(info));
  } else if (command === 'account') {
    // What the chain holds, read from the account itself. 'getaccountstate'
    // writes the account behind its account$1 tag, which loadAccount does not expect.
    const slice = read(rest[0]).beginParse();
    slice.loadBit();
    const account = loadAccount(slice);
    const state = account.storage.state;
    const answer = {
      address: account.addr.toRawString(),
      state: state.type,
      balance: account.storage.balance.coins.toString(),
    };
    if (state.type === 'active') {
      answer.codeHash = state.state.code.hash().toString('hex');
      // Another contract's storage would decode as nonsense, so only the
      // pinned pool code is read as a pool.
      if (answer.codeHash === pinned().codeHash) {
        answer.pool = decodePool(state.state.data);
        answer.validator = answer.pool.config.validator;
      }
    }
    console.log(JSON.stringify(answer));
  } else if (command === 'base64') {
    console.log(fs.readFileSync(rest[0]).toString('base64'));
  } else {
    throw Error('unknown command: ' + command);
  }
}

main().catch(error => {
  console.error(String(error && error.message ? error.message : error));
  process.exit(1);
});
