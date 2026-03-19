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

#define MWUM_TOTAL_FILES 2000000ULL

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
	const uint64_t worker_idx = (uint64_t)(worker - bench->workers);
	const uint64_t base_num_files = MWUM_TOTAL_FILES / bench->ncpu;
	const uint64_t extra_num_files = worker_idx < (MWUM_TOTAL_FILES % bench->ncpu);
	const uint64_t num_files = base_num_files + extra_num_files;
	char path[PATH_MAX];
	int fd, rc = 0;

	/* time to create files */
	for (; worker->private[0] < num_files; ++worker->private[0]) {
		set_test_file(worker, worker->private[0], path);
		if ((fd = open(path, O_CREAT | O_RDWR, S_IRWXU)) == -1) {
			if (errno == ENOSPC) {
				rc = 0;
				goto out;
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
	return rc;
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
	return rc;
err_out:
	bench->stop = 1;
	rc = errno;
	goto out;
}

struct bench_operations u_sh_file_rm_ops = {
	.pre_work = pre_work,
	.main_work = main_work,
};
