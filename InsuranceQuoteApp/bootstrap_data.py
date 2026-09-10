#!/usr/bin/env python
"""Create an environment's data directory.

Production starts empty; dev starts with the repo's seed data. Run once per
environment, before the first request:

    GI_DATA_DIR=~/gi-data/prod python bootstrap_data.py
    GI_DATA_DIR=~/gi-data/dev  python bootstrap_data.py --seed

--seed copies seed/ (the customer list as it stood when the environments were
split) into the data directory. It never overwrites a file that is already
there, so re-running it cannot destroy a data set someone has been working in;
pass --force if replacing them is the actual intent.

The app creates its own users.json, databases and upload directories on first
use, so this script only has to make the directory and, for dev, plant the
starting files.
"""

import argparse
import shutil
import sys
from pathlib import Path

import paths

SEED_DIR = paths.BASE_DIR / "seed"


def seed_files():
        if not SEED_DIR.is_dir():
                return []
        return sorted(path for path in SEED_DIR.iterdir() if path.is_file())


def main(argv=None):
        parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
        parser.add_argument("--seed", action="store_true",
                            help="copy seed/ into the data directory (dev)")
        parser.add_argument("--force", action="store_true",
                            help="with --seed, overwrite files that already exist")
        args = parser.parse_args(argv)

        target = paths.ensure_data_dir()
        print(f"environment : {paths.ENV_NAME}")
        print(f"data dir    : {target}")

        if target == paths.BASE_DIR:
                print("\nGI_DATA_DIR is unset, so this would seed the app directory "
                      "itself.\nSet GI_DATA_DIR to a directory outside the checkout "
                      "and run again.", file=sys.stderr)
                return 1

        if not args.seed:
                print("\nEmpty data directory ready. The app creates users.json, "
                      "messages.db,\nreminders.db and the upload directories on "
                      "first use.")
                return 0

        files = seed_files()
        if not files:
                print(f"\nNothing to seed - {SEED_DIR} is empty or missing.",
                      file=sys.stderr)
                return 1

        copied, skipped = 0, 0
        for source in files:
                destination = target / source.name
                if destination.exists() and not args.force:
                        print(f"  kept     {source.name} (already present)")
                        skipped += 1
                        continue
                shutil.copy2(source, destination)
                print(f"  seeded   {source.name}")
                copied += 1

        print(f"\n{copied} file(s) seeded, {skipped} left alone.")
        return 0


if __name__ == "__main__":
        raise SystemExit(main())
