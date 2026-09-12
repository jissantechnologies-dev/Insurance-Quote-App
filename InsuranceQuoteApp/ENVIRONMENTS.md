# Dev and production environments

Two independent instances of the same app on one cPanel account:

| | production | dev |
|---|---|---|
| URL | `app.gravityinsurance.in` | `devapp.gravityinsurance.in` |
| app root | `~/public_html/app.gravityinsurance.in` | `~/devapp.gravityinsurance.in` |
| virtualenv | `~/virtualenv/public_html/app.gravityinsurance.in/3.11` | `~/virtualenv/devapp.gravityinsurance.in/3.11` |
| data dir | `~/gi-data/production` | `~/gi-data/dev` |
| default branch | `main` | `quote-automation` |
| data | starts empty | seeded with the customer list |
| reminder cron | yes | only with a non-overlapping list |
| page banner | none | orange "DEV ENVIRONMENT" bar |

They share the code and the WhatsApp credentials. They share nothing else.

### Why `devapp` and not `dev.app`

`dev.app.gravityinsurance.in` was the obvious name and does not work here.
cPanel's **Create a New Domain** rejects a four-label host with "You must
specify a subdomain", and a wildcard certificate for `*.gravityinsurance.in`
only covers one level, so even once created it would have had no SSL. A
single-label subdomain avoids both.

Dev's app root also sits **outside** `public_html`. Anything under
`public_html` inherits the React site's `.htaccess` rewrite, which answers
every request with a 500 unless an override is written into the subdomain's
own `.htaccess`. Production still lives inside `public_html` and still needs
that override; dev sidesteps it entirely.

## How the split works

Every live-data path resolves against `GI_DATA_DIR` (see `paths.py`), which
sits **outside** both app roots so a deploy can never rsync over it. Unset, it
falls back to the app directory, so a local checkout keeps working with no
configuration at all.

`GI_ENV_NAME` labels the instance. It defaults to `production`, on the
principle that an unlabelled server is the live one: forgetting to set it on
dev is a visible mislabel, whereas the reverse would quietly mark the real
site as safe to experiment on.

Live data, per environment: `users.json`, `auth_secret.key`, `messages.db`,
`reminders.db`, `customers.json`, `newcustomer.txt`, any `*.xlsx` customer
workbook, `sent_*.json`, `batch_*.json`, `documents/`, `chat_media/`.

`customers.json`, `newcustomer.txt` and `customerxlfile.xlsx` used to be
tracked in git and copied out by every deploy, which meant the repo's copy
overwrote the server's client list. They are now gitignored live data; the
starting copies live in `seed/` and only ever reach a data directory when
`bootstrap_data.py --seed` is run on purpose.

## First-time setup

`setup_environments.sh` does steps 1 and 2 in one go — archive production,
create both data directories, seed dev, and clear the live data out of
production's app root. It prints the cPanel values for steps 3-5 as it
finishes:

```bash
cd ~/repo && git fetch origin && git checkout quote-automation && git pull
bash ~/repo/InsuranceQuoteApp/setup_environments.sh --dry-run
bash ~/repo/InsuranceQuoteApp/setup_environments.sh
```

Pass `--keep-production-data` to move production's current data into its new
data directory instead of starting blank. The steps below are what that
script automates, written out in case you would rather run them one at a
time.

### 1. Archive production, then blank it

```bash
cd ~/repo/InsuranceQuoteApp
bash archive_data.sh --data-dir ~/public_html/app.gravityinsurance.in --dry-run
bash archive_data.sh --data-dir ~/public_html/app.gravityinsurance.in --blank
```

Today production's data still sits in its app root, which is why `--data-dir`
points there for this one run. The tarball lands in `~/gi-archives/` and the
script refuses to delete anything until it has verified the archive lists.

**Blanking clears the logins too.** The default admin account
(`admin` / `admin123`) comes back on the next request — change its password
immediately.

### 2. Create the data directories

```bash
PY=~/virtualenv/public_html/app.gravityinsurance.in/3.11/bin/python
GI_DATA_DIR=~/gi-data/production $PY ~/repo/InsuranceQuoteApp/bootstrap_data.py
GI_DATA_DIR=~/gi-data/dev GI_ENV_NAME=dev $PY ~/repo/InsuranceQuoteApp/bootstrap_data.py --seed
```

Production ends up empty; dev gets the seeded customer list. To give dev the
real archived data instead, unpack the tarball into `~/gi-data/dev`.

### 3. Create the dev subdomain in cPanel

0. **DNS**: an `A` record `devapp` → the server IP. The name field is
   relative — entering the full host doubles the domain.
