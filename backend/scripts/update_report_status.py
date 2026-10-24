import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cyber_reports import REPORT_STATUSES, update_report_status


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manually update a locally stored VoiceShield complaint status."
    )
    parser.add_argument("reference", help="Complaint reference shown when the report was saved")
    parser.add_argument("status", choices=REPORT_STATUSES)
    args = parser.parse_args()
    if not update_report_status(args.reference, args.status):
        parser.error("No report with that reference exists.")
    print(f"{args.reference} status updated to {args.status}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
