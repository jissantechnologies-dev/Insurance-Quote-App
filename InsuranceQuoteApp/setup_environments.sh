#!/bin/bash
#
# One-time server-side setup for the dev/production split. Run this ON the
# server, over SSH or in cPanel -> Terminal:
#
#     bash ~/repo/InsuranceQuoteApp/setup_environments.sh --dry-run
#     bash ~/repo/InsuranceQuoteApp/setup_environments.sh
#
# It does the parts that can be scripted, in the order they have to happen:
#
#   1. archives production's current data to ~/gi-archives
#   2. creates ~/gi-data/production (empty) and ~/gi-data/dev (seeded)
#   3. leaves production's data directory empty and clears the live data out
#      of its app root, so the live site starts blank
#   4. prints the cPanel environment variables and the cron line to set
#
# What it deliberately does NOT do: create the dev subdomain and its Python
# app, or set environment variables. Those are cPanel UI steps - see
# ENVIRONMENTS.md - and this script prints exactly what to enter.
#
# Production's data currently lives in its app root, because that is where
# the app wrote it before the split. Step 3 removes it from there as well as
# leaving the new data directory empty - a leftover users.json in the app
# root is read by nothing after the split, but it is a confusing thing to
# find later, and it is in the archive either way.
#
# --keep-production-data moves that data into the new data directory instead
# of clearing it, for a rehearsal run or if you change your mind about
# starting clean. Nothing is deleted before the archive verifies.

set -euo pipefail

REPO_DIR="${GI_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SOURCE_DIR="$REPO_DIR/InsuranceQuoteApp"
PROD_DOMAIN="${GI_PROD_DOMAIN:-app.gravityinsurance.in}"
# A single-label subdomain: cPanel's "Create a New Domain" rejects a
# four-label host ("You must specify a subdomain"), and a wildcard certificate
# only covers one level, so dev.app.* would also have had no SSL.
DEV_DOMAIN="${GI_DEV_DOMAIN:-devapp.gravityinsurance.in}"
PROD_APP_ROOT="${GI_PROD_APP_ROOT:-$HOME/public_html/$PROD_DOMAIN}"
# Dev sits outside public_html so the parent SPA .htaccess rewrite cannot
# leak into it and turn every request into a 500.
DEV_APP_ROOT="${GI_DEV_APP_ROOT:-$HOME/$DEV_DOMAIN}"
DATA_ROOT="${GI_DATA_ROOT:-$HOME/gi-data}"
PROD_DATA="$DATA_ROOT/production"
DEV_DATA="$DATA_ROOT/dev"
VENV="${GI_VENV:-$HOME/virtualenv/public_html/$PROD_DOMAIN/3.11}"
BLANK_PRODUCTION=1
DRY_RUN=0

while [ $# -gt 0 ]; do
        case "$1" in
                --keep-production-data) BLANK_PRODUCTION=0; shift ;;
                --dry-run) DRY_RUN=1; shift ;;
                --data-root) DATA_ROOT="$2"; PROD_DATA="$2/production"; DEV_DATA="$2/dev"; shift 2 ;;
                --app-root) PROD_APP_ROOT="$2"; shift 2 ;;
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

# Live data the app wrote into the production app root before the split.
# Anything not here is code and stays in the app root.
LIVE_NAMES=(
        users.json auth_secret.key messages.db messages.db-wal messages.db-shm
        reminders.db reminders.log sent_quote.json sent_payment.json
        batch_quote.json batch_payment.json customers.json newcustomer.txt
        newcustomer.xlsx customerxlfile.xlsx documents chat_media
)

step "Checking the environment"
[ -d "$SOURCE_DIR" ]     || die "no InsuranceQuoteApp/ under $REPO_DIR (set GI_REPO_DIR)"
[ -d "$PROD_APP_ROOT" ]  || die "production app root $PROD_APP_ROOT does not exist"
[ -x "$PYTHON" ]         || die "no python at $PYTHON (set GI_VENV)"
[ -f "$SOURCE_DIR/bootstrap_data.py" ] || die "the checkout predates the split -
     pull the quote-automation branch first, then run this again"
