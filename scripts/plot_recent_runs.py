#!/usr/bin/env python3
import argparse
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOGS_DIR = ROOT_DIR / "logs"
DEFAULT_PLOTTER = ROOT_DIR / "bin" / "plotter.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Combine recent fxmark logs and plot them together."
    )
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        required=True,
        help="Number of recent executions to include.",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=DEFAULT_LOGS_DIR,
        help=f"Directory containing fxmark run outputs (default: {DEFAULT_LOGS_DIR}).",
    )
    parser.add_argument(
        "--plotter",
        type=Path,
        default=DEFAULT_PLOTTER,
        help=f"Path to plotter script (default: {DEFAULT_PLOTTER}).",
    )
    return parser.parse_args()


def collect_log_files(logs_dir: Path) -> List[Path]:
    if not logs_dir.is_dir():
        raise FileNotFoundError(f"Logs directory not found: {logs_dir}")

    log_files = []
    for entry in logs_dir.iterdir():
        log_file = entry / "fxmark.log"
        if log_file.is_file():
            log_files.append((log_file.stat().st_mtime, log_file))
    log_files.sort(key=lambda item: item[0], reverse=True)
    return [log for _, log in log_files]


def combine_logs(log_files: List[Path], destination: Path) -> None:
    with destination.open("w") as out:
        for log_file in log_files:
            content = log_file.read_text()
            out.write(content)
            if not content.endswith("\n"):
                out.write("\n")


def main() -> int:
    args = parse_args()
    if args.count <= 0:
        print("Count must be a positive integer.", file=sys.stderr)
        return 1

    logs_dir = args.logs_dir.resolve()
    plotter = args.plotter.resolve()

    try:
        log_files = collect_log_files(logs_dir)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    if not plotter.is_file():
        print(f"Plotter not found: {plotter}", file=sys.stderr)
        return 1

    if len(log_files) < args.count:
        print(
            f"Only found {len(log_files)} fxmark.log files under {logs_dir}, "
            f"but {args.count} were requested.",
            file=sys.stderr,
        )
        return 1

    selected_logs = log_files[: args.count]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT_DIR / "plot" / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    combined_log = out_dir / "combined_fxmark.log"
    combine_logs(selected_logs, combined_log)

    cmd = [
        str(plotter),
        "--ty",
        "sc",
        "--log",
        str(combined_log),
        "--out",
        str(out_dir),
    ]
    subprocess.run(cmd, check=True)

    print(f"Combined {len(selected_logs)} logs into {combined_log}")
    print(f"Plot output directory: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
