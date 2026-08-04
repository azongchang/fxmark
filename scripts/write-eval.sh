#!/bin/bash
SOURCE=${BASH_SOURCE[0]}
while [ -L "$SOURCE" ]; do
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/$SOURCE"
done
SCRIPT_DIR="$(cd -- "$(dirname -- "$SOURCE")" && pwd -P)"
SSRFS_ROOT="$SCRIPT_DIR/../../../ssrfs-kernel"
FXMARK_ROOT="$SCRIPT_DIR/.."

$SSRFS_ROOT/setup.sh -d
$FXMARK_ROOT/bin/run-fxmark.py --config $FXMARK_ROOT/workloads/write-eval.json

$SSRFS_ROOT/setup.sh -e
$FXMARK_ROOT/bin/run-fxmark.py --config $FXMARK_ROOT/workloads/write-eval.json
