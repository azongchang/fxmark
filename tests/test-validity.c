#include <assert.h>
#include <errno.h>
#include <string.h>
#include "bench.h"
int main(void)
{
 struct worker w = {0};
 struct bench b = {.ncpu=1, .workers=&w};
 int errors[] = {ENOSPC, EIO, EAGAIN};
 for (unsigned int i=0; i<sizeof(errors)/sizeof(errors[0]); ++i) {
  char buf[256] = {0};
  FILE *out = tmpfile();
  assert(out);
  w.ret=errors[i]; w.usecs=3000; w.works=190;
  assert(bench_error(&b)==errors[i]);
  report_bench(&b,out); rewind(out);
  assert(fgets(buf,sizeof(buf),out));
  assert(strstr(buf,"# ERROR worker="));
  fclose(out);
 }
 w.ret=0; assert(bench_error(&b)==0);
 b.duration=3;
 {
  FILE *out=tmpfile();
  assert(out);
  report_bench(&b,out);
  assert(bench_error(&b)==ETIMEDOUT);
  fclose(out);
 }
 w.ret=0; w.usecs=3000000;
 {
  FILE *out=tmpfile();
  assert(out);
  report_bench(&b,out);
  assert(bench_error(&b)==0);
  fclose(out);
 }
 puts("PASS: ENOSPC/EIO/EAGAIN cannot produce a success report");
}
