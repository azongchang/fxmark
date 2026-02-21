#!/usr/bin/env python3
import argparse
import json
import os
import shlex
import sys
import signal
import subprocess
import datetime
import tempfile
import pdb
from os.path import join
from perfmon import PerfMon

CUR_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_CONFIG_PATH = os.path.join(CUR_DIR, "run-config.json")

try:
    import cpupol
except ImportError:
    print("No cpupolicy for this machine.")
    print("Do \'make\' at %s\n"
          % os.path.normpath(os.path.join(CUR_DIR, "..")))
    raise

def catch_ctrl_C(sig, frame):
    print("Umount a testing file system. Please wait.")

class Runner(object):

    # media path
    LOOPDEV = "/dev/loop0"
    NVMEDEV = "/dev/disk/by-id/nvme-INTEL_SSDPF21Q400GB_PHAL11310014400AGN"
    HDDDEV = "/dev/sdX"
    SSDDEV = "/dev/sdY"

    # test core granularity
    CORE_FINE_GRAIN   = 0
    CORE_COARSE_GRAIN = 1

    def __init__(self, \
                 run_filter = ("*", "*", "*", "*", "*"), \
                 disk_size = "32G", \
                 duration = 5):
        # run config
        self.CORE_GRAIN    = Runner.CORE_FINE_GRAIN
        self.PERFMON_LEVEL = PerfMon.LEVEL_LOW
        self.FILTER        = run_filter # media, fs, bench, ncore, directio
        self.DRYRUN        = False
        self.DEBUG_OUT     = False

        # bench config
        self.DISK_SIZE     = disk_size
        self.DURATION = duration  # seconds
        self.DIRECTIOS     = ["bufferedio", "directio"]  # enable directio except tmpfs -> nodirectio 
        self.MEDIA_TYPES = ["ssd", "hdd", "nvme", "mem"]
        self.FS_TYPES = [
            "tmpfs",
            "ext4",
            "ext4_no_jnl",
            "xfs",
            "btrfs",
            "f2fs",
            "f2fs_no_ck",
            "jfs",
            "reiserfs",
            "ext2",
            "ext3",
        ]
        self.BENCH_TYPES = [
            # write/write
            "DWAL",
            "DWOL",
            "DWOM",
            "DWSL",
            "MWRL",
            "MWRM",
            "MWRH",
            "MWCL",
            "MWCM",
            "MWUM",
            "MWUL",
            "DWTL",
            "MWDL",
            "MWDM",
            "MWNL",
            "MWNM",
            # # filebench
            "filebench_varmail",
            "filebench_oltp",
            "filebench_fileserver",
            # # dbench
            "dbench_client",
            # read/read
            "MRPL",
            "MRPM",
            "MRPH",
            "MRDM",
            "MRDL",
            "DRBH",
            "DRBM",
            "DRBL",
            # read/write
            "MRPM_bg",
            "DRBM_bg",
            "MRDM_bg",
            "DRBH_bg",
            "DRBL_bg",
            "MRDL_bg",
        ]
        self.BENCH_BG_SFX   = "_bg"

        # path config
        self.ROOT_NAME      = "root"
        self.LOGD_NAME      = "../logs"
        self.FXMARK_NAME    = "fxmark"
        self.FILEBENCH_NAME = "run-filebench.py"
        self.DBENCH_NAME    = "run-dbench.py"
        self.PERFMN_NAME    = "perfmon.py"

        # fs config
        self.HOWTO_MOUNT = {
            "tmpfs": self.mount_tmpfs,
            "ext2": self.mount_anyfs,
            "ext3": self.mount_anyfs,
            "ext4": self.mount_anyfs,
            "ext4_no_jnl": self.mount_ext4_no_jnl,
            "xfs": self.mount_anyfs,
            "btrfs": self.mount_anyfs,
            "f2fs": self.mount_anyfs,
            "f2fs_no_ck": self.mount_f2fs_no_ck,
            "jfs": self.mount_anyfs,
            "reiserfs": self.mount_anyfs,
        }
        self.HOWTO_MKFS = {
            "ext2": "-F",
            "ext3": "-F",
            "ext4": "-F -O large_dir,huge_file",
            "ext4_no_jnl": "-F",
            "f2fs": "-f",
            "f2fs_no_ck": "-f",
            "xfs": "-f",
            "btrfs": "-f",
            "jfs": "-q",
            "reiserfs": "-q",
        }

        # media config
        self.HOWTO_INIT_MEDIA = {
            "mem": self.init_mem_disk,
            "nvme": self.init_nvme_disk,
            "ssd": self.init_ssd_disk,
            "hdd": self.init_hdd_disk,
        }

        # misc. setup
        self.redirect    = subprocess.PIPE if not self.DEBUG_OUT else None
        self.dev_null    = open("/dev/null", "a") if not self.DEBUG_OUT else None
        self.npcpu       = cpupol.PHYSICAL_CHIPS * cpupol.CORE_PER_CHIP
        self.nhwthr      = self.npcpu * cpupol.SMT_LEVEL
        self.ncores      = self.get_ncores()
        self.test_root   = os.path.normpath(
            os.path.join(CUR_DIR, self.ROOT_NAME))
        self.fxmark_path = os.path.normpath(
            os.path.join(CUR_DIR, self.FXMARK_NAME))
        self.filebench_path = os.path.normpath(
            os.path.join(CUR_DIR, self.FILEBENCH_NAME))
        self.dbench_path = os.path.normpath(
            os.path.join(CUR_DIR, self.DBENCH_NAME))
        self.tmp_path = os.path.normpath(
            os.path.join(CUR_DIR, ".tmp"))
        self.disk_path = os.path.normpath(
            os.path.join(self.tmp_path, "disk.img"))
        self.perfmon_start = "%s start" % os.path.normpath(
            os.path.join(CUR_DIR, self.PERFMN_NAME))
        self.perfmon_stop = "%s stop" % os.path.normpath(
            os.path.join(CUR_DIR, self.PERFMN_NAME))
        self.perfmon_log = ""
        self.log_dir     = ""
        self.log_path    = ""
        self.umount_hook = []
        self.active_ncore = -1
        self.active_cpuset = None

    def log_start(self):
        log_subdir = getattr(Runner, "LOG_SUBDIR", None)
        if not log_subdir:
            log_subdir = str(datetime.datetime.now()).replace(' ','-').replace(':','-')
        self.log_dir = os.path.normpath(
            os.path.join(CUR_DIR, self.LOGD_NAME, log_subdir))
        self.log_path = os.path.normpath(os.path.join(self.log_dir, "fxmark.log"))
        self.exec_cmd("mkdir -p " + self.log_dir, self.dev_null)

        log_mode = "ab" if os.path.exists(self.log_path) else "bw"
        self.log_fd = open(self.log_path, log_mode)
        p = self.exec_cmd("echo -n \"### SYSTEM         = \"; uname -a", self.redirect)
        if self.redirect:
            for l in p.stdout.readlines():
                self.log(l.decode("utf-8").strip())
        self.log("### DISK_SIZE      = %s"   % self.DISK_SIZE)
        self.log("### DURATION       = %ss"  % self.DURATION)
        self.log("### DIRECTIO       = %s"   % ','.join(self.DIRECTIOS))
        self.log("### MEDIA_TYPES    = %s"   % ','.join(self.MEDIA_TYPES))
        self.log("### FS_TYPES       = %s"   % ','.join(self.FS_TYPES))
        self.log("### BENCH_TYPES    = %s"   % ','.join(self.BENCH_TYPES))
        self.log("### NCORES         = %s"   % 
                 ','.join(map(lambda c: str(c), self.ncores)))
        self.log("### CORE_SEQ       = %s" % 
                 ','.join(map(lambda c: str(c), cpupol.seq_cores)))
        self.log("\n")
        self.log("### MODEL_NAME     = %s" % cpupol.MODEL_NAME)
        self.log("### PHYSICAL_CHIPS = %s" % cpupol.PHYSICAL_CHIPS)
        self.log("### CORE_PER_CHIP  = %s" % cpupol.CORE_PER_CHIP)
        self.log("### SMT_LEVEL      = %s" % cpupol.SMT_LEVEL)
        self.log("\n")

    def log_end(self):
        self.log_fd.close()

    def log(self, log):
        self.log_fd.write((log+'\n').encode('utf-8'))
        print(log)

    def get_ncores(self):
        hw_thr_cnts_map = {
            Runner.CORE_FINE_GRAIN:cpupol.test_hw_thr_cnts_fine_grain,
            Runner.CORE_COARSE_GRAIN:cpupol.test_hw_thr_cnts_coarse_grain,
        }
        ncores = []
        test_hw_thr_cnts = hw_thr_cnts_map.get(self.CORE_GRAIN,
                                               cpupol.test_hw_thr_cnts_fine_grain)
        for n in test_hw_thr_cnts:
            if n > self.npcpu:
                break
            ncores.append(n)
        return ncores

    def bind_cmd(self, cmd):
        if not self.active_cpuset:
            return cmd
        return ' '.join(["taskset", "-c", self.active_cpuset, "sh", "-c", shlex.quote(cmd)])

    def exec_cmd(self, cmd, out=None, bind=False):
        if bind:
            cmd = self.bind_cmd(cmd)
        p = subprocess.Popen(cmd, shell=True, stdout=out, stderr=out)
        p.wait()
        return p

    def keep_sudo(self):
        self.exec_cmd("sudo -v", self.dev_null)

    def drop_caches(self):
        sync_cmd = "sync"
        if os.path.ismount(self.test_root):
            sync_cmd = ' '.join(["sudo", "sync", "-f", self.test_root])
        self.exec_cmd(sync_cmd, self.dev_null)

        cmd = ' '.join(["sudo",
                        os.path.normpath(
                            os.path.join(CUR_DIR, "drop-caches"))])
        self.exec_cmd(cmd, self.dev_null)

    def set_cpus(self, ncore):
        if self.active_ncore == ncore:
            return
        self.active_ncore = ncore
        if ncore == 0:
            cores = cpupol.seq_cores[:self.nhwthr]
        else:
            cores = cpupol.seq_cores[0:ncore]
        self.active_cpuset = ','.join(map(lambda c: str(c), cores))

    def add_bg_worker_if_needed(self, bench, ncore):
        if bench.endswith(self.BENCH_BG_SFX):
            ncore = min(ncore + 1, self.nhwthr)
            return (ncore, 1)
        return (ncore, 0)

    def prepre_work(self, ncore):
        self.keep_sudo()
        self.exec_cmd("sudo sh -c \"echo 0 >/proc/sys/kernel/lock_stat\"",
                      self.dev_null)
        self.set_cpus(ncore)

    def pre_work(self):
        self.keep_sudo()
        self.drop_caches()

    def post_work(self):
        self.keep_sudo()

    def unset_loopdev(self):
        self.exec_cmd(' '.join(["sudo", "losetup", "-d", Runner.LOOPDEV]),
                      self.dev_null)

    def umount(self, where):
        while True:
            p = self.exec_cmd("sudo umount " + where, self.dev_null)
            if p.returncode != 0:
                break
        (umount_hook, self.umount_hook) = (self.umount_hook, [])
        for hook in umount_hook:
            hook()

    def init_mem_disk(self):
        self.unset_loopdev()
        self.umount(self.tmp_path)
        self.unset_loopdev()
        self.exec_cmd("mkdir -p " + self.tmp_path, self.dev_null)
        if not self.mount_tmpfs("mem", "tmpfs", self.tmp_path):
            return False;
        self.exec_cmd("dd if=/dev/zero of=" 
                      + self.disk_path +  " bs=1G count=1024000",
                      self.dev_null)
        p = self.exec_cmd(' '.join(["sudo", "losetup",
                                    Runner.LOOPDEV, self.disk_path]), 
                          self.dev_null)
        if p.returncode == 0:
            self.umount_hook.append(self.deinit_mem_disk)
        return (p.returncode == 0, Runner.LOOPDEV)

    def deinit_mem_disk(self):
        self.unset_loopdev()
        self.umount(self.tmp_path)

    def init_nvme_disk(self):
        return (os.path.exists(Runner.NVMEDEV), Runner.NVMEDEV)

    def init_ssd_disk(self):
        return (os.path.exists(Runner.SSDDEV), Runner.SSDDEV)

    def init_hdd_disk(self):
        return (os.path.exists(Runner.HDDDEV), Runner.HDDDEV)

    def init_media(self, media):
        _init_media = self.HOWTO_INIT_MEDIA.get(media, None)
        if not _init_media:
            return (False, None)
        (rc, dev_path) = _init_media()
        return (rc, dev_path)

    def mount_tmpfs(self, media, fs, mnt_path):
        p = self.exec_cmd("sudo mount -t tmpfs -o mode=0777,size="
                          + self.DISK_SIZE + " none " + mnt_path,
                          self.dev_null)
        return p.returncode == 0

    def mount_anyfs(self, media, fs, mnt_path):
        (rc, dev_path) = self.init_media(media)
        if not rc:
            return False

        p = self.exec_cmd("sudo mkfs." + fs
                          + " " + self.HOWTO_MKFS.get(fs, "")
                          + " " + dev_path,
                          self.dev_null)
        if p.returncode != 0:
            return False
        p = self.exec_cmd(' '.join(["sudo mount -t", fs,
                                    dev_path, mnt_path]),
                          self.dev_null)
        if p.returncode != 0:
            return False
        p = self.exec_cmd("sudo chmod 777 " + mnt_path,
                          self.dev_null)
        if p.returncode != 0:
            return False
        return True

    def mount_f2fs_no_ck(self, media, fs, mnt_path):
        (rc, dev_path) = self.init_media(media)
        if not rc:
            return False

        p = self.exec_cmd("sudo mkfs.f2fs"
                          + " " + self.HOWTO_MKFS.get(fs, "")
                          + " " + dev_path,
                          self.dev_null)
        if p.returncode != 0:
            return False
        p = self.exec_cmd(' '.join(["sudo mount -t f2fs -o background_gc=off,checkpoint=disable ",
                                    dev_path, mnt_path]),
                          self.dev_null)
        if p.returncode != 0:
            return False
        p = self.exec_cmd("sudo chmod 777 " + mnt_path,
                          self.dev_null)
        if p.returncode != 0:
            return False
        return True

    def mount_ext4_no_jnl(self, media, fs, mnt_path):
        (rc, dev_path) = self.init_media(media)
        if not rc:
            return False

        p = self.exec_cmd("sudo mkfs.ext4"
                          + " " + self.HOWTO_MKFS.get(fs, "")
                          + " " + dev_path,
                          self.dev_null)
        if p.returncode != 0:
            return False
        p = self.exec_cmd("sudo tune2fs -O ^has_journal %s" % dev_path,
                          self.dev_null)
        if p.returncode != 0:
            return False
        p = self.exec_cmd(' '.join(["sudo mount -t ext4",
                                    dev_path, mnt_path]),
                          self.dev_null)
        if p.returncode != 0:
            return False
        p = self.exec_cmd("sudo chmod 777 " + mnt_path,
                          self.dev_null)
        if p.returncode != 0:
            return False
        return True

    def mount(self, media, fs, mnt_path):
        mount_fn = self.HOWTO_MOUNT.get(fs, None)
        if not mount_fn:
            return False;

        self.umount(mnt_path)
        self.exec_cmd("mkdir -p " + mnt_path, self.dev_null)
        return mount_fn(media, fs, mnt_path)

    def _match_config(self, key1, key2):
        def _match_value(filter_val, actual_val):
            if filter_val == "*" or actual_val == "*":
                return True
            if isinstance(filter_val, (list, tuple, set)):
                return any(_match_value(v, actual_val) for v in filter_val)
            return str(filter_val) == str(actual_val)

        for (k1, k2) in zip(key1, key2):
            if not _match_value(k1, k2):
                return False
        return True

    def gen_config(self):
        for ncore in sorted(self.ncores, reverse=True):
            for bench in self.BENCH_TYPES:
                for media in self.MEDIA_TYPES:
                    for dio in self.DIRECTIOS:
                        for fs in self.FS_TYPES:
                            if fs == "tmpfs" and media != "mem":
                                continue
                            mount_fn = self.HOWTO_MOUNT.get(fs, None)
                            if not mount_fn:
                                continue
                            if self._match_config(self.FILTER, \
                                                  (media, fs, bench, str(ncore), dio)):
                                yield(media, fs, bench, ncore, dio)

    def fxmark_env(self):
        env = ' '.join(["PERFMON_LEVEL=%s" % self.PERFMON_LEVEL,
                        "PERFMON_LDIR=%s"  % self.log_dir,
                        "PERFMON_LFILE=%s" % self.perfmon_log])
        return env

    def get_bin_type(self, bench):
        if bench.startswith("filebench_"):
            return (self.filebench_path, bench[len("filebench_"):])
        if bench.startswith("dbench_"):
            return (self.dbench_path, bench[len("dbench_"):])
        return (self.fxmark_path, bench)

    def fxmark(self, media, fs, bench, ncore, nfg, nbg, dio):
        self.perfmon_log = os.path.normpath(
            os.path.join(self.log_dir,
                         '.'.join([media, fs, bench, str(nfg), "pm"])))
        (bin, type) = self.get_bin_type(bench)
        directio = '1' if dio == "directio" else '0'

        if directio == '1':
            if fs == "tmpfs": 
                print("# INFO: DirectIO under tmpfs disabled by default")
                directio='0';
            else: 
                print("# INFO: DirectIO Enabled")

        cmd = ' '.join([self.fxmark_env(),
                        bin,
                        "--type", type,
                        "--ncore", str(ncore),
                        "--nbg",  str(nbg),
                        "--duration", str(self.DURATION),
                        "--directio", directio,
                        "--root", self.test_root,
                        "--profbegin", "\"%s\"" % self.perfmon_start,
                        "--profend",   "\"%s\"" % self.perfmon_stop,
                        "--proflog", self.perfmon_log])
        p = self.exec_cmd(cmd, self.redirect, bind=True)
        if self.redirect:
            for l in p.stdout.readlines():
                self.log(l.decode("utf-8").strip())

    def fxmark_cleanup(self):
        cmd = ' '.join([self.fxmark_env(),
                        "%s; rm -f %s/*.pm" % (self.perfmon_stop, self.log_dir)])
        self.exec_cmd(cmd)
        self.exec_cmd("sudo sh -c \"echo 0 >/proc/sys/kernel/lock_stat\"",
                      self.dev_null)

    def run(self, run_filters=None):
        configs = run_filters or [self.FILTER]
        ssrfs_enabled = False
        try:
            with open("/sys/module/ssrfs/parameters/ssrfs_enabled") as fd:
                ssrfs_enabled = fd.read().strip() not in ("", "0")
        except OSError:
            ssrfs_enabled = False
        try:
            for flt in configs:
                self.FILTER = flt
                self.ncores = self.get_ncores()

                cnt = -1
                totol = 0
                self.log_start()
                try:
                    for (cnt, (media, fs, bench, ncore, dio)) in enumerate(self.gen_config()):
                        (ncore, nbg) = self.add_bg_worker_if_needed(bench, ncore)
                        nfg = ncore - nbg

                        log_fs = fs + "s" if ssrfs_enabled else fs
                        if self.DRYRUN:
                            self.log("## %s:%s:%s:%s:%s" % (media, log_fs, bench, nfg, dio))
                            continue

                        self.prepre_work(ncore)
                        if not self.mount(media, fs, self.test_root):
                            self.log("# Fail to mount %s on %s." % (fs, media))
                            continue
                        self.log("## %s:%s:%s:%s:%s" % (media, log_fs, bench, nfg, dio))
                        self.pre_work()
                        self.fxmark(media, fs, bench, ncore, nfg, nbg, dio)
                        self.post_work()
                    totol += (cnt + 1)
                    self.log("### NUM_TEST_CONF  = %d" % totol)
                finally:
                    self.log_end()
        finally:
            signal.signal(signal.SIGINT, catch_ctrl_C)
            self.fxmark_cleanup()
            self.umount(self.test_root)
            self.set_cpus(0)

