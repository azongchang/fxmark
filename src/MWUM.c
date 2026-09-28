// SPDX-License-Identifier: MIT
/**
 * Microbenchmark
 *   FC. PROCESS = {create/delete files in 4KB at /test}
 *       - TEST: inode alloc/dealloc, block alloc/dealloc,
 *	        dentry insert/delete, block map insert/delete
 */
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <signal.h>
#include <stdlib.h>
#define __STDC_FORMAT_MACROS
#include <inttypes.h>
#include "fxmark.h"
#include "util.h"
#include "rdtsc.h"

static volatile sig_atomic_t stop_pre_work;
static uint64_t prepared_target;

static void sighandler(int x)
{
	stop_pre_work = 1;
}

static void set_test_file(struct worker *worker, uint64_t file_id,
			  char *test_file)
{
	struct fx_opt *fx_opt = fx_opt_worker(worker);
	sprintf(test_file, "%s/u_sh_file_rm-%d-%" PRIu64 ".dat", fx_opt->root,
		worker->id, file_id);
}

static int pre_work(struct worker *worker)
{
	struct bench *bench = worker->bench;
	char path[PATH_MAX];
	int fd, rc = 0;

	stop_pre_work = 0;
	{
		const char *target = getenv("FXMARK_MWUM_FILES");

		prepared_target = target ? strtoull(target, NULL, 10) : 0;
	}
	if (signal(SIGALRM, sighandler) == SIG_ERR) {
		rc = errno;
		goto err_out;
	}
	if (!prepared_target)
		alarm(bench->duration * 3);

	for (; !stop_pre_work; ++worker->private[0]) {
		if (prepared_target && worker->private[0] >= prepared_target)
			break;
		set_test_file(worker, worker->private[0], path);
		if ((fd = open(path, O_CREAT | O_RDWR, S_IRWXU)) == -1) {
			if (errno == ENOSPC) {
				if (!prepared_target)
					goto out;
				rc = ENOSPC;
				goto err_out;
			}
			rc = errno;
			goto err_out;
		}
		close(fd);
	}
	goto out;
err_out:
	bench->stop = 1;
out:
	alarm(0);
	return rc;
}

/* Finite shared-directory deletion uses the slowest worker's clear time. */
static void mwum_report_bench(struct bench *bench, FILE *out)
{
	uint64_t prepared = 0, unlinked = 0, clear_us = 0, total_us = 0;
	int i, n_fg = bench->ncpu - bench->nbg, all_done = 1;

	for (i = 0; i < bench->ncpu; i++) {
		struct worker *w = &bench->workers[i];

		if (w->is_bg)
			continue;
		prepared += w->private[0];
		unlinked += (uint64_t)w->works;
		total_us += w->usecs;
		if (w->usecs > clear_us)
			clear_us = w->usecs;
		if (!w->work_done)
			all_done = 0;
	}
	fprintf(out, "# ncpu secs works works/sec \n");
	fprintf(out, "%d %f %f %f \n", n_fg,
		n_fg ? (double)total_us / n_fg / 1000000.0 : 0.0,
		(double)unlinked,
		total_us ? (double)unlinked * n_fg * 1000000.0 / total_us : 0.0);
	fprintf(out,
		"# MWUM_DRAIN prepared=%llu unlinked=%llu all_done=%d clear_secs=%.6f\n",
		(unsigned long long)prepared, (unsigned long long)unlinked,
		all_done, (double)clear_us / 1000000.0);
}

static int main_work(struct worker *worker)
{
	struct bench *bench = worker->bench;
	uint64_t iter;
	int rc = 0;
	for (iter = 0; iter < worker->private[0] && !bench->stop; ++iter) {
		char file[PATH_MAX];
		set_test_file(worker, iter, file);
        if (unlink(file))
			goto err_out;
	}
out:
	worker->works = (double)iter;
	if (!rc && iter >= worker->private[0] && !bench->stop)
		worker->work_done = 1;
	return rc;
err_out:
	bench->stop = 1;
	rc = errno;
	goto out;
}

struct bench_operations u_sh_file_rm_ops = {
	.parallel_pre_work = 1,
	.pre_work = pre_work,
	.main_work = main_work,
	.report_bench = mwum_report_bench,
};
