#!/usr/bin/env python3
"""Exercise the real MRDM/MRDL loop with deterministic directory streams."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PREFIX = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stddef.h>
#define PATH_MAX 4096
struct bench { int stop; };
struct worker { struct bench *bench; double works; };
typedef int DIR;
struct dirent { int unused; };
static struct bench bench;
static DIR stream;
static struct dirent entry;
static int mode, opens, closes, reads;
static void set_test_root(struct worker *w, char *p) { p[0]=0; }
static DIR *opendir(const char *p) {
  ++opens; reads=0;
  if (mode==1) { errno=EACCES; return NULL; }
  return &stream;
}
static struct dirent *readdir(DIR *d) {
  if (mode==2) { errno=EAGAIN; return NULL; }
  if (++reads <= 2) return &entry;
  return NULL;
}
static int readdir_r(DIR *d, struct dirent *e, struct dirent **r) {
  *r=readdir(d); return errno;
}
static int closedir(DIR *d) {
  if (++closes==2) bench.stop=1;
  if (mode==2 || mode==3) { errno=EIO; return -1; }
  return 0;
}
'''
MAIN = r'''
int main(void) {
  for (mode=0; mode<4; ++mode) {
    struct worker w={.bench=&bench};
    bench.stop=opens=closes=reads=0; errno=0;
    int ret=main_work(&w);
    if (!mode) { assert(!ret); assert(opens==2 && closes==2); assert(w.works==4); }
    if (mode==1) { assert(ret==EACCES && closes==0 && w.works==0); }
    if (mode==2) { assert(ret==EAGAIN && closes==1 && w.works==0); }
    if (mode==3) { assert(ret==EIO && closes==1 && w.works==2); }
  }
  return 0;
}
'''
class ReaddirTest(unittest.TestCase):
    def test_actual_loops(self):
        for bench in ('MRDM', 'MRDL'):
            with self.subTest(bench=bench), tempfile.TemporaryDirectory() as tmp:
                source = (ROOT / 'src' / (bench + '.c')).read_text()
                function = source[source.index('static int main_work('):]
                function = function[:function.index('\nstruct bench_operations')]
                exe = str(pathlib.Path(tmp) / 'test')
                subprocess.run(['cc', '-x', 'c', '-o', exe, '-'],
                               input=PREFIX + function + MAIN, text=True, check=True)
                subprocess.run([exe], timeout=3, check=True)
if __name__ == '__main__':
    unittest.main()
