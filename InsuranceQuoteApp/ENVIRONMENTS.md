# Dev and production environments

Two independent instances of the same app on one cPanel account:

| | production | dev |
|---|---|---|
| URL | `app.gravityinsurance.in` | `dev.app.gravityinsurance.in` |
| app root | `~/public_html/app.gravityinsurance.in` | `~/public_html/dev.app.gravityinsurance.in` |
| virtualenv | `~/virtualenv/public_html/app.gravityinsurance.in/3.11` | `~/virtualenv/public_html/dev.app.gravityinsurance.in/3.11` |
| data dir | `~/gi-data/production` | `~/gi-data/dev` |
| default branch | `main` | `quote-automation` |
| data | starts empty | seeded with the customer list |
| reminder cron | yes | **no** |
| page banner | none | orange "DEV ENVIRONMENT" bar |

They share the code and the WhatsApp credentials. They share nothing else.

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

1. **Domains → Create a Domain**: `dev.app.gravityinsurance.in`, document root
   `public_html/dev.app.gravityinsurance.in`.
2. **Setup Python App**: Python 3.11, application root
   `public_html/dev.app.gravityinsurance.in`, application URL the new
   subdomain, startup file `app.py`, entry point `application`.
3. In that app's **Environment variables**, add:
   - `GI_DATA_DIR` = `/home/USER/gi-data/dev`
   - `GI_ENV_NAME` = `dev`
   - the same `GI_WA_*` and `GI_QUOTE_CONTACT` values as production.
4. Add the same variables to the **production** app, with
   `GI_DATA_DIR=/home/USER/gi-data/production` and `GI_ENV_NAME=production`.
5. Restart both apps.

The `.htaccess` rewrite override (`RewriteEngine On` / `RewriteRule ^ - [L]`)
is needed on the dev subdomain too — it sits inside `public_html` and inherits
the React site's rewrite exactly as production does. `deploy.sh` checks for it
and inserts it if missing.

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

Two consequences:

- **Never install the reminder cron on dev.** Two crons over overlapping
  customer lists means every customer gets messaged twice.
  `send_reminders.py` refuses to send from a non-production environment
  unless `--force` is passed; `--dry-run` always works.
- **Test with your own number.** Dev's seeded customer list contains real
  mobile numbers. Edit a row to your own number before sending anything.

If this becomes a problem, the fix is a Meta test number and a separate
`GI_ENV_FILE` for dev — no code change needed, just different `GI_WA_*`
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
