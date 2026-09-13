# Parallel preparation

The Python runner enables `FXMARK_PARALLEL_INIT=1` by default and forwards it
explicitly through sudo. Set `FXMARK_PARALLEL_INIT=0` for the legacy serialized
preparation schedule. Direct `bin/fxmark` calls remain serial unless opted in.

Enabled workloads:

- MWUL and MWRM: separate per-worker directories and files.
- DWTL: separate per-worker files; the existing 4 GiB per-worker cap remains.
- MRPM and MRPH: partition the 64 depth-two subtrees among worker indexes;
  the final 32,768-file namespace is unchanged, including at nonidentity CPU
  mappings and when some workers receive no subtree.

Other workloads, including shared-file and background-worker variants, retain
their existing initialization schedule. Each parallel initializer runs in its
own process so SIGALRM state and file descriptors remain private. A coordinator
starts the measurement alarm only after all workers report successful init.
Failures abort without entering measured work; children are reaped. Init uses
a shared deadline of 3 * duration + 30 seconds, inside the runner case timeout.

Ready workers sleep instead of spinning. mkdir_p uses mkdir/stat directly,
eliminating thousands of shell/mkdir subprocesses during tree construction.
Disabled profiling does not launch profiler start/stop subprocesses. Command
output spools to a temporary file to avoid deadlocks on full stdout/stderr pipes.
Case logs record setup, init+run, and strict teardown elapsed seconds, plus
parallel init duration and the prepared private[0] counters (file/page counts
for MWUL/MWRM/DWTL; not a universal size metric).

Measurement duration, main_work implementations, sync/drop-cache policy,
format options, and blocking unmount remain unchanged. Cases sharing the same
device never overlap. Flush and unmount must complete before the next format;
reducing their genuine persistence work requires a separately validated kernel
change. There is no lazy detach, flush bypass, or reuse of a dirty filesystem.

Time-limited preparation can produce different file counts under concurrent
load. Use matching init mode for native/SSRFS comparisons and record the mode
and prepared counts. Results from the two init schedules are not interchangeable
performance baselines. Deletion/truncation may exhaust prepared work before
the timer: inspect reported seconds and counts rather than assuming a full
duration was measured. No adaptive duration reduction or hidden dataset cap is
introduced by this optimization.

Validation: `make` then `python3 tests/test-prepare.py` (no root/device needed).
Tests cover readiness, worker-0/child init errors, fork failure, child crashes,
reaping, exact tree equivalence, large subprocess output and timeout propagation.
