// SPDX-License-Identifier: MIT
/**
 * Nanobenchmark: mknod load (shared root)
 *   Each worker creates special files at the test root.
 */
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#define __STDC_FORMAT_MACROS
#include <inttypes.h>
#include "fxmark.h"
#include "util.h"

static int pre_work(struct worker *worker)
{
	struct fx_opt *fx_opt = fx_opt_worker(worker);
	return mkdir_p(fx_opt->root);
}

static int main_work(struct worker *worker)
{
	struct bench *bench = worker->bench;
	struct fx_opt *fx_opt = fx_opt_bench(bench);
	uint64_t iter;
	int rc = 0;

	for (iter = 0; !bench->stop; ++iter) {
		char path[PATH_MAX];
		snprintf(path, PATH_MAX, "%s/u_mknod-%d-%" PRIu64, fx_opt->root,
			 worker->id, iter);
		if (mknod(path, S_IFREG | S_IRWXU, 0))
			goto err_out;
	}
out:
	worker->works = (double)iter;
	return rc;
err_out:
	bench->stop = 1;
	rc = errno;
	goto out;
}

struct bench_operations u_mknod_ops = {
	.pre_work  = pre_work,
	.main_work = main_work,
};