def _get_config_value(cfg, keys, default=None):
    for k in keys:
        if k in cfg:
            return cfg[k]
    return default

def normalize_filter(entry, default_media="*"):
    if "filter" in entry:
        flt = entry["filter"]
    else:
        directio = entry.get("directio", entry.get("dio", "*"))
        if isinstance(directio, bool):
            directio = "directio" if directio else "bufferedio"
        flt = [
            entry.get("media", entry.get("device", entry.get("dev_type", default_media))),
            entry.get("fs", "*"),
            entry.get("bench", "*"),
            entry.get("core", "*"),
            directio,
        ]
    if len(flt) != 5:
        raise ValueError("Each filter must have 5 elements: media, fs, bench, core, directio")
    return flt

def apply_device_paths(cfg):
    key_map = {
        "loop_dev": "LOOPDEV",
        "nvme_dev": "NVMEDEV",
        "hdd_dev": "HDDDEV",
        "ssd_dev": "SSDDEV",
    }
    for key in ("LOOPDEV", "NVMEDEV", "HDDDEV", "SSDDEV"):
        if key in cfg:
            setattr(Runner, key, cfg[key])
    for src, dst in key_map.items():
        if src in cfg:
            setattr(Runner, dst, cfg[src])
    if "dev_path" in cfg:
        dev_type = cfg.get("dev_type", "").lower()
        type_to_key = {
            "nvme": "NVMEDEV",
            "ssd": "SSDDEV",
            "hdd": "HDDDEV",
            "loop": "LOOPDEV",
        }
        dst = type_to_key.get(dev_type)
        if dst:
            setattr(Runner, dst, cfg["dev_path"])

