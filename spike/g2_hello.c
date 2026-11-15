#include <stdio.h>
#include <sys/neutrino.h>
#include <sys/utsname.h>
#include <time.h>
#include <sys/syspage.h>
int main(void){
  struct utsname u; uname(&u);
  struct timespec ts; clock_getres(CLOCK_MONOTONIC,&ts);
  printf("TT-G2 hello from %s %s %s %s, cpu=%d, clock_res_ns=%ld\n",u.sysname,u.release,u.version,u.machine,_syspage_ptr? (int)_syspage_ptr->num_cpu:-1,ts.tv_nsec);
  return 0;
}