note "repo         $REPO_DIR"
note "prod app     $PROD_APP_ROOT"
note "prod data    $PROD_DATA"
note "dev data     $DEV_DATA"
note "production   $([ "$BLANK_PRODUCTION" = 1 ] && echo 'starts EMPTY (--keep-production-data to keep it)' || echo 'keeps its current data')"
[ "$DRY_RUN" = 1 ] && note "DRY RUN - nothing will be changed"

step "Archiving production's current data"
if [ "$DRY_RUN" = 1 ]; then
        note "would run: bash $SOURCE_DIR/archive_data.sh --data-dir $PROD_APP_ROOT"
else
        bash "$SOURCE_DIR/archive_data.sh" --data-dir "$PROD_APP_ROOT" \
                || die "the archive failed - stopping before anything is moved"
fi

step "Creating the data directories"
run mkdir -p "$PROD_DATA" "$DEV_DATA"
if [ "$DRY_RUN" = 1 ]; then
        note "would seed $DEV_DATA from $SOURCE_DIR/seed"
else
        GI_DATA_DIR="$DEV_DATA" GI_ENV_NAME=dev "$PYTHON" \
                "$SOURCE_DIR/bootstrap_data.py" --seed
fi

step "Placing production's data"
if [ "$BLANK_PRODUCTION" = 1 ]; then
        note "leaving $PROD_DATA empty"
        removed=0
        for name in "${LIVE_NAMES[@]}"; do
                source_path="$PROD_APP_ROOT/$name"
                [ -e "$source_path" ] || continue
                run rm -rf "$source_path"
                note "$([ "$DRY_RUN" = 1 ] && echo 'would clear' || echo 'cleared ') $name from the app root"
                removed=$((removed + 1))
        done
        note "$removed item(s) $([ "$DRY_RUN" = 1 ] && echo "would be cleared" || echo "cleared"); all of it is in the archive"
        note "the app recreates users.json, the databases and the upload dirs"
        note "the default admin login (admin / admin123) will be back - change it"
else
        moved=0
        for name in "${LIVE_NAMES[@]}"; do
                source_path="$PROD_APP_ROOT/$name"
                [ -e "$source_path" ] || continue
                if [ -e "$PROD_DATA/$name" ]; then
                        note "kept   $name (already in the data dir)"
                        continue
                fi
                run mv "$source_path" "$PROD_DATA/$name"
                note "$([ "$DRY_RUN" = 1 ] && echo 'would move' || echo 'moved    ') $name"
                moved=$((moved + 1))
        done
        note "$moved item(s) $([ "$DRY_RUN" = 1 ] && echo "would move" || echo "moved") out of the app root"
fi

step "What is left to do by hand"
cat <<INSTRUCTIONS

    These are cPanel UI steps - this script cannot do them.

    1. Domains -> Create a Domain
         domain:        $DEV_DOMAIN
         document root: $DEV_APP_ROOT
                        (share document root: leave UNCHECKED)

    2. Setup Python App -> Create Application
         python:        3.11
         app root:      $DEV_APP_ROOT
         app URL:       $DEV_DOMAIN
         startup file:  app.py
         entry point:   application

    3. Environment variables

       On the DEV app:
         GI_DATA_DIR       $DEV_DATA
         GI_ENV_NAME       dev
         GI_PEER_DATA_DIR  $PROD_DATA
         (plus the same GI_WA_* and GI_QUOTE_CONTACT values as production)

       On the PRODUCTION app:
         GI_DATA_DIR       $PROD_DATA
         GI_ENV_NAME       production

       Restart both apps afterwards.

    4. Deploy both

         bash $SOURCE_DIR/deploy.sh --env dev
         bash $SOURCE_DIR/deploy.sh --env prod

    5. Cron Jobs -> replace the reminder job with:

         0 9 * * * GI_ENV_FILE=\$HOME/gi.env GI_DATA_DIR=$PROD_DATA \\
             $PYTHON $PROD_APP_ROOT/send_reminders.py \\
             >> $PROD_DATA/reminders.log 2>&1

       The old line has no GI_DATA_DIR, so after the split it would read the
       app directory, find no customers and silently send nothing.

INSTRUCTIONS

step "Done"
note "Archive is under \$HOME/gi-archives - keep it until both sites look right"