1. **Domains → Create a New Domain**: `devapp.gravityinsurance.in`, document
   root `devapp.gravityinsurance.in`, **share document root unchecked** (that
   setting is permanent). Removing the `public_html/` prefix is the point —
   see above.
2. **Setup Python App**: Python 3.11, application root
   `devapp.gravityinsurance.in`, application URL the new subdomain, startup
   file `app.py`, entry point `application`. Passenger log
   `logs/devapp-passenger.log`.
3. In that app's **Environment variables**, add:
   - `GI_DATA_DIR` = `/home/USER/gi-data/dev`
   - `GI_ENV_NAME` = `dev`
   - `GI_PEER_DATA_DIR` = `/home/USER/gi-data/production`
   - the same `GI_WA_*` and `GI_QUOTE_CONTACT` values as production.
4. Add the same variables to the **production** app, with
   `GI_DATA_DIR=/home/USER/gi-data/production` and `GI_ENV_NAME=production`.
5. Restart both apps.

`GI_WA_VERIFY_TOKEN` on dev is inert: Meta points the webhook at one URL, so
inbound messages keep arriving on production only. Repointing the webhook at
dev would stop production receiving replies.

The `.htaccess` rewrite override (`RewriteEngine On` / `RewriteRule ^ - [L]`)
is what production needs to survive the React site's rewrite; `deploy.sh`
checks for it and inserts it if missing. Dev, being outside `public_html`,
inherits nothing and does not need it.

### 4. Deploy

```bash
bash ~/repo/InsuranceQuoteApp/deploy.sh --env dev --dry-run
bash ~/repo/InsuranceQuoteApp/deploy.sh --env dev
bash ~/repo/InsuranceQuoteApp/deploy.sh --env prod
```

`--env` picks the app root, virtualenv, data directory and default branch. It
defaults to `prod`, because that is the environment whose settings must never
be guessed wrong. The deploy refuses to run if the data directory is missing
or sits inside the app root.

## Day-to-day

Test on dev, then promote:

```bash
# on dev, from the feature branch
bash deploy.sh --env dev --branch my-feature
# once it looks right, merge to main and
bash deploy.sh --env prod
```

## The WhatsApp caveat

Both environments send from the **same** WhatsApp number. A dev test message
is indistinguishable from a production one at the customer's end, and it
counts against the same template limits and quality rating.

The risk that matters is **overlapping customer lists**, not the shared number
itself. Each environment keeps its own `reminders.db`, so neither can see that
the other has already messaged someone; a customer listed in both gets
reminded twice, once by each cron. Lists that share no number are safe, and
dev can then run its own cron.

`send_reminders.py` enforces exactly that. On a non-production environment it
compares today's recipients against the customer list at `GI_PEER_DATA_DIR`
and refuses to send if any number appears in both:

```bash
GI_DATA_DIR=~/gi-data/dev GI_ENV_NAME=dev \
GI_PEER_DATA_DIR=~/gi-data/production \
    $PY send_reminders.py --dry-run
```

Numbers are normalized first, so `9941456453` in one list and
`919941456453` in the other still count as the same person. `--dry-run`
reports what it finds and carries on; `--force` skips the check. If
`GI_PEER_DATA_DIR` is unset or unreadable the send is refused — unverifiable
is not the same as safe.

Two practical points:

- **Keep dev's list to test numbers.** Dev seeded from production, so its
  starting list is entirely overlapping — every number in it is a real
  customer. Cut it down to your own numbers before running a dev send, or the
  guard will (correctly) block you.
- **A dev cron needs `GI_PEER_DATA_DIR` in the crontab**, the same way it
  needs `GI_DATA_DIR` — cron loads none of the cPanel app's variables.

If sharing the number becomes awkward, the fix is a Meta test number and a
separate `GI_ENV_FILE` for dev — no code change, just different `GI_WA_*`
values on the dev app.

## The reminder cron

Production only, once a day:

```
0 9 * * * GI_ENV_FILE=/home/USER/gi.env GI_DATA_DIR=/home/USER/gi-data/production \
    /home/USER/virtualenv/public_html/app.gravityinsurance.in/3.11/bin/python \
    /home/USER/public_html/app.gravityinsurance.in/send_reminders.py \
    >> /home/USER/gi-data/production/reminders.log 2>&1
```

Cron does not load the cPanel app's environment variables, so `GI_DATA_DIR`
has to be set here too — without it the run reads the app directory and finds
no customers.

## Restoring an archive

```bash
tar -xzf ~/gi-archives/gi-<name>-<stamp>.tar.gz -C ~/gi-data
```

The tarball holds the data directory under its original name, so unpack it
next to the others and either rename it or point `GI_DATA_DIR` at it.
