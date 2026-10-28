#!/usr/bin/env node
// Live smoke test against a deployed KeptWord on Studionet.
//   node e2e/smoke.mjs                              claim -> open -> sponsor -> cancel (about 5 minutes)
//   node e2e/smoke.mjs --verify                        additionally waits for the first checkpoint
//                                                  and verifies it (about 25 minutes)
//   KW_PROOF_URL=https://...  public https page used as proof (default: https://example.com/)
import { createAccount, generatePrivateKey, makeClient, fund, readView, writeTx, nowUnix, sleep, EXPLORER, DEPLOYED_ADDRESS } from "./lib.mjs";

const address = process.env.KW_ADDRESS || DEPLOYED_ADDRESS;
const withVerify = process.argv.includes("--verify");
const proofUrl = process.env.KW_PROOF_URL || "https://example.com/";

const owner = createAccount(generatePrivateKey());
const sponsor = createAccount(generatePrivateKey());
const ownerClient = makeClient(owner);
const sponsorClient = makeClient(sponsor);
for (const [name, acc, cl] of [["owner", owner, ownerClient], ["sponsor", sponsor, sponsorClient]]) {
  const funded = await fund(cl, acc.address);
  console.log(`${name} ${acc.address} (${funded === true ? "funded" : funded})`);
}

const step = (msg) => console.log(`\n> ${msg}`);
const check = (cond, msg) => { if (!cond) throw new Error("CHECK FAILED: " + msg); console.log(`  ok: ${msg}`); };

step("version");
console.log("  ", await readView(ownerClient, address, "get_version"));

step("both accounts claim their starter points");
await writeTx(ownerClient, address, "claim_starter_points");
await writeTx(sponsorClient, address, "claim_starter_points");
let standing = await readView(ownerClient, address, "get_standing", [owner.address]);
check(standing.balance === 1000, "owner starts with 1000 WP");

step("owner opens a pledge (starts in 6 minutes, 10-minute windows, 1 checkpoint)");
const startAt = nowUnix() + 6 * 60;
const { hash: openHash } = await writeTx(ownerClient, address, "open_pledge", [
  "Smoke test pledge for KeptWord",
  "Publish at least one new dated entry on the proof page during the checkpoint window.",
  proofUrl, BigInt(startAt), 600n, 1n, 1n, 1n, 50n,
]);
console.log(`   ${EXPLORER}/tx/${openHash}`);
const stats = await readView(ownerClient, address, "get_stats");
const pledgeId = stats.total_pledges;
check(pledgeId >= 1, `pledge #${pledgeId} exists`);
standing = await readView(ownerClient, address, "get_standing", [owner.address]);
check(standing.balance === 950 && standing.locked === 50, "50 WP are locked as stake");

step("sponsor backs the pledge with 20 WP");
await writeTx(sponsorClient, address, "sponsor_pledge", [BigInt(pledgeId), 20n]);
const preview = await readView(ownerClient, address, "preview_outcomes", [pledgeId]);
check(preview.if_kept.owner_receives === 70, "owner would receive stake + sponsor reward if the pledge is kept");
check(preview.if_broken.sponsor_compensation === 25 && preview.if_broken.burned === 25, "a broken pledge pays the sponsor half the stake");

if (!withVerify) {
  step("owner cancels before the start; everybody is refunded");
  await writeTx(ownerClient, address, "cancel_pledge", [BigInt(pledgeId)]);
  const after = await readView(ownerClient, address, "get_standing", [owner.address]);
  const sp = await readView(ownerClient, address, "get_standing", [sponsor.address]);
  check(after.balance === 1000 && sp.balance === 1000, "stakes and sponsorships were refunded");
  console.log("\nSmoke test passed. Run with --verify to also exercise checkpoint verification.");
  process.exit(0);
}

step("waiting for the checkpoint to open (window end + grace)");
const schedule = await readView(ownerClient, address, "get_schedule", [pledgeId]);
const opensAt = schedule.checkpoints[0].verify_opens_at;
while (nowUnix() < opensAt + 30) { console.log(`   ${Math.ceil((opensAt + 30 - nowUnix()) / 60)} min left`); await sleep(60_000); }

step("anyone verifies the checkpoint (validators read the proof page)");
const { hash } = await writeTx(sponsorClient, address, "verify_checkpoint", [BigInt(pledgeId)]);
console.log(`   ${EXPLORER}/tx/${hash}`);
const pledge = await readView(ownerClient, address, "get_pledge", [pledgeId]);
console.log("   pledge status:", pledge.status, "| checked:", pledge.checked, "| met:", pledge.met, "| missed:", pledge.missed);
check(pledge.checked === 1 || pledge.fetch_failures >= 1, "the checkpoint was recorded (or deferred because the page was unreachable)");
console.log("\nVerification smoke test finished.");
