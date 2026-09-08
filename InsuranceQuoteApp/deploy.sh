#!/bin/bash
#
# Deploy to the cPanel/Passenger host. Run this ON the server, over SSH or in
# cPanel -> Terminal:
#
#     bash ~/repo/InsuranceQuoteApp/deploy.sh
#     bash ~/repo/InsuranceQuoteApp/deploy.sh --branch quote-automation
#     bash ~/repo/InsuranceQuoteApp/deploy.sh --dry-run
#
# What it does, stopping at the first failure:
#   1. fetches and fast-forwards the checkout
#   2. installs requirements.txt into the app virtualenv
#   3. copies code into the app root, leaving live data untouched
#   4. repairs the .htaccess rewrite override
#   5. runs preflight.py
#   6. restarts Passenger
#
# It is deliberately conservative about two things:
#
#   Live data. users.json, auth_secret.key, messages.db, reminders.db, the
#   sent/batch json, documents/ and chat_media/ are written by the running
#   server and are gitignored, so the repo copy is empty or stale. The sync
#   excludes them and never uses --delete; the server's copies are the real
#   ones and losing them means losing logins, chat history and customer IDs.
#
#   The working tree. A fast-forward-only pull refuses to run over local edits
#   rather than discarding them - someone may have hot-fixed on the server.
#
set -euo pipefail

APP_ROOT="${GI_APP_ROOT:-$HOME/public_html/app.gravityinsurance.in}"
VENV="${GI_VENV:-$HOME/virtualenv/public_html/app.gravityinsurance.in/3.11}"
BRANCH="${GI_DEPLOY_BRANCH:-main}"
REPO_DIR="${GI_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SOURCE_DIR="$REPO_DIR/InsuranceQuoteApp"
DRY_RUN=0

while [ $# -gt 0 ]; do
        case "$1" in
                --branch) BRANCH="$2"; shift 2 ;;
                --dry-run) DRY_RUN=1; shift ;;
                --app-root) APP_ROOT="$2"; shift 2 ;;
                --venv) VENV="$2"; shift 2 ;;
                -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
                *) echo "Unknown option: $1" >&2; exit 64 ;;
        esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
note() { printf '    %s\n' "$1"; }
die()  { printf '\n\033[31mFAILED: %s\033[0m\n' "$1" >&2; exit 1; }
run()  { if [ "$DRY_RUN" = 1 ]; then note "would run: $*"; else "$@"; fi; }

PYTHON="$VENV/bin/python"
PIP="$VENV/bin/pip"

# Live server data - never overwritten, never deleted. Keep in step with
# .gitignore; anything here that the repo also tracks would be clobbered.
EXCLUDES=(
        --exclude ".git"
        --exclude "__pycache__"
        --exclude "*.pyc"
        --exclude "users.json"
        --exclude "auth_secret.key"
        --exclude "messages.db"
        --exclude "messages.db-wal"
        --exclude "messages.db-shm"
        --exclude "reminders.db"
        --exclude "reminders.log"
        --exclude "sent_quote.json"
        --exclude "sent_payment.json"
        --exclude "batch_quote.json"
        --exclude "batch_payment.json"
        --exclude "documents/"
        --exclude "chat_media/"
        --exclude "*.log"
        --exclude ".htaccess"
)

step "Checking the environment"
[ -d "$SOURCE_DIR" ] || die "no InsuranceQuoteApp/ under $REPO_DIR (set GI_REPO_DIR)"
[ -d "$APP_ROOT" ]   || die "app root $APP_ROOT does not exist (set GI_APP_ROOT)"
[ -x "$PYTHON" ]     || die "no python at $PYTHON (set GI_VENV)"
command -v rsync >/dev/null 2>&1 || die "rsync is not installed on this host"
note "repo      $REPO_DIR"
note "app root  $APP_ROOT"
note "venv      $VENV"
note "branch    $BRANCH"
[ "$DRY_RUN" = 1 ] && note "DRY RUN - nothing will be changed"

step "Updating the checkout"
cd "$REPO_DIR"
if [ -n "$(git status --porcelain)" ]; then
        git status --short
        die "the checkout has local changes. Commit or discard them by hand -
     this script will not throw away work it did not make."
fi
run git fetch --prune origin
# Fast-forward only: a diverged branch means someone committed on the server,
# and merging that unattended is how you lose it.
run git checkout "$BRANCH"
run git merge --ff-only "origin/$BRANCH" || die "cannot fast-forward $BRANCH onto origin/$BRANCH"
note "now at $(git log -1 --format='%h %s')"

step "Installing dependencies"
run "$PIP" install --quiet --upgrade -r "$SOURCE_DIR/requirements.txt" \
        || die "pip install failed - see the output above"

step "Syncing code to the app root"
if [ "$DRY_RUN" = 1 ]; then
        rsync -a --itemize-changes --dry-run "${EXCLUDES[@]}" "$SOURCE_DIR/" "$APP_ROOT/"
else
        rsync -a --itemize-changes "${EXCLUDES[@]}" "$SOURCE_DIR/" "$APP_ROOT/"
fi

step "Checking the .htaccess rewrite override"
# The subdomain docroot lives inside public_html, whose .htaccess rewrites
# non-file paths to /index.html for the main React site. Without an override,
# every route but / returns 500 - and cPanel regenerates this file whenever the
# Python app is recreated, so the override has to be re-checked every deploy.
HTACCESS="$APP_ROOT/.htaccess"
if [ ! -f "$HTACCESS" ]; then
        note "no .htaccess at $HTACCESS - Passenger usually writes one; check cPanel"
elif grep -q "RewriteRule \^ - \[L\]" "$HTACCESS"; then
        note "override present"
else
        note "override MISSING - inserting it above the Passenger block"
        if [ "$DRY_RUN" = 1 ]; then
                note "would back up to .htaccess.bak and prepend the override"
        else
                cp "$HTACCESS" "$HTACCESS.bak.$(date +%Y%m%d%H%M%S)"
                printf 'RewriteEngine On\nRewriteRule ^ - [L]\n\n%s\n' \
                        "$(cat "$HTACCESS")" > "$HTACCESS.new"
                mv "$HTACCESS.new" "$HTACCESS"
                note "inserted; previous file kept as .htaccess.bak.*"
        fi
fi

step "Running preflight"
if [ "$DRY_RUN" = 1 ]; then
        note "would run: $PYTHON $APP_ROOT/preflight.py"
else
        cd "$APP_ROOT"
        "$PYTHON" preflight.py || die "preflight found a problem - NOT restarting.
     The old code is still serving, so the site is unaffected. Fix the FAIL
     lines above and run this script again."
fi

step "Restarting Passenger"
# Passenger watches tmp/restart.txt and reloads the app on the next request.
run mkdir -p "$APP_ROOT/tmp"
run touch "$APP_ROOT/tmp/restart.txt"
note "restart requested; the next request reloads the app"

step "Done"
note "Deployed $BRANCH to $APP_ROOT"
note "If anything looks wrong, check ~/logs/passenger.log"
