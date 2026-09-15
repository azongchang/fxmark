// SPDX-License-Identifier: MIT
/**
 * Nanobenchmark: Read operation
 *   RD. PROCESS = {read entries of the shared directory}
 */	      
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <dirent.h>
#include <unistd.h>
#include <errno.h>
#include <stdlib.h>
#define __STDC_FORMAT_MACROS
#include <inttypes.h>
#include "fxmark.h"
#include "util.h"

static void set_test_root(struct worker *worker, char *test_root)
{
	struct fx_opt *fx_opt = fx_opt_worker(worker);
	sprintf(test_root, "%s", fx_opt->root);
}

static void set_test_file(struct worker *worker, 
			  uint64_t file_id, char *test_file)
{
	struct fx_opt *fx_opt = fx_opt_worker(worker);
	sprintf(test_file, "%s/n_shdir_rd-%" PRIu64 ".dat",
		fx_opt->root, file_id);
}

static int pre_work(struct worker *worker)
{
	struct bench *bench = worker->bench;
	char path[PATH_MAX];
	int fd, rc;
	uint64_t total = 32768, id;
	unsigned int index = worker - bench->workers;
	const char *value = getenv("FXMARK_MRDM_FILES");
	char *end;

	/* Fixed shared directory, independent of worker count and FS speed. */
	if (value) {
		if (*value < '0' || *value > '9')
			return EINVAL;
		errno = 0;
		total = strtoull(value, &end, 10);
		if (errno || *end || !total || total > INT64_MAX)
			return EINVAL;
	}
	if (!index)
		printf("# PREPARED_TARGET MRDM files=%" PRIu64 "\n", total);

	/* create private directory */
	set_test_root(worker, path);
	rc = mkdir_p(path);
	if (rc) return rc;

	/* create files at the private directory */
	for (id = index; id < total; id += bench->ncpu) {
		set_test_file(worker, id, path);
		fd = open(path, O_CREAT | O_EXCL | O_RDWR, S_IRWXU);
		if (fd == -1)
			return errno;
		if (close(fd))
			return errno;
		++worker->private[0];
	}
	return 0;
}

static int main_work(struct worker *worker)
{
	struct bench *bench = worker->bench;
	char dir_path[PATH_MAX];
	DIR *dir;
	struct dirent *entry;
	uint64_t iter = 0;
	int rc = 0;

	set_test_root(worker, dir_path);
	while (!bench->stop) {
		dir = opendir(dir_path);
		if (!dir) {
			rc = errno;
			break;
		}
		while (!bench->stop) {
			errno = 0;
			entry = readdir(dir);
			if (!entry) {
				rc = errno;
				break;
			}
			++iter;
		}
		if (closedir(dir) && !rc)
			rc = errno;
		if (rc)
			break;
	}
	bench->stop = 1;
	worker->works = (double)iter;
	return rc;
}

struct bench_operations n_shdir_rd_ops = {
	/* Each worker prepares a disjoint partition of the fixed name set. */
	.parallel_pre_work = 1,
	.pre_work  = pre_work, 
	.main_work = main_work,
};
