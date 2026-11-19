"""Read-only audit by default; --apply installs the versioned MongoDB schema."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.database_schema import apply_schema, audit_database, ensure_indexes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Install validators and indexes after a clean audit")
    mode.add_argument("--indexes-only", action="store_true", help="Install indexes without changing collection validators")
    args = parser.parse_args()
    load_dotenv(Path(__file__).parent / ".env")
    uri = os.getenv("MONGODB_URI")
    if not uri:
        parser.error("Set MONGODB_URI in backend/.env")
    try:
        with MongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True) as client:
            database = client[os.getenv("MONGODB_DB_NAME", "mediconnect")]
            report = apply_schema(database) if args.apply else audit_database(database)
            if args.indexes_only and not report["issues"]:
                ensure_indexes(database)
            print(json.dumps(report, indent=2))
            if args.apply:
                print("Schema applied.")
            elif args.indexes_only:
                print("Indexes installed; collection validators unchanged." if not report["issues"] else "Indexes not installed; fix audit findings first.")
            else:
                print("Read-only audit complete; no changes made.")
            return 1 if report["issues"] else 0
    except ValueError as exc:
        print(str(exc))
        return 1
    except PyMongoError as exc:
        if "collMod" in str(exc) and ("not allowed" in str(exc) or "not authorized" in str(exc)):
            print("Validator setup requires collMod permission. Run --apply using a setup account with "
                  "dbAdmin on the target database in addition to readWrite. The app's normal user can remain readWrite.")
            return 1
        # Connection errors can embed hostnames; never print credentials or document contents.
        print(f"Database operation failed ({type(exc).__name__}, code={getattr(exc, 'code', None)}). "
              "Check connectivity and MongoDB schema-management permissions. Setup can be rerun safely.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
