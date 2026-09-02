// SPDX-License-Identifier: MIT
#include <sys/time.h>
#include <sched.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

#include <execinfo.h>
#include <sys/wait.h>

#include "bench.h"
#include "cpupol.h"
#include "rdtsc.h"

static struct bench *running_bench;

static uint64_t usec(void)
{
        struct timeval tv;
        gettimeofday(&tv, 0);
        return (uint64_t)tv.tv_sec * 1000000 + tv.tv_usec;
}

static inline void nop_pause(void)
{
        __asm __volatile("pause");
}

static inline void wmb(void)
{
        __asm__ __volatile__("sfence":::"memory");
}

/*
 * Ensure forked workers terminate if the coordinating parent dies.
 * The double-ppid check closes the race where parent exits between fork()
 * and PR_SET_PDEATHSIG setup in the child.
 */
static void arm_parent_death_signal_or_exit(void)
{
        pid_t parent = getppid();

        if (prctl(PR_SET_PDEATHSIG, SIGTERM) != 0)
                return;

        if (getppid() != parent)
                _exit(1);
}

static int setaffinity(int c)
{
        cpu_set_t cpuset;
        CPU_ZERO(&cpuset);
        CPU_SET(c, &cpuset);
        return sched_setaffinity(0, sizeof(cpuset), &cpuset);
}

struct bench *alloc_bench(int ncpu, int nbg)
{
        struct bench *bench; 
        struct worker *worker;
        void *shmem;
        int shmem_size = sizeof(*bench) + sizeof(*worker) * ncpu;
        int i;
        
        /* alloc shared memory using mmap */
        shmem = mmap(0, shmem_size, PROT_READ | PROT_WRITE, 
                     MAP_SHARED | MAP_ANONYMOUS, -1, 0);
        if (shmem == MAP_FAILED)
                return NULL;
        memset(shmem, 0, shmem_size);

        /* init. */ 
        bench = (struct bench *)shmem;
        bench->ncpu = ncpu; 
        bench->nbg  = nbg;
        bench->workers = (struct worker*)(shmem + sizeof(*bench));
        for (i = 0; i < ncpu; ++i) {
                worker = &bench->workers[i];
                worker->bench = bench;
                worker->id = seq_cores[i];
		worker->is_bg = i >= (ncpu - nbg);
        }

        return bench;
}

static void sighandler(int x)
{
        running_bench->stop = 1;
}

#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-result"

static void print_backtrace(void)
{
	void *buffer[128];
	int nptrs = backtrace(buffer, 128);

	fprintf(stderr,
		"# ERROR: worker init failed -- backtrace (%d frames):\n",
		nptrs);
	backtrace_symbols_fd(buffer, nptrs, STDERR_FILENO);
}

static void worker_main(void *arg)
{
	struct worker *worker = (struct worker*)arg;
	struct bench *bench = worker->bench;
	int worker_index = worker - bench->workers;
	uint64_t s_clk = 1, s_us = 1;
	uint64_t e_clk = 1, e_us = 1;
	int err = 0;

	/* set affinity */
	setaffinity(worker->id);

	/* pre-work */
	if (bench->ops.pre_work) {
		err = bench->ops.pre_work(worker);
		if (err)
			print_backtrace();
	}

	/*
	 * Signal init completion BEFORE jumping to err_out.
	 * This is the core deadlock fix: even on pre_work failure, the parent
	 * sees ready == 1 and can check ret rather than spinning forever.
	 * ret is written before ready (ordering enforced by wmb) so the parent
	 * sees a consistent snapshot.
	 */
	worker->ret = err;
	wmb();
	worker->ready = 1;

	/* skip barrier and main_work when init failed */
	if (err) {
		/*
		 * Worker 0 runs in the master process.  If its init fails
		 * it must still release the sibling workers from the start
		 * spin: without this, run_bench spins in wait_workers while
		 * every child spins on !bench->start forever (observed:
		 * DWTL hanging 40+ min with the master in wait_workers and
		 * the children in the bench.c:150 spin — indistinguishable
		 * from a kernel hang, and no init-failure message is ever
		 * printed for worker 0).  The children then run main_work
		 * against an unprepared tree and the case fails with the
		 * real error visible in worker 0's ret.
		 */
		if (!worker_index) {
			fprintf(stderr,
				"# ERROR: worker 0 init failed: ret=%d (%s)\n",
				err, strerror(err));
			bench->start = 1;
			wmb();
		}
		worker->clocks = 1;
		return;
	}

	/* wait for start signal */
	if (worker_index) {
		while (!bench->start)
			nop_pause();
	} else {
		/* are all workers ready? */
		int i;
		for (i = 1; i < bench->ncpu; i++) {
			struct worker *w = &bench->workers[i];
			while (!w->ready)
				nop_pause();
		}
		/*
		 * run-fxmark already syncs and drops caches before invoking
		 * fxmark. Avoid a global sync() here to prevent long stalls.
		 */

		/* start performance profiling */
		if (bench->profile_start_cmd[0])
			system(bench->profile_start_cmd);

		/* ok, before running, set timer */
		if (signal(SIGALRM, sighandler) == SIG_ERR) {
			err = errno;
			goto err_out;
		}
		running_bench = bench;
		alarm(bench->duration);
		bench->start = 1;
		wmb();
	}

	/* start time */
	s_clk = rdtsc_beg();
	s_us = usec();

	/* main work */
	if (bench->ops.main_work) {
		err = bench->ops.main_work(worker);
		if (err && err != ENOSPC)
			goto err_out;
	}

	/* end time */
	e_clk = rdtsc_end();
	e_us = usec();

	/* stop performance profiling */
	if (!worker_index && bench->profile_stop_cmd[0])
		system(bench->profile_stop_cmd);

	/* post-work */
	if (bench->ops.post_work)
		err = bench->ops.post_work(worker);
err_out:
	worker->ret = err;
	worker->usecs = e_us - s_us;
	wmb();
	worker->clocks = e_clk - s_clk;
}

