#!/bin/bash
#
# Archive an environment's live data, and optionally blank it afterwards.
# Run this ON the server, over SSH or in cPanel -> Terminal.
#
#     bash archive_data.sh                       # archive the prod data dir
#     bash archive_data.sh --data-dir ~/gi-data/prod
#     bash archive_data.sh --blank               # archive, then start empty
#     bash archive_data.sh --dry-run
#
# The archive is a dated tarball under ~/gi-archives. Nothing is deleted
# unless --blank is passed, and --blank refuses to run unless the tarball was
# written and can be listed - blanking without a verified archive is how the
# WhatsApp chat history and the uploaded customer documents get lost for good.
#
# The data directory is recreated empty afterwards; the app rebuilds
# users.json, the databases and the upload directories on first use. Note
# that blanking clears the logins too: the default admin account
# (admin / admin123) comes back and needs its password changed immediately.

set -euo pipefail

DATA_DIR="${GI_DATA_DIR:-$HOME/gi-data/prod}"
ARCHIVE_DIR="${GI_ARCHIVE_DIR:-$HOME/gi-archives}"
BLANK=0
DRY_RUN=0

while [ $# -gt 0 ]; do
        case "$1" in
                --data-dir) DATA_DIR="$2"; shift 2 ;;
                --archive-dir) ARCHIVE_DIR="$2"; shift 2 ;;
                --blank) BLANK=1; shift ;;
                --dry-run) DRY_RUN=1; shift ;;
                -h|--help) sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 0 ;;
                *) echo "Unknown option: $1" >&2; exit 64 ;;
        esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
note() { printf '    %s\n' "$1"; }
die()  { printf '\n\033[31mFAILED: %s\033[0m\n' "$1" >&2; exit 1; }

[ -d "$DATA_DIR" ] || die "no data directory at $DATA_DIR (set --data-dir)"

STAMP="$(date +%Y%m%d-%H%M%S)"
LABEL="$(basename "$DATA_DIR")"
ARCHIVE="$ARCHIVE_DIR/gi-$LABEL-$STAMP.tar.gz"

step "Archiving"
note "from     $DATA_DIR"
note "to       $ARCHIVE"
[ "$DRY_RUN" = 1 ] && note "DRY RUN - nothing will be written or removed"

if [ "$DRY_RUN" = 1 ]; then
        note "would archive:"
        ls -A "$DATA_DIR" | sed 's/^/      /'
else
        mkdir -p "$ARCHIVE_DIR"
        # -C so the tarball holds the directory's contents under one name,
        # which restores cleanly into a differently-named data dir later.
        tar -czf "$ARCHIVE" -C "$(dirname "$DATA_DIR")" "$LABEL"
        [ -s "$ARCHIVE" ] || die "the archive is empty - refusing to go further"
        tar -tzf "$ARCHIVE" >/dev/null || die "the archive will not list - refusing to go further"
        note "archived $(du -h "$ARCHIVE" | cut -f1), $(tar -tzf "$ARCHIVE" | wc -l) entries"
fi

if [ "$BLANK" = 0 ]; then
        step "Done"
        note "Data left in place. Re-run with --blank to start this environment empty."
        exit 0
fi

step "Blanking $DATA_DIR"
if [ "$DRY_RUN" = 1 ]; then
        note "would remove everything under $DATA_DIR and recreate it empty"
else
        # Replace the directory rather than globbing inside it: a glob misses
        # dotfiles, and a half-cleared data dir is worse than either state.
        rm -rf "${DATA_DIR:?}"
        mkdir -p "$DATA_DIR"
        note "empty; the app recreates users.json, the databases and the"
        note "upload directories on the next request"
        note "the default admin login (admin / admin123) is back - change it now"
fi

step "Done"
note "Archive kept at $ARCHIVE"
note "Restore with: tar -xzf $ARCHIVE -C $(dirname "$DATA_DIR")"
