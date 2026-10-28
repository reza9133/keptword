#!/usr/bin/env node
// One-command live run against GenLayer Studionet. Deploys (or reuses) KeptWord, plays a full
// pledge through the real network and prints + saves a report.
//
//   npm run live            full run: deploy, claim, open, sponsor, wait, verify (about 25 minutes)
//   npm run live:quick      short run: deploy, claim, open, sponsor, cancel (about 8 minutes)
//
// Environment (all optional):
//   KW_ADDRESS=0x...        contract to use (default: the deployed 0x0c002de09871195fb223Dad5b3E89587c2fb346f)
//   KW_DEPLOY=1             deploy a fresh contract instead of reusing one
//   KW_PRIVATE_KEY=0x...    deployer key (a throw-away key is generated otherwise)
//   KW_PROOF_URL=https://.. public https page used as proof (default https://example.com/)
//   KW_FINAL_WAIT_MIN=10    how long to wait at the end for every transaction to reach FINALIZED
//
// Output: reports/live-report.md and reports/live-report.json (also printed to the console).
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  ROOT, EXPLORER, createAccount, generatePrivateKey, makeClient, fund, readView, writeTx,
  deployKeptWord, nowUnix, sleep, resolveAddress,
} from "./lib.mjs";

const quick = process.argv.includes("--quick");
const proofUrl = process.env.KW_PROOF_URL || "https://example.com/";
const finalWaitMs = Number(process.env.KW_FINAL_WAIT_MIN || 10) * 60_000;
const log = (m = "") => console.log(m);

const report = {
  project: "KeptWord",
  network: "GenLayer Studionet",
  mode: quick ? "quick (cancel path)" : "full (verify path)",
  startedAt: new Date().toISOString(),
  finishedAt: null,
  result: "RUNNING",
  error: null,
  contract: { address: null, deployTx: null, version: null, reused: false },
  accounts: {},
  proofUrl,
  steps: [],
  checks: [],
  balances: [],
  pledge: null,
  checkpoints: null,
  stats: null,
  finality: [],
};

const txs = []; // { name, hash, client }

function check(cond, msg) {
  report.checks.push({ ok: !!cond, msg });
  log(`  ${cond ? "ok  " : "FAIL"}: ${msg}`);
  if (!cond) throw new Error("CHECK FAILED: " + msg);
}

async function step(name, fn) {
  log(`\n> ${name}`);
  const t0 = Date.now();
  const entry = { name, ok: false, seconds: 0, tx: null, explorer: null, execution: null, status: null };
  report.steps.push(entry);
  try {
    const out = await fn(entry);
    entry.ok = true;
    return out;
  } catch (err) {
    entry.error = String(err?.message ?? err);
    throw err;
  } finally {
    entry.seconds = Math.round((Date.now() - t0) / 1000);
  }
}

async function send(entry, client, address, fn, args = []) {
  const { hash, receipt } = await writeTx(client, address, fn, args);
  entry.tx = hash;
  entry.explorer = `${EXPLORER}/tx/${hash}`;
  entry.status = String(receipt?.statusName ?? receipt?.status ?? "ACCEPTED");
  entry.execution = String(receipt?.txExecutionResultName ?? "unknown");
  txs.push({ name: entry.name, hash, client });
  log(`   ${entry.explorer}  [${entry.status} / ${entry.execution}]`);
  return receipt;
}

async function snapshot(label, reader, address, owner, sponsor) {
  const o = await readView(reader, address, "get_standing", [owner]);
  const s = await readView(reader, address, "get_standing", [sponsor]);
  const stats = await readView(reader, address, "get_stats");
  const row = {
    label,
    owner: { balance: o.balance, locked: o.locked },
    sponsor: { balance: s.balance, locked: s.locked },
    burned: stats.points_burned,
    granted: stats.points_granted,
  };
  report.balances.push(row);
  log(`   owner ${o.balance}+${o.locked} locked | sponsor ${s.balance}+${s.locked} locked | burned ${stats.points_burned}`);
  return { o, s, stats };
}

async function awaitFinality(reader) {
  if (!txs.length) return;
  log(`\n> waiting up to ${Math.round(finalWaitMs / 60000)} min for every transaction to reach FINALIZED`);
  const deadline = Date.now() + finalWaitMs;
  const pending = new Map(txs.map((t) => [t.hash, t]));
  const seen = new Map();
  while (pending.size && Date.now() < deadline) {
    for (const [hash, t] of [...pending]) {
      try {
        const tx = await reader.getTransaction({ hash });
        const status = String(tx?.statusName ?? tx?.status ?? "UNKNOWN");
        seen.set(hash, status);
        if (status.toUpperCase() === "FINALIZED") pending.delete(hash);
      } catch (err) {
        seen.set(hash, "unreadable: " + String(err?.message ?? err).slice(0, 80));
      }
    }
    if (pending.size) await sleep(10_000);
  }
  for (const t of txs) {
    const last = seen.get(t.hash) ?? "UNKNOWN";
    report.finality.push({ name: t.name, tx: t.hash, explorer: `${EXPLORER}/tx/${t.hash}`, lastSeenStatus: last, finalized: last.toUpperCase() === "FINALIZED" });
  }
  const done = report.finality.filter((f) => f.finalized).length;
  log(`   ${done}/${report.finality.length} transactions FINALIZED`);
}

