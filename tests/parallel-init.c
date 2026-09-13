// SPDX-License-Identifier: MIT
#include <assert.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/wait.h>
#include "bench.h"

static int mode, forks, clock_reads;
int __real_clock_gettime(clockid_t, struct timespec *);
int __wrap_clock_gettime(clockid_t id, struct timespec *ts)
{
	int rc = __real_clock_gettime(id, ts);
	/* Deterministically expire init without waiting for the real 33s limit. */
	if (mode == 6 && id == CLOCK_MONOTONIC && ++clock_reads > 2)
		ts->tv_sec += 100;
	return rc;
}
pid_t __real_fork(void);
pid_t __wrap_fork(void)
{
	if (mode == 4 && ++forks == 2) {
		errno = EAGAIN;
		return -1;
	}
	return __real_fork();
}
static int prepare(struct worker *w)
{
	int index = w - w->bench->workers;
	if ((mode == 1 && index == 0) || (mode == 2 && index == 2))
		return EIO;
	if (mode == 3 && index == 2)
		_exit(9);
	usleep(100000);
	w->private[0] = 1;
	return 0;
}
static int measure(struct worker *w)
{
	for (int i = 0; i < w->bench->ncpu; i++)
		assert(w->bench->workers[i].ready && w->bench->workers[i].private[0]);
	if (mode == 5 && w == &w->bench->workers[2])
		_exit(9);
	w->works = 1;
	return 0;
}
int main(int argc, char **argv)
{
	struct timespec a, b;
	assert(argc == 3);
	mode = atoi(argv[1]);
	setenv("FXMARK_PARALLEL_INIT", argv[2], 1);
	struct bench *bench = alloc_bench(4, 0);
	assert(bench);
	bench->duration = 1;
	bench->ops.parallel_pre_work = 1;
	bench->ops.pre_work = prepare;
	bench->ops.main_work = measure;
	clock_gettime(CLOCK_MONOTONIC, &a);
	run_bench(bench);
	alarm(0);
	clock_gettime(CLOCK_MONOTONIC, &b);
	assert(!!bench_error(bench) == !!mode);
	for (int i = 0; i < 4; i++) {
		if (!mode)
			assert(bench->workers[i].works == 1);
		else if (mode != 5)
			assert(bench->workers[i].works == 0);
	}
	assert(waitpid(-1, NULL, WNOHANG) == -1 && errno == ECHILD);
	printf("PASS mode=%d parallel=%s elapsed=%.3f\n", mode, argv[2],
	       b.tv_sec - a.tv_sec + (b.tv_nsec - a.tv_nsec) / 1e9);
}
