#!/usr/bin/env node
// Deploys contracts/kept_word.py to GenLayer Studionet and prints the address.
//   KW_PRIVATE_KEY=0x...   optional (a throw-away key is generated otherwise)
//   node e2e/deploy.mjs
import { createAccount, EXPLORER, fund, generatePrivateKey, makeClient, deployKeptWord } from "./lib.mjs";

const key = process.env.KW_PRIVATE_KEY || generatePrivateKey();
const account = createAccount(key);
const client = makeClient(account);
const funded = await fund(client, account.address);
console.log(`deployer ${account.address}${funded === true ? " (funded via sim_fundAccount)" : ` (funding note: ${funded})`}`);
const { address, hash } = await deployKeptWord(client);
console.log(`\nKeptWord deployed\n  address : ${address}\n  tx      : ${EXPLORER}/tx/${hash}\n  contract: ${EXPLORER}/contracts/${address}`);
console.log(`\nNext: KW_ADDRESS=${address} npm run smoke   (update DEPLOYED_ADDRESS in e2e/lib.mjs to make it the default)`);
