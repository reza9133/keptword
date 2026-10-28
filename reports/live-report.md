# KeptWord live report

- Result: **PASS**
- Network: GenLayer Studionet
- Mode: full (verify path)
- Started: 2026-10-28T15:23:04.952Z
- Finished: 2026-10-28T15:44:30.597Z
- Contract: `0x0c002de09871195fb223Dad5b3E89587c2fb346f` (version 1.0.0, reused)
- Explorer: https://explorer-studio.genlayer.com/contracts/0x0c002de09871195fb223Dad5b3E89587c2fb346f
- Owner: `0xaadA72561544D8A6D9846f8406D555B4C63D14E5`
- Sponsor: `0xA47B3acABc3D7d12e224EF511bFA8BD0dA6dB04B`
- Proof page: https://github.com/vercel/next.js/commits/canary

## Steps

| # | Step | Result | Seconds | Status | Transaction |
|---|---|---|---|---|---|
| 1 | read contract version | ok | 1 |   |  |
| 2 | owner claims starter points | ok | 6 | 5 unknown | [tx](https://explorer-studio.genlayer.com/tx/0x2d3947ce6b6a0ca4fabfd75bc8bae746db2311e686a6090fdd03c8e30991ea80) |
| 3 | sponsor claims starter points | ok | 10 | 5 unknown | [tx](https://explorer-studio.genlayer.com/tx/0x3c00909c8be13ceef3ff055af0534323fb24d82a2ced28f40b8ff5fa5dc713ba) |
| 4 | owner opens a pledge (starts in 6 min, 10-minute window, 1 checkpoint) | ok | 27 | 5 unknown | [tx](https://explorer-studio.genlayer.com/tx/0xd2fa0625ac961959e2f98a43ef01113c2732b5db4c6df91f0196f35b1de39f78) |
| 5 | sponsor backs the pledge with 20 WP | ok | 11 | 5 unknown | [tx](https://explorer-studio.genlayer.com/tx/0x29a6baea58bce48e92e7757a8c923e75bf5219eaa7153330f96513abd3514c94) |
| 6 | wait until the checkpoint can be verified | ok | 1142 |   |  |
| 7 | anyone verifies the checkpoint (validators read the proof page) | ok | 63 | 5 unknown | [tx](https://explorer-studio.genlayer.com/tx/0x4a84b1cc716210cc45b3a711c1a5f703c7715db86beefb5711dbb74c6855554d) |
| 8 | points are conserved | ok | 0 |   |  |

## Checks

- [x] contract answers get_version (1.0.0)
- [x] owner and sponsor each hold 1000 WP
- [x] pledge #2 exists
- [x] the 50 WP stake is locked
- [x] if KEPT the owner receives stake + sponsor reward (70)
- [x] if BROKEN the sponsor gets 25 and 25 is burned
- [x] the checkpoint was recorded (or deferred because the page was unreachable)
- [x] this run's accounts plus burned points never exceed the points granted

## Points after each stage

| Stage | Owner free | Owner locked | Sponsor free | Sponsor locked | Burned |
|---|---|---|---|---|---|
| after claim | 1000 | 0 | 1000 | 0 | 0 |
| after open | 950 | 50 | 1000 | 0 | 0 |
| after sponsor | 950 | 50 | 980 | 20 | 0 |
| after verify | 950 | 50 | 980 | 20 | 0 |

## Pledge

```json
{
  "appeal_deadline": 1793223783,
  "appeals_used": 0,
  "checked": 1,
  "checkpoints": 1,
  "closed_at": 0,
  "commitment": "Publish at least one new dated entry on the proof page during the checkpoint window.",
  "created_at": 1793201003,
  "fetch_failures": 0,
  "id": 2,
  "interval_seconds": 600,
  "last_attempt_at": 0,
  "met": 0,
  "min_items": 1,
  "min_met": 1,
  "missed": 1,
  "owner": "0xaada72561544d8a6d9846f8406d555b4c63d14e5",
  "proof_url": "https://github.com/vercel/next.js/commits/canary",
  "sponsor_count": 1,
  "sponsor_pool": 20,
  "stake": 50,
  "start_at": 1793201363,
  "status": "BREACHED",
  "title": "KeptWord live run pledge"
}
```

## Checkpoints (what the validators recorded)

```json
[
  {
    "appeal_url": "",
    "appealed": false,
    "checked_at": 1793202183,
    "evidence": "",
    "evidence_hash": "5a3ffd9c2315c8b713469e49d8c227cec1281ef869fdd1cda18255ff2244eacc",
    "evidence_len": 3919,
    "first_reason": "",
    "id": 1,
    "index": 0,
    "items_found": 0,
    "note": "The page shows commits dated 2026-10-28, but no times placing any new dated entry inside the 15:29–15:39 UTC window.",
    "outcome": "MISSED",
    "pledge_id": 2,
    "reason": "NOT_ENOUGH_EVIDENCE",
    "verifier": "0xa47b3acabc3d7d12e224ef511bfa8bd0da6db04b"
  }
]
```

## Finality

| Transaction | Last status seen | Finalized |
|---|---|---|
| [owner claims starter points](https://explorer-studio.genlayer.com/tx/0x2d3947ce6b6a0ca4fabfd75bc8bae746db2311e686a6090fdd03c8e30991ea80) | FINALIZED | yes |
| [sponsor claims starter points](https://explorer-studio.genlayer.com/tx/0x3c00909c8be13ceef3ff055af0534323fb24d82a2ced28f40b8ff5fa5dc713ba) | FINALIZED | yes |
| [owner opens a pledge (starts in 6 min, 10-minute window, 1 checkpoint)](https://explorer-studio.genlayer.com/tx/0xd2fa0625ac961959e2f98a43ef01113c2732b5db4c6df91f0196f35b1de39f78) | FINALIZED | yes |
| [sponsor backs the pledge with 20 WP](https://explorer-studio.genlayer.com/tx/0x29a6baea58bce48e92e7757a8c923e75bf5219eaa7153330f96513abd3514c94) | FINALIZED | yes |
| [anyone verifies the checkpoint (validators read the proof page)](https://explorer-studio.genlayer.com/tx/0x4a84b1cc716210cc45b3a711c1a5f703c7715db86beefb5711dbb74c6855554d) | FINALIZED | yes |
