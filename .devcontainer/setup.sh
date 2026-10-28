#!/usr/bin/env bash
# Runs once when the Codespace is created. The Python tests need no packages at all.
set -euo pipefail
npm install
cat <<'MSG'

KeptWord is ready. Deployed on Studionet: 0x0c002de09871195fb223Dad5b3E89587c2fb346f

  npm test             offline contract tests (pure Python, no network, no wallet)
  npm run mutation     mutation check: proves the tests catch planted bugs
  npm run live:quick   live run on the deployed contract + printed report (about 8 minutes)
  npm run live         full live run incl. checkpoint verification (about 25 minutes)
  npm run deploy       deploy contracts/kept_word.py to Studionet, prints the address
  npm run smoke                           live smoke test (about 5 minutes)
  npm run smoke:verify                     also verifies a checkpoint live (about 25 minutes)
MSG
