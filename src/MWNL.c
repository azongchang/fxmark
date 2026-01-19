// SPDX-License-Identifier: MIT
/**
 * Nanobenchmark: mknod load (private dir)
 *   Each worker creates special files under its private root.
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

static void set_test_root(struct worker *worker, char *test_root)
{
	struct fx_opt *fx_opt = fx_opt_worker(worker);
	sprintf(test_root, "%s/%d", fx_opt->root, worker->id);
}

static int pre_work(struct worker *worker)
{
	char test_root[PATH_MAX];
	set_test_root(worker, test_root);
	return mkdir_p(test_root);
}

static int main_work(struct worker *worker)
{
	char test_root[PATH_MAX];
	struct bench *bench = worker->bench;
	uint64_t iter;
	int rc = 0;

	set_test_root(worker, test_root);
	for (iter = 0; !bench->stop; ++iter) {
		char path[PATH_MAX];
		snprintf(path, PATH_MAX, "%s/n_mknod-%" PRIu64, test_root, iter);
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

struct bench_operations n_mknod_ops = {
	.pre_work  = pre_work,
	.main_work = main_work,
};

