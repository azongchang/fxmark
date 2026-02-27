!#/bin/sh
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SSRFS_ROOT="$SCRIPT_DIR/../../ssrfs-kernel"
FXMARK_ROOT="$SCRIPT_DIR/.."

$SSRFS_ROOT/setup.sh -d all
$FXMARK_ROOT/bin/run-fxmark.py --config $FXMARK_ROOT/workloads/single-eval.json

$SSRFS_ROOT/setup.sh -e all
$FXMARK_ROOT/bin/run-fxmark.py --config $FXMARK_ROOT/workloads/single-eval.json