static void wait_workers(struct bench *bench)
{
	int i;

	for (i = 0; i < bench->ncpu; i++) {
		struct worker *w = &bench->workers[i];
		while (!w->clocks)
			nop_pause();
	}
}

void run_bench(struct bench *bench)
{
	pid_t children[bench->ncpu];
	int i;

	for (i = 0; i < bench->ncpu; i++)
		children[i] = -1;

	/*
	 * Fork and initialize workers one at a time (serialized init).
	 * This avoids the fork storm that caused transient system()
	 * failures in mkdir_p, and allows individual init-failure
	 * reporting with backtraces.
	 */
	for (i = 1; i < bench->ncpu; ++i) {
		struct worker *w = &bench->workers[i];
		pid_t p;

		p = fork();
		if (p < 0) {
			w->ret = errno;
			fprintf(stderr,
				"# ERROR: fork failed for worker %d: %s\n",
				i, strerror(errno));
			continue;
		}

		if (!p) {
			/* child */
			arm_parent_death_signal_or_exit();
			worker_main(w);
			exit(0);
		}

		/* parent: record PID and wait for init to complete */
		children[i] = p;

		{
			int timeout_ms = 30000;
			int crashed = 0;

			while (timeout_ms > 0 && !w->ready) {
				int status;
				pid_t ret = waitpid(p, &status, WNOHANG);

				if (ret == p) {
					/* child terminated during init */
					if (WIFEXITED(status))
						fprintf(stderr,
							"# ERROR: worker %d "
							"exited with status %d "
							"during init\n",
							i, WEXITSTATUS(status));
					else if (WIFSIGNALED(status))
						fprintf(stderr,
							"# ERROR: worker %d "
							"killed by signal %d "
							"during init\n",
							i, WTERMSIG(status));
					w->ret = -ECHILD;
					w->clocks = 1;
					crashed = 1;
					break;
				} else if (ret < 0) {
					/* unexpected waitpid error */
					break;
				}
				/* child still alive, keep polling */
				usleep(10000); /* 10 ms */
				timeout_ms -= 10;
			}

			if (!crashed && !w->ready) {
				fprintf(stderr,
					"# ERROR: worker %d init timed out "
					"after 30s\n", i);
				kill(p, SIGKILL);
				waitpid(p, NULL, 0);
				w->ret = -ETIMEDOUT;
				w->clocks = 1;
				children[i] = -1; /* already reaped */
			}
		}

		/* Report init result */
		if (w->ret) {
			fprintf(stderr,
				"# ERROR: worker %d init failed: ret=%d (%s)\n",
				i, w->ret, strerror(abs(w->ret)));
		}
	}

	/* Run worker 0 in the parent process */
	worker_main(&bench->workers[0]);

	/* Wait for all workers to complete main_work (spin on clocks) */
	wait_workers(bench);

	/* Reap remaining child processes */
	for (i = 1; i < bench->ncpu; ++i) {
		if (children[i] > 0) {
			int status;
			pid_t ret;

			do {
				ret = waitpid(children[i], &status, 0);
			} while (ret < 0 && errno == EINTR);
		}
	}
}

void report_bench(struct bench *bench, FILE *out)
{
	static char *empty_str = "";
        uint64_t total_usecs = 0;
        double   total_works = 0.0;
        double   avg_secs;
	char *profile_name, *profile_data;
        int i, n_fg_cpu;

        for (i = 0; i < bench->ncpu; ++i) {
                struct worker *w = &bench->workers[i];

                if (!w->ret || w->ret == ENOSPC)
                        continue;
                fprintf(out, "# ERROR worker=%d cpu=%d ret=%d (%s)\n",
                        i, w->id, w->ret, strerror(w->ret));
                return;
        }

        /* if report_bench is overloaded */ 
        if (bench->ops.report_bench) {
                bench->ops.report_bench(bench, out);
                return;
        }

        /* default report_bench impl. */
        for (i = 0; i < bench->ncpu; ++i) {
                struct worker *w = &bench->workers[i];
		if (w->is_bg) continue;
                total_usecs += w->usecs;
                total_works += w->works;
        }
	n_fg_cpu = bench->ncpu - bench->nbg;
        avg_secs = (double)total_usecs/(double)n_fg_cpu/1000000.0;

	/* get profiling result */ 
	profile_name = profile_data = empty_str;
	if (bench->profile_stat_file[0]) {
		FILE *fp = fopen(bench->profile_stat_file, "r");
		size_t len;
		
		if (fp) {
			profile_name = profile_data = NULL;
			getline(&profile_name, &len, fp);
			getline(&profile_data, &len, fp);
			fclose(fp);
		}
	}

        fprintf(out, "# ncpu secs works works/sec %s\n", profile_name);
        fprintf(out, "%d %f %f %f %s\n", 
                n_fg_cpu, avg_secs, total_works, total_works/avg_secs, profile_data);

	if (profile_name != empty_str)
		free(profile_name);
	if (profile_data != empty_str)
		free(profile_data);
}

int bench_error(struct bench *bench)
{
        int i;

        for (i = 0; i < bench->ncpu; ++i) {
                struct worker *w = &bench->workers[i];

                if (w->ret && w->ret != ENOSPC)
                        return w->ret;
        }
        return 0;
}

#pragma GCC diagnostic pop