function markdown() {
  const L = [];
  L.push(`# KeptWord live report`, "");
  L.push(`- Result: **${report.result}**${report.error ? ` (${report.error})` : ""}`);
  L.push(`- Network: ${report.network}`, `- Mode: ${report.mode}`);
  L.push(`- Started: ${report.startedAt}`, `- Finished: ${report.finishedAt}`);
  if (report.contract.address) {
    L.push(`- Contract: \`${report.contract.address}\` (version ${report.contract.version ?? "?"}${report.contract.reused ? ", reused" : ""})`);
    L.push(`- Explorer: ${EXPLORER}/contracts/${report.contract.address}`);
    if (report.contract.deployTx) L.push(`- Deploy tx: ${EXPLORER}/tx/${report.contract.deployTx}`);
  }
  if (report.accounts.owner) L.push(`- Owner: \`${report.accounts.owner}\``, `- Sponsor: \`${report.accounts.sponsor}\``);
  L.push(`- Proof page: ${report.proofUrl}`, "");
  L.push(`## Steps`, "", `| # | Step | Result | Seconds | Status | Transaction |`, `|---|---|---|---|---|---|`);
  report.steps.forEach((s, i) => L.push(`| ${i + 1} | ${s.name} | ${s.ok ? "ok" : "FAILED"} | ${s.seconds} | ${s.status ?? ""} ${s.execution ?? ""} | ${s.explorer ? `[tx](${s.explorer})` : ""} |`));
  L.push("", `## Checks`, "");
  report.checks.forEach((c) => L.push(`- [${c.ok ? "x" : " "}] ${c.msg}`));
  if (report.balances.length) {
    L.push("", `## Points after each stage`, "", `| Stage | Owner free | Owner locked | Sponsor free | Sponsor locked | Burned |`, `|---|---|---|---|---|---|`);
    report.balances.forEach((b) => L.push(`| ${b.label} | ${b.owner.balance} | ${b.owner.locked} | ${b.sponsor.balance} | ${b.sponsor.locked} | ${b.burned} |`));
  }
  if (report.pledge) {
    L.push("", `## Pledge`, "", "```json", JSON.stringify(report.pledge, null, 2), "```");
  }
  if (report.checkpoints) {
    L.push("", `## Checkpoints (what the validators recorded)`, "", "```json", JSON.stringify(report.checkpoints, null, 2), "```");
  }
  if (report.finality.length) {
    L.push("", `## Finality`, "", `| Transaction | Last status seen | Finalized |`, `|---|---|---|`);
    report.finality.forEach((f) => L.push(`| [${f.name}](${f.explorer}) | ${f.lastSeenStatus} | ${f.finalized ? "yes" : "not yet"} |`));
  }
  return L.join("\n") + "\n";
}

function writeReport() {
  report.finishedAt = new Date().toISOString();
  const dir = resolve(ROOT, "reports");
  mkdirSync(dir, { recursive: true });
  writeFileSync(resolve(dir, "live-report.json"), JSON.stringify(report, null, 2));
  writeFileSync(resolve(dir, "live-report.md"), markdown());
  log("\n" + "=".repeat(72) + "\n" + markdown() + "=".repeat(72));
  log("saved: reports/live-report.md and reports/live-report.json");
}