def run_plot(runner, plot_cfg):
    if not plot_cfg:
        return
    plot_type = "sc"
    out_name = "plot"
    if isinstance(plot_cfg, bool):
        if not plot_cfg:
            return
    elif isinstance(plot_cfg, str):
        plot_type = plot_cfg
    elif isinstance(plot_cfg, dict):
        plot_type = plot_cfg.get("type", plot_type)
        out_name = plot_cfg.get("out", out_name)
    log_arg = runner.log_path
    out_arg = os.path.join(runner.log_dir, out_name)
    cmd = ' '.join([
        os.path.join(CUR_DIR, "plotter.py"),
        "--ty", str(plot_type),
        "--log", log_arg,
        "--out", out_arg,
    ])
    p = runner.exec_cmd(cmd, runner.redirect)
    if p.returncode != 0:
        print(f"Plot command failed: {cmd}", file=sys.stderr)

def confirm_media_path():
    print("%" * 80)
    print("%% WARNING! WARNING! WARNING! WARNING! WARNING!")
    print("%" * 80)
    yn = input(
        "All data in %s, %s, %s and %s will be deleted. Is it ok? [Y,N]: "
        % (Runner.HDDDEV, Runner.SSDDEV, Runner.NVMEDEV, Runner.LOOPDEV)
    )
    if yn != "Y":
        print("Please, check Runner.LOOPDEV and Runner.NVMEDEV")
        exit(1)
    yn = input("Are you sure? [Y,N]: ")
    if yn != "Y":
        print("Please, check Runner.LOOPDEV and Runner.NVMEDEV")
        exit(1)
    print("%" * 80)
    print("\n\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run fxmark workloads")
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="Path to JSON configuration file",
    )
    args = parser.parse_args()

    try:
        with open(args.config, "r") as fd:
            cfg = json.load(fd)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Failed to load config {args.config}: {exc}", file=sys.stderr)
        sys.exit(1)

    apply_device_paths(cfg)
    disk_size = _get_config_value(cfg, ["DISK_SIZE", "disk_size", "dev_size"], "32G")
    duration = _get_config_value(cfg, ["DURATION", "duration"], 5)
    raw_run_config = cfg.get("run_config", [])
    if isinstance(raw_run_config, dict):
        raw_run_config = [raw_run_config]
    default_media = cfg.get("dev_type", cfg.get("media", "*"))
    try:
        if not raw_run_config:
            raise ValueError("run_config must contain at least one entry")
        run_configs = []
        for entry in raw_run_config:
            flt = normalize_filter(entry, default_media)
            run_configs.append(flt)
    except ValueError as exc:
        print(f"Invalid run_config in {args.config}: {exc}", file=sys.stderr)
        sys.exit(1)
    plot_cfg = cfg.get("plot", None)

    # use a single timestamped log folder/file for all run_config entries in this invocation
    Runner.LOG_SUBDIR = str(datetime.datetime.now()).replace(' ','-').replace(':','-')

    # config parameters
    # -----------------
    #
    # o testing core granularity
    # - Runner.CORE_FINE_GRAIN
    # - Runner.CORE_COARSE_GRAIN
    #
    # o profiling level
    # - PerfMon.LEVEL_LOW
    # - PerfMon.LEVEL_PERF_RECORD
    # - PerfMon.LEVEL_PERF_PROBE_SLEEP_LOCK
    # - PerfMon.LEVEL_PERF_PROBE_SLEEP_LOCK_D  # do NOT use if you don't understand what it is
    # - PerfMon.LEVEL_PERF_LOCK                # do NOT use if you don't understand what it is
    # - PerfMon.LEVEL_PERF_STAT                # for cycles and instructions
    #
    # o testcase filter
    # - (storage device, filesystem, test case, # core, directio | bufferedio)

    # TODO: make it scriptable
    # confirm_media_path()
    runner = Runner(run_configs[0], disk_size=disk_size, duration=duration)
    runner.run(run_configs)
    run_plot(runner, plot_cfg)
