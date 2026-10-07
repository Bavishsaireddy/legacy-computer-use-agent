#!/usr/bin/env bash
# End-to-end demo. Regenerates capabilities/ and evidence/ from scratch.
# Needs ANTHROPIC_API_KEY for the discovery and probe steps (6 short model runs).
set -uo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] && { set -a; . ./.env; set +a; }
: "${ANTHROPIC_API_KEY:?put ANTHROPIC_API_KEY in .env or export it first}"
export HARBOR_USERNAME=${HARBOR_USERNAME:-teller01} HARBOR_PASSWORD=${HARBOR_PASSWORD:-harbor-demo-pw}
export LAKESIDE_USERNAME=${LAKESIDE_USERNAME:-teller01} LAKESIDE_PASSWORD=${LAKESIDE_PASSWORD:-harbor-demo-pw}
CUA="uv run python -m cua"
BAL=capabilities/member.read_savings_balance.yaml
SUB=capabilities/member.open_sub_account.yaml
APP=http://127.0.0.1:8765

uv run python -m mockapp.server --port 8765 & PID1=$!
uv run python -m mockapp.server --port 8766 --variant lakeside & PID2=$!
trap 'kill $PID1 $PID2 2>/dev/null' EXIT
sleep 1
rm -rf evidence capabilities && mkdir -p evidence capabilities

step() { echo; echo "=== $1"; }
# Runs a replay and carries on whatever the exit code. The result printed here is the caller's copy,
# with real outputs, so it is shown but not saved; the run directory holds the redacted result.json.
run() { local dir=$1; shift; "$@" --runs-dir "evidence/$dir"; }

step "01 discovery (LLM): read a member's savings balance"
$CUA discover --name member.read_savings_balance --runs-dir evidence/01-discovery-read-balance \
  --goal "Look up member {{member_id}} and read their current share savings balance." \
  --param member_id=10042 --verify-param member_id=10077 || exit 1

step "02 probe (LLM): what does an unknown member look like?"
$CUA probe $BAL --param member_id=99999 --runs-dir evidence/02-probe-not-found

step "03 replay: success with a different member"
run 03-replay-success $CUA replay $BAL --param member_id=10077

step "04 replay: business outcome, member not found"
run 04-replay-not-found $CUA replay $BAL --param member_id=88888

step "05 replay: business outcome, permission denied"
run 05-replay-permission-denied $CUA replay $BAL --param member_id=20013

step "06 replay: bad input refused before touching the app"
run 06-replay-invalid-param $CUA replay $BAL --param member_id=12

step "07 replay: recoverable conditions (system notice, then session expiry)"
curl -s "$APP/__fault?notice=1" >/dev/null
run 07-replay-recovered-notice $CUA replay $BAL --param member_id=10042
curl -s "$APP/__fault?expire=1" >/dev/null
run 07-replay-recovered-session-expiry $CUA replay $BAL --param member_id=10042

step "08 replay: hard failure (application error), with screenshot"
curl -s "$APP/__fault?error=1" >/dev/null
run 08-replay-app-error $CUA replay $BAL --param member_id=10042

step "09 replay: stuck, handed to an operator (played by a script) who fixes the screen and answers 'retry'"
PYTHONPATH=. uv run python scripts/handoff_demo.py $BAL evidence/09-replay-human-handoff

step "10 replay: layout drift, fallback locator"
curl -s "$APP/__fault?flat=1" >/dev/null
run 10-replay-layout-drift $CUA replay $BAL --param member_id=10042
curl -s "$APP/__reset" >/dev/null

step "11 replay: the same artifact on a second tenant (lakeside)"
run 11-replay-second-tenant $CUA replay $BAL --tenant lakeside --param member_id=10042

step "12 discovery (LLM): open a sub-account; the irreversible click is approved on the console"
echo approve | $CUA discover --name member.open_sub_account --runs-dir evidence/12-discovery-open-sub-account \
  --goal "Open a new {{account_type}} sub-account for member {{member_id}} with nickname {{nickname}} and an opening deposit of {{deposit}}, confirm it, and read the confirmation number." \
  --param member_id=10042 --param account_type="Holiday Club" --param nickname=Gifts --param deposit=25.00 \
  --operator console || exit 1

step "13 probe (LLM): a deposit below the minimum"
$CUA probe $SUB --param member_id=10042 --param account_type="Holiday Club" --param nickname=Gifts --param deposit=1.00 \
  --runs-dir evidence/13-probe-validation-error

SUBARGS=(--param member_id=10077 --param "account_type=Vacation Club" --param nickname=Trip --param deposit=100.00)
step "14 replay: irreversible capability refused while still a draft"
run 14-replay-irreversible-refused $CUA replay $SUB "${SUBARGS[@]}" --confirm

step "15 approve, then replay with the caller's confirm"
$CUA approve $SUB --by demo-reviewer
run 15-replay-irreversible-committed $CUA replay $SUB "${SUBARGS[@]}" --confirm

step "16 replay: response lost after the commit; side effects unknown, never retried"
curl -s "$APP/__fault?lost=1" >/dev/null
run 16-replay-commit-unknown $CUA replay $SUB "${SUBARGS[@]}" --confirm

curl -s "$APP/__reset" >/dev/null

step "17 replay: slow page load absorbed by the checkpoint wait"
curl -s "$APP/__fault?slow=2500" >/dev/null
run 17-replay-slow-load $CUA replay $BAL --param member_id=10042

step "18 replay: business outcome learned by the probe (deposit below minimum); nothing is committed"
run 18-replay-validation-outcome $CUA replay $SUB --param member_id=10077 --param "account_type=Vacation Club" --param nickname=Trip --param deposit=1.00 --confirm

step "19 replay: dry run of the irreversible capability stops before the commit"
run 19-replay-dry-run $CUA replay $SUB "${SUBARGS[@]}" --dry-run

step "20 replay: session expires as the commit is clicked; the flow is not restarted"
curl -s "$APP/__fault?expire_confirm=1" >/dev/null
run 20-replay-expiry-at-commit $CUA replay $SUB "${SUBARGS[@]}" --confirm

step "21 replay: the irreversible capability on the second tenant (lakeside)"
run 21-replay-second-tenant-irreversible $CUA replay $SUB --tenant lakeside "${SUBARGS[@]}" --confirm

step "22 discovery (LLM): a goal outside the allowlist; the agent is blocked and no capability is saved"
$CUA discover --name support.read_vendor_phone --runs-dir evidence/22-discovery-policy-blocked \
  --goal "Open the Vendor Support page from the menu and read the support phone number." || echo "(discovery stopped, as intended)"

cp capabilities/*.yaml evidence/
echo; echo "done: artifacts in capabilities/, evidence in evidence/"