async function main() {
  const owner = createAccount(process.env.KW_PRIVATE_KEY || generatePrivateKey());
  const sponsor = createAccount(generatePrivateKey());
  const ownerClient = makeClient(owner);
  const sponsorClient = makeClient(sponsor);
  report.accounts = { owner: owner.address, sponsor: sponsor.address };
  for (const [name, acc, cl] of [["owner", owner, ownerClient], ["sponsor", sponsor, sponsorClient]]) {
    const funded = await fund(cl, acc.address);
    log(`${name} ${acc.address} (${funded === true ? "funded" : funded})`);
  }

  let address = resolveAddress();
  if (address) {
    report.contract.reused = true;
  } else {
    await step("deploy contracts/kept_word.py", async (entry) => {
      const { address: a, hash } = await deployKeptWord(ownerClient, log);
      address = a;
      entry.tx = hash;
      entry.explorer = `${EXPLORER}/tx/${hash}`;
      entry.status = "FINALIZED";
      report.contract.deployTx = hash;
      txs.push({ name: "deploy", hash, client: ownerClient });
    });
  }
  report.contract.address = address;

  await step("read contract version", async () => {
    report.contract.version = await readView(ownerClient, address, "get_version");
    check(typeof report.contract.version === "string" && report.contract.version.length > 0, `contract answers get_version (${report.contract.version})`);
  });

  await step("owner claims starter points", async (entry) => {
    await send(entry, ownerClient, address, "claim_starter_points");
  });
  await step("sponsor claims starter points", async (entry) => {
    await send(entry, sponsorClient, address, "claim_starter_points");
    const { o, s } = await snapshot("after claim", ownerClient, address, owner.address, sponsor.address);
    check(o.balance === 1000 && s.balance === 1000, "owner and sponsor each hold 1000 WP");
  });

  const startAt = nowUnix() + 6 * 60;
  let pledgeId;
  await step("owner opens a pledge (starts in 6 min, 10-minute window, 1 checkpoint)", async (entry) => {
    await send(entry, ownerClient, address, "open_pledge", [
      "KeptWord live run pledge",
      "Publish at least one new dated entry on the proof page during the checkpoint window.",
      proofUrl, BigInt(startAt), 600n, 1n, 1n, 1n, 50n,
    ]);
    const stats = await readView(ownerClient, address, "get_stats");
    pledgeId = stats.total_pledges;
    check(pledgeId >= 1, `pledge #${pledgeId} exists`);
    const { o } = await snapshot("after open", ownerClient, address, owner.address, sponsor.address);
    check(o.locked >= 50, "the 50 WP stake is locked");
  });

  await step("sponsor backs the pledge with 20 WP", async (entry) => {
    await send(entry, sponsorClient, address, "sponsor_pledge", [BigInt(pledgeId), 20n]);
    const preview = await readView(ownerClient, address, "preview_outcomes", [pledgeId]);
    check(preview.if_kept.owner_receives === 70, "if KEPT the owner receives stake + sponsor reward (70)");
    check(preview.if_broken.sponsor_compensation === 25 && preview.if_broken.burned === 25, "if BROKEN the sponsor gets 25 and 25 is burned");
    await snapshot("after sponsor", ownerClient, address, owner.address, sponsor.address);
  });

  if (quick) {
    await step("owner cancels before the start; everybody is refunded", async (entry) => {
      await send(entry, ownerClient, address, "cancel_pledge", [BigInt(pledgeId)]);
      const { o, s, stats } = await snapshot("after cancel", ownerClient, address, owner.address, sponsor.address);
      check(o.balance === 1000 && o.locked === 0, "owner got the stake back");
      check(s.balance === 1000 && s.locked === 0, "sponsor got the sponsorship back");
      check(stats.points_burned === 0, "nothing was burned");
    });
  } else {
    await step("wait until the checkpoint can be verified", async () => {
      const schedule = await readView(ownerClient, address, "get_schedule", [pledgeId]);
      const opensAt = schedule.checkpoints[0].verify_opens_at;
      while (nowUnix() < opensAt + 30) {
        log(`   ${Math.ceil((opensAt + 30 - nowUnix()) / 60)} min left`);
        await sleep(60_000);
      }
    });

    await step("anyone verifies the checkpoint (validators read the proof page)", async (entry) => {
      await send(entry, sponsorClient, address, "verify_checkpoint", [BigInt(pledgeId)]);
      const pledge = await readView(ownerClient, address, "get_pledge", [pledgeId]);
      log(`   pledge status ${pledge.status} | checked ${pledge.checked} | met ${pledge.met} | missed ${pledge.missed}`);
      check(pledge.checked === 1 || pledge.fetch_failures >= 1, "the checkpoint was recorded (or deferred because the page was unreachable)");
      await snapshot("after verify", ownerClient, address, owner.address, sponsor.address);
    });
  }

  report.pledge = await readView(ownerClient, address, "get_pledge", [pledgeId]).catch(() => null);
  report.checkpoints = await readView(ownerClient, address, "list_pledge_checkpoints", [pledgeId]).catch(() => null);
  report.stats = await readView(ownerClient, address, "get_stats").catch(() => null);
  await step("points are conserved", async () => {
    const bal = report.balances[report.balances.length - 1];
    const mine = bal.owner.balance + bal.owner.locked + bal.sponsor.balance + bal.sponsor.locked;
    check(mine + bal.burned <= bal.granted, "this run's accounts plus burned points never exceed the points granted");
    if (!report.contract.reused) check(mine + bal.burned === 2000, "free + locked + burned equals the 2000 WP granted in this run");
  });

  await awaitFinality(ownerClient);
  report.result = report.checks.every((c) => c.ok) ? "PASS" : "FAIL";
}

try {
  await main();
} catch (err) {
  report.result = "FAIL";
  report.error = String(err?.message ?? err);
  console.error("\nrun failed:", report.error);
}
writeReport();
process.exit(report.result === "PASS" ? 0 : 1);
