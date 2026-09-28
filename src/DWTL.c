// SPDX-License-Identifier: MIT
/**
 * Microbenchmark
 *   FC. PROCESS = {create/delete files in 4KB at /test}
 *       - TEST: inode alloc/dealloc, block alloc/dealloc,
 *	        dentry insert/delete, block map insert/delete
 */
#define __USE_LARGEFILE64
#define _LARGEFILE_SOURCE
#define _LARGEFILE64_SOURCE

#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <signal.h>
#include <stdlib.h>
#define __STDC_FORMAT_MACROS
#include <inttypes.h>
#include <stdlib.h>
#include "fxmark.h"
#include "util.h"
#include "rdtsc.h"

static volatile sig_atomic_t stop_pre_work;
static uint64_t prepared_target_pages;

static void sighandler(int x)
{
    stop_pre_work = 1;
}

static void set_test_file(struct worker *worker,
                          char *test_file)
{
    struct fx_opt *fx_opt = fx_opt_worker(worker);
    sprintf(test_file, "%s/u_file_tr-%d.dat",
            fx_opt->root, worker->id);
}

static int pre_work(struct worker *worker)
{
    struct bench *bench =  worker->bench;
    char path[PATH_MAX];
    int fd=-1, rc = 0;
    char *page = NULL;
    int mem_rc;

    stop_pre_work = 0;
    {
      const char *target = getenv("FXMARK_DWTL_PAGES");

      prepared_target_pages = target ? strtoull(target, NULL, 10) : 0;
    }
    if (signal(SIGALRM, sighandler) == SIG_ERR) {
      rc = errno;
      goto err_out;
    }
    if (!prepared_target_pages)
      alarm(bench->duration * 3);

    /* allocate data buffer aligned with pagesize*/                    
    mem_rc = posix_memalign((void **)&(worker->page), PAGE_SIZE, PAGE_SIZE);
    if(mem_rc) {
      rc = mem_rc;
      goto err_out;
    }
    page = worker->page;                                               
    if (!page) {
      rc = ENOMEM;
      goto err_out;                                                    
    }

    /* time to create large file */
    set_test_file(worker, path);
    if ((fd = open(path, O_CREAT | O_RDWR | O_LARGEFILE, S_IRWXU)) == -1) {
      rc = errno;
      goto err_out;
    }

    /*set flag with O_DIRECT if necessary*/                   
    if(bench->directio && (fcntl(fd, F_SETFL, O_DIRECT)==-1)) {
      rc = errno;
      goto err_out;                                           
    }

    /* Serialized init gives each worker the full 21s write window, so
     * the aggregate file size would fill the device long before the
     * last worker (the master) runs: its open() then fails ENOSPC and
     * the whole case reports zero works.  Cap the file at 4GB (1M
     * pages) per worker — 48 workers x 4GB = 192GB, well under the
     * 372G SSD — while still giving ftruncate() plenty of shrink
     * steps for the 7s measure window. */
    for(; !stop_pre_work && worker->private[0] <
             (prepared_target_pages && prepared_target_pages < (1ULL << 20) ?
              prepared_target_pages : (1ULL << 20));
        ++worker->private[0]) {
      rc = write(fd, page, PAGE_SIZE);
      if (rc != PAGE_SIZE) {
        /* The pre_work alarm fires mid-write: the interrupted write
         * returns EINTR.  That is the designed end of pre_work, not an
         * error — treat it like the ENOSPC stop below. */
        if (rc < 0 && errno == EINTR && stop_pre_work) {
          rc = 0;
          goto out;
        }
        if (rc < 0 && errno == ENOSPC) {
          if (prepared_target_pages) {
            rc = ENOSPC;
            goto err_out;
          }
          /* The loop counter is the number of completed pages.  Do not
           * decrement it: an ENOSPC write did not advance the counter,
           * and decrementing from zero wraps the uint64_t value. */
          rc = 0;
          goto out;
        }
        if (rc >= 0)
          rc = EIO;
        else
          rc = errno;
        goto err_out;
      }
    }
    rc = 0;
    goto out;
err_out:
    bench->stop = 1;
 out:
    alarm(0);
    /*put fd to worker's private*/
    worker->private[1] = (uint64_t)fd;
    worker->private[2] = worker->private[0];
    free(page);
    worker->page=NULL;
    return rc;
}

static void dwtl_report_bench(struct bench *bench, FILE *out)
{
    uint64_t prepared = 0, truncated = 0, clear_us = 0, total_us = 0;
    int i, n_fg = bench->ncpu - bench->nbg, all_done = 1;

    for (i = 0; i < bench->ncpu; i++) {
        struct worker *w = &bench->workers[i];

        if (w->is_bg)
            continue;
        prepared += w->private[2];
        truncated += (uint64_t)w->works;
        total_us += w->usecs;
        if (w->usecs > clear_us)
            clear_us = w->usecs;
        if (!w->work_done)
            all_done = 0;
    }
    fprintf(out, "# ncpu secs works works/sec \n");
    fprintf(out, "%d %f %f %f \n", n_fg,
            n_fg ? (double)total_us / n_fg / 1000000.0 : 0.0,
            (double)truncated,
            total_us ? (double)truncated * n_fg * 1000000.0 / total_us : 0.0);
    fprintf(out,
            "# DWTL_DRAIN prepared_pages=%llu truncated=%llu all_done=%d clear_secs=%.6f\n",
            (unsigned long long)prepared, (unsigned long long)truncated,
            all_done, (double)clear_us / 1000000.0);
}
#include <string.h>

static int main_work(struct worker *worker)
{
    struct bench *bench = worker->bench;
    uint64_t iter = 0;
    int fd, rc = 0;
    char path[PATH_MAX];
    set_test_file(worker, path);

    /*get file */
    fd = (int)worker->private[1];

    if (!worker->private[0])
      goto out;

    for (iter = --worker->private[0]; iter > 0 && !bench->stop; --iter) {
      if (ftruncate(fd, iter * PAGE_SIZE) == -1) {
        rc = errno;
        goto err_out;
      }
    }
 out:
    close(fd);
    worker->works = (double)(worker->private[0] - iter);
    if (!rc && iter == 0 && !bench->stop)
      worker->work_done = 1;
    return rc;
 err_out:
    bench->stop = 1;
    rc = errno;
    goto out;
}

struct bench_operations u_file_tr_ops = {
    .parallel_pre_work = 1,
    .pre_work  = pre_work,
    .main_work = main_work,
    .report_bench = dwtl_report_bench,
};
