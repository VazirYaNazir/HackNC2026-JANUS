/* Standalone Janus native tool. JSON Lines on stdout; diagnostics on stderr.
 * Build commands and wire protocol are in agent/README.md. */
#define _POSIX_C_SOURCE 200809L
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601
#endif
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#if defined(_WIN32)
#include <windows.h>
#else
#include <time.h>
#include <unistd.h>
#include <poll.h>
#endif

static uint64_t stamp_ms(void) {
#ifdef _WIN32
    FILETIME ft; ULARGE_INTEGER n;
    GetSystemTimeAsFileTime(&ft); n.LowPart=ft.dwLowDateTime; n.HighPart=ft.dwHighDateTime;
    return (n.QuadPart-116444736000000000ULL)/10000;
#else
    struct timespec t; clock_gettime(CLOCK_REALTIME,&t);
    return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;
#endif
}
static uint64_t monotonic_ms(void) {
#ifdef _WIN32
    return GetTickCount64();
#else
    struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;
#endif
}
static void pause_ms(unsigned n) {
#ifdef _WIN32
    Sleep(n);
#else
    struct timespec t={n/1000,(long)(n%1000)*1000000};
    while(nanosleep(&t,&t)<0 && errno==EINTR) {}
#endif
}
static void json_string(const char *s) {
    putchar('"');
    for(const unsigned char *p=(const unsigned char *)s; *p; ++p) {
        if(*p=='"' || *p=='\\') {putchar('\\'); putchar(*p);}
        else if(*p<32) printf("\\u%04x",*p);
        else if(*p>=128) {
            unsigned count=(*p>=0xc2 && *p<=0xdf)?2:(*p>=0xe0 && *p<=0xef)?3:(*p>=0xf0 && *p<=0xf4)?4:0;
            unsigned code=count?(*p & ((1U<<(7-count))-1)):0;int valid=count!=0;
            for(unsigned j=1;j<count;j++) {
                if((p[j]&0xc0)!=0x80) {valid=0;break;}
                code=(code<<6)|(p[j]&0x3f);
            }
            if((count==2 && code<128) || (count==3 && code<2048) || (count==4 && code<65536) || (code>=0xd800 && code<=0xdfff) || code>0x10ffff) valid=0;
            if(valid) {fwrite(p,1,count,stdout);p+=count-1;}
            else printf("\\ufffd");
        }
        else putchar(*p);
    }
    putchar('"');
}
static void event_start(const char *type,const char *stream) {
    static unsigned long seq=0;
    printf("{\"type\":\"%s\",\"stream\":\"%s\",\"timestamp_ms\":%llu,\"seq\":%lu,",
        type,stream,(unsigned long long)stamp_ms(),seq++);
}
static void failure(const char *stream,const char *reason,int command) {
    event_start(command?"command_result":"error",stream);
    printf("\"ok\":false,\"error\":"); json_string(reason); puts("}");fflush(stdout);
}
static int number(const char *s,double low,double high,double *out) {
    char *end; errno=0; double n=strtod(s,&end);
    if(errno || end==s || *end || !isfinite(n) || n<low || n>high) return 0;
    *out=n; return 1;
}
static unsigned interval_arg(const char *s,unsigned low,unsigned high) {
    double n;
    if(!number(s,low,high,&n) || floor(n)!=n) {
        fprintf(stderr,"Invalid integer interval/limit: %s\n",s); exit(2);
    }
    return (unsigned)n;
}
typedef struct {
    unsigned long pid,ppid;uint64_t birth,cpu_ns,rss;
    char name[1024];double cpu_percent;int measured,cpu_known,ram_known,selected,include;
} Process;
static Process *list;static size_t length,capacity;
static void append(Process *p) {
    if(length==capacity) {
        capacity=capacity?capacity*2:256;
        Process *n=realloc(list,capacity*sizeof(*list));
        if(!n) {fprintf(stderr,"Out of memory\n");exit(1);}list=n;
    }
    list[length++]=*p;
}
#ifdef _WIN32
#include <tlhelp32.h>
#include <psapi.h>
static uint64_t filetime_value(FILETIME t) {ULARGE_INTEGER n;n.LowPart=t.dwLowDateTime;n.HighPart=t.dwHighDateTime;return n.QuadPart;}
static int snapshot(int all) {
    HANDLE snap=CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0);if(snap==INVALID_HANDLE_VALUE) return 0;
    DWORD session=0;ProcessIdToSessionId(GetCurrentProcessId(),&session);
    PROCESSENTRY32W e={0};e.dwSize=sizeof(e);
    if(Process32FirstW(snap,&e)) do {
        DWORD owner_session=0;
        if(!e.th32ProcessID || (!all && (!ProcessIdToSessionId(e.th32ProcessID,&owner_session) || owner_session!=session))) continue;
        Process p={0};p.pid=e.th32ProcessID;p.ppid=e.th32ParentProcessID;
        WideCharToMultiByte(CP_UTF8,0,e.szExeFile,-1,p.name,sizeof(p.name),NULL,NULL);
        HANDLE h=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|PROCESS_VM_READ,FALSE,e.th32ProcessID);
        if(!h) h=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,FALSE,e.th32ProcessID);
        if(h) {
            FILETIME birth,exit_time,kernel,user;
            if(GetProcessTimes(h,&birth,&exit_time,&kernel,&user)) {
                p.birth=filetime_value(birth);p.cpu_ns=(filetime_value(kernel)+filetime_value(user))*100;p.measured=1;
            }
            PROCESS_MEMORY_COUNTERS m={0};m.cb=sizeof(m);
            if(GetProcessMemoryInfo(h,&m,sizeof(m))) {p.rss=m.WorkingSetSize;p.ram_known=1;}
            CloseHandle(h);
        }
        append(&p);
    } while(Process32NextW(snap,&e));
    CloseHandle(snap);return 1;
}
#elif defined(__APPLE__)
#include <libproc.h>
#include <sys/proc_info.h>
static int snapshot(int all) {
    int size=proc_listpids(PROC_ALL_PIDS,0,NULL,0);if(size<=0) return 0;
    size+=4096*sizeof(pid_t);pid_t *ids=calloc(1,size);if(!ids) return 0;
    int bytes=proc_listpids(PROC_ALL_PIDS,0,ids,size);
    if(bytes<=0) {free(ids);return 0;}
    for(int i=0;i<bytes/(int)sizeof(pid_t);i++) {
        struct proc_bsdinfo b={0};struct proc_taskinfo t={0};pid_t pid=ids[i];
        if(pid<=0 || proc_pidinfo(pid,PROC_PIDTBSDINFO,0,&b,sizeof(b))!=sizeof(b) || (!all && b.pbi_uid!=geteuid())) continue;
        Process p={0};p.pid=pid;p.ppid=b.pbi_ppid;p.birth=b.pbi_start_tvsec*1000000ULL+b.pbi_start_tvusec;
        memcpy(p.name,b.pbi_name,sizeof(b.pbi_name));
        if(!p.name[0]) memcpy(p.name,b.pbi_comm,sizeof(b.pbi_comm));
        if(proc_pidinfo(pid,PROC_PIDTASKINFO,0,&t,sizeof(t))==sizeof(t)) {
            p.cpu_ns=t.pti_total_user+t.pti_total_system;p.rss=t.pti_resident_size;p.measured=p.ram_known=1;
        }
        append(&p);
    }
    free(ids);return 1;
}
#else
static int snapshot(int all) {(void)all;return 0;}
#endif
static int compare_pid(const void *a,const void *b) {
    unsigned long x=((const Process *)a)->pid,y=((const Process *)b)->pid;return (x>y)-(x<y);
}
static Process *find(Process *items,size_t n,unsigned long pid) {
    Process key={0};key.pid=pid;return bsearch(&key,items,n,sizeof(*items),compare_pid);
}
static Process *parent_of(Process *p) {
    Process *parent=find(list,length,p->ppid);
    /* An OS parent PID can outlive its original process and be reused. */
    if(parent==p || (parent && parent->birth && p->birth && parent->birth>p->birth)) return NULL;
    return parent;
}
static void select_tree(int all,unsigned long root,double cpu,double ram_mb) {
    for(size_t i=0;i<length;i++) {
        Process *p=&list[i];
        p->selected=all || (!root && ((p->cpu_known && p->cpu_percent>=cpu) || (p->ram_known && p->rss>=ram_mb*1048576.0)));
        if(root) {
            Process *parent=p;
            for(size_t j=0;j<length && parent;j++) {
                if(parent->pid==root) {p->selected=1;break;}
                parent=parent_of(parent);
            }
        }
        if(p->selected) p->include=1;
    }
    for(size_t i=0;i<length;i++) if(list[i].selected) {
        Process *p=&list[i];
        for(size_t j=0;j<length;j++) {
            p=parent_of(p);if(!p || p->include) break;p->include=1;
        }
    }
}
int main(int argc,char **argv) {
    setvbuf(stdout,NULL,_IOFBF,65536);unsigned interval=1000;int once=0,all=0;unsigned long root=0;double cpu=1,ram_mb=100;
    for(int i=1;i<argc;i++) {
        if(!strcmp(argv[i],"--once")) once=1;
        else if(!strcmp(argv[i],"--all")) all=1;
        else if(!strcmp(argv[i],"--interval") && i+1<argc) interval=interval_arg(argv[++i],250,60000);
        else if(!strcmp(argv[i],"--pid") && i+1<argc) root=interval_arg(argv[++i],1,INT_MAX);
        else if(!strcmp(argv[i],"--min-cpu") && i+1<argc && number(argv[++i],0,100000,&cpu)) {}
        else if(!strcmp(argv[i],"--min-ram-mb") && i+1<argc && number(argv[++i],0,1000000,&ram_mb)) {}
        else {fprintf(stderr,"Usage: processes [--once] [--interval ms] [--min-cpu percent] [--min-ram-mb MiB] [--pid PID | --all]\n");return 2;}
    }
    if(all && root) {fprintf(stderr,"--all and --pid are mutually exclusive\n");return 2;}
    if(!snapshot(all)) {failure("processes","Process enumeration unavailable on this OS",0);return 1;}
    qsort(list,length,sizeof(*list),compare_pid);uint64_t previous_time=monotonic_ms();
    do {
        Process *previous=list;size_t previous_n=length;list=NULL;length=capacity=0;
        pause_ms(interval);
        if(!snapshot(all)) {free(previous);failure("processes","Process snapshot failed",0);return 1;}
        qsort(list,length,sizeof(*list),compare_pid);uint64_t now=monotonic_ms(),elapsed=now-previous_time;previous_time=now;
        for(size_t i=0;i<length;i++) {
            Process *p=&list[i],*old=find(previous,previous_n,p->pid);
            if(old && p->measured && old->measured && p->birth==old->birth && p->cpu_ns>=old->cpu_ns && elapsed)
                {p->cpu_percent=100.0*(p->cpu_ns-old->cpu_ns)/(elapsed*1000000.0);p->cpu_known=1;}
        }
        free(previous);select_tree(all,root,cpu,ram_mb);
        event_start("telemetry","processes");printf("\"data\":{\"sample_ms\":%llu,\"min_cpu_percent\":%.2f,\"min_ram_bytes\":%.0f,\"processes\":[",(unsigned long long)elapsed,cpu,ram_mb*1048576);
        int comma=0;
        for(size_t i=0;i<length;i++) if(list[i].include) {
            Process *p=&list[i],*parent=parent_of(p);
            if(comma++) putchar(',');
            printf("{\"pid\":%lu,\"ppid\":%lu,\"parent_pid\":",p->pid,p->ppid);
            if(parent && parent->include && parent!=p) printf("%lu",parent->pid);else printf("null");
            printf(",\"name\":");json_string(p->name);printf(",\"cpu_percent\":");
            if(p->cpu_known) printf("%.2f",p->cpu_percent);else printf("null");
            printf(",\"ram_bytes\":");if(p->ram_known) printf("%llu",(unsigned long long)p->rss);else printf("null");
            printf(",\"context\":%s}",p->selected?"false":"true");
        }
        puts("]}}");fflush(stdout);
    } while(!once);
    free(list);return 0;
}
