// Cell tooling for the vendored single-nominator contract.
//
// This builds and inspects cells only. It never reads a private key, never
// signs and never broadcasts: contract execution belongs to the node, and
// signing to the wallet signer. Keeping those apart is what lets this file
// run in a build image while keys stay on the operator's machine.
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');
const {Address, beginCell, Cell, contractAddress, storeMessage, internal,
       external, storeStateInit, loadStateInit, loadMessage,
       loadAccount} = require('@ton/core');
const {compileFunc} = require('@ton-community/func-js');

const read = p => Cell.fromBoc(fs.readFileSync(p))[0];
const write = (p, c) => fs.writeFileSync(p, c.toBoc());
const serialize = msg => beginCell().store(storeMessage(msg)).endCell();
const hex = n => n.toString(16).padStart(64, '0');

// The contract stores nothing but its two roles, so its address is a pure
// function of the code and that pair. A different pair is a different account.
const roleData = (owner, validator) => beginCell()
  .storeAddress(Address.parse(owner))
  .storeAddress(Address.parse(validator))
  .endCell();

async function compile(sources) {
  const result = await compileFunc({
    targets: sources,
    sources: p => fs.readFileSync(path.join(root, p), 'utf8'),
  });
  if (result.status !== 'ok') throw Error(result.message);
  return Cell.fromBase64(result.codeBoc);
}

// The deployer exists because a wallet's ordinary transfer cannot carry a
// StateInit. Its data holds one prepared message and its code can only send
// that message and delete itself, so anyone may trigger the deployment and
// nobody can redirect its funds. It has no signing key at all.
const FACTORY_SOURCE =
  '() recv_internal(slice body) impure { return (); }\n' +
  '() recv_external(slice body) impure {\n' +
  '  accept_message();\n' +
  '  send_raw_message(get_data().begin_parse().preload_ref(), 160);\n' +
  '}\n';

async function main() {
  const [command, ...rest] = process.argv.slice(2);
  if (command === 'compile') {
    const code = await compile([
      'contracts/imports/stdlib.fc',
      'contracts/single-nominator.fc',
    ]);
    write(rest[0], code);
    console.log(JSON.stringify({
      codeHash: code.hash().toString('hex'),
      codeHashBase64: code.hash().toString('base64'),
      compiler: require('@ton-community/func-js/package.json').version,
      compilerBinary: require('@ton-community/func-js-bin/package.json').version,
    }));
  } else if (command === 'address') {
    // Derive the account without writing anything, for verification.
    const code = read(rest[0]);
    console.log(contractAddress(-1, {
      code, data: roleData(rest[1], rest[2]),
    }).toRawString());
  } else if (command === 'init') {
    const code = read(rest[0]);
    const init = {code, data: roleData(rest[1], rest[2])};
    write(rest[3], beginCell().store(storeStateInit(init)).endCell());
    console.log(contractAddress(-1, init).toRawString());
  } else if (command === 'factory') {
    const init = loadStateInit(read(rest[0]).beginParse());
    const outgoing = serialize(internal({
      to: contractAddress(-1, init), value: BigInt(rest[1]),
      bounce: false, init,
    }));
    const factory = {
      code: await compileFunc({
        targets: ['stdlib.fc', 'factory.fc'],
        sources: p => p === 'factory.fc' ? FACTORY_SOURCE
          : fs.readFileSync(path.join(root, 'contracts/imports/stdlib.fc'), 'utf8'),
      }).then(r => {
        if (r.status !== 'ok') throw Error(r.message);
        return Cell.fromBase64(r.codeBoc);
      }),
      data: beginCell().storeRef(outgoing).endCell(),
    };
    factory.code = await factory.code;
    const address = contractAddress(-1, factory);
    write(rest[2], serialize(external({
      to: address, init: factory, body: beginCell().endCell(),
    })));
    console.log(address.toRawString());
  } else if (command === 'body') {
    const [kind, out, ...args] = rest;
    const b = beginCell();
    if (kind === 'empty') {
      // A deposit is an empty body. Anyone may donate; only the owner withdraws.
    } else if (kind === 'comment') {
      b.storeUint(0, 32).storeStringTail(args[0]);
    } else if (kind === 'withdraw') {
      b.storeUint(0x1000, 32).storeUint(BigInt(args[1] || 1), 64)
       .storeCoins(BigInt(args[0]));
    } else if (kind === 'rotate') {
      b.storeUint(0x1001, 32).storeUint(BigInt(args[1] || 1), 64)
       .storeAddress(Address.parse(args[0]));
    } else if (kind === 'recover') {
      b.storeUint(0x47657424, 32).storeUint(BigInt(args[0] || 1), 64);
    } else if (kind === 'stake') {
      // The node's console produces the elector's own new_stake body. The
      // nominator needs the amount it should take from its balance inserted
      // after the query id, and forwards the rest to the elector untouched.
      const source = read(args[0]).beginParse();
      const op = source.loadUint(32);
      if (op !== 0x4e73744b) throw Error('not a new_stake body: op ' + op);
      const query = source.loadUintBig(64);
      b.storeUint(op, 32)
       .storeUint(args[2] === undefined ? query : BigInt(args[2]), 64)
       .storeCoins(BigInt(args[1]))
       .storeSlice(source);
    } else if (kind === 'hex') {
      b.storeBuffer(Buffer.from(args[0], 'hex'));
    } else {
      throw Error('unknown body: ' + kind);
    }
    write(out, b.endCell());
  } else if (command === 'stake-info') {
    const s = read(rest[0]).beginParse();
    console.log(JSON.stringify({
      op: s.loadUint(32), query: s.loadUintBig(64).toString(),
      key: hex(s.loadUintBig(256)), at: s.loadUint(32),
      factor: s.loadUint(32), adnl: hex(s.loadUintBig(256)),
    }));
  } else if (command === 'account') {
    // What the chain holds for a deployed contract, read from the account
    // itself rather than from a node's summary of it: the code hash has to
    // come from the bytecode that is actually there.
    //
    // 'getaccountstate' writes the account preceded by its account$1 tag,
    // which @ton/core's loadAccount does not expect to see.
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
      const data = state.state.data.beginParse();
      answer.owner = data.loadAddress().toRawString();
      answer.validator = data.loadAddress().toRawString();
      answer.codeHash = state.state.code.hash().toString('hex');
    }
    console.log(JSON.stringify(answer));
  } else if (command === 'roles') {
    const s = read(rest[0]).beginParse();
    console.log(JSON.stringify({
      owner: s.loadAddress().toRawString(),
      validator: s.loadAddress().toRawString(),
    }));
  } else if (command === 'message-info') {
    const m = loadMessage(read(rest[0]).beginParse());
    console.log(JSON.stringify({
      type: m.info.type, from: m.info.src ? m.info.src.toRawString() : null,
      to: m.info.dest ? m.info.dest.toRawString() : null,
      value: m.info.type === 'internal' ? m.info.value.coins.toString() : null,
      bounced: m.info.bounced === true,
    }));
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
