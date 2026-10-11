/* Standalone Janus native tool. JSON Lines on stdout; diagnostics on stderr.
 * Build commands and wire protocol are in agent/README.md. */
/* Darwin SDK headers need BSD types even when compiling in C11 mode. */
#if defined(__APPLE__)
#ifndef _DARWIN_C_SOURCE
#define _DARWIN_C_SOURCE 1
#endif
#elif !defined(_WIN32) && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200809L
#endif
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
#include <time.h>
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
#ifdef _WIN32
#include <initguid.h>
#include <wbemidl.h>
static IWbemServices *sensors;
static int open_sensors(void) {
    IWbemLocator *locator=NULL;
    if(FAILED(CoCreateInstance(&CLSID_WbemLocator,NULL,CLSCTX_INPROC_SERVER,&IID_IWbemLocator,(void **)&locator))) return 0;
    const wchar_t *spaces[]={L"ROOT\\LibreHardwareMonitor",L"ROOT\\OpenHardwareMonitor"};
    for(int i=0;i<2 && !sensors;i++) {
        BSTR ns=SysAllocString(spaces[i]);
        if(ns) {IWbemLocator_ConnectServer(locator,ns,NULL,NULL,NULL,0,NULL,NULL,&sensors);SysFreeString(ns);}
    }
    IWbemLocator_Release(locator);
    if(sensors && FAILED(CoSetProxyBlanket((IUnknown *)sensors,RPC_C_AUTHN_WINNT,RPC_C_AUTHZ_NONE,NULL,RPC_C_AUTHN_LEVEL_CALL,RPC_C_IMP_LEVEL_IMPERSONATE,NULL,EOAC_NONE))) {
        IWbemServices_Release(sensors); sensors=NULL;
    }
    return sensors!=NULL;
}
static int temperature(double *value) {
    if(!sensors) return 0;
    BSTR lang=SysAllocString(L"WQL"),query=SysAllocString(L"SELECT Value FROM Sensor WHERE SensorType='Temperature' AND Identifier LIKE '%cpu%'");
    IEnumWbemClassObject *items=NULL;
    HRESULT hr=IWbemServices_ExecQuery(sensors,lang,query,WBEM_FLAG_FORWARD_ONLY|WBEM_FLAG_RETURN_IMMEDIATELY,NULL,&items);
    SysFreeString(lang);SysFreeString(query); if(FAILED(hr)) return 0;
    int found=0; double hottest=-100;
    for(int i=0;i<128;i++) {
        IWbemClassObject *obj=NULL; ULONG n=0;
        hr=IEnumWbemClassObject_Next(items,200,1,&obj,&n);
        if(FAILED(hr) || !n) break;
        VARIANT v; VariantInit(&v);
        if(SUCCEEDED(IWbemClassObject_Get(obj,L"Value",0,&v,NULL,NULL))) {
            VARIANT converted;VariantInit(&converted);
            if(SUCCEEDED(VariantChangeType(&converted,&v,0,VT_R8))) {
                double t=V_R8(&converted);
                if(isfinite(t) && t>0 && t<150) {if(t>hottest) hottest=t;found=1;}
            }
            VariantClear(&converted);
        }
        VariantClear(&v); IWbemClassObject_Release(obj);
    }
    IEnumWbemClassObject_Release(items); *value=hottest; return found;
}
static int ram(uint64_t *total,uint64_t *used) {
    MEMORYSTATUSEX m={0};m.dwLength=sizeof(m);if(!GlobalMemoryStatusEx(&m)) return 0;
    *total=m.ullTotalPhys;*used=m.ullTotalPhys-m.ullAvailPhys;return 1;
}
#elif defined(__APPLE__)
#include <sys/types.h>
#include <sys/sysctl.h>
#include <mach/mach.h>
#include <IOKit/IOKitLib.h>
/* AppleSMC is an optional Intel sensor interface, not an Apple Silicon API. */
typedef struct {uint8_t major,minor,build,reserved;uint16_t release;} SMCVersion;
typedef struct {uint16_t version,length;uint32_t cpu,gpu,mem;} SMCLimits;
typedef struct {uint32_t size,type;uint8_t attributes;} SMCInfo;
typedef struct {uint32_t key;SMCVersion version;SMCLimits limits;SMCInfo info;uint8_t result,status,command;uint32_t parameter;uint8_t bytes[32];} SMCData;
static io_connect_t smc;
static int open_sensors(void) {
#if defined(__x86_64__)
    io_service_t s=IOServiceGetMatchingService(kIOMasterPortDefault,IOServiceMatching("AppleSMC"));
    if(!s) return 0;
    kern_return_t r=IOServiceOpen(s,mach_task_self(),0,&smc);IOObjectRelease(s);return r==KERN_SUCCESS;
#else
    return 0;
#endif
}
static int smc_call(SMCData *in,SMCData *out) {
    size_t size=sizeof(*out);memset(out,0,size);
    return IOConnectCallStructMethod(smc,2,in,sizeof(*in),out,&size)==KERN_SUCCESS && !out->result;
}
static int temperature(double *v) {
    if(!smc) return 0;
    const char *keys[]={"TC0P","TC0D","TC0E","TC0F"};
    for(int i=0;i<4;i++) {
        SMCData in={0},out={0};
        for(int j=0;j<4;j++) in.key=(in.key<<8)|(unsigned char)keys[i][j];
        in.command=9;
        if(!smc_call(&in,&out) || out.info.type!=0x73703738 || out.info.size!=2) continue;
        in.info=out.info;in.command=5;
        if(!smc_call(&in,&out)) continue;
        int16_t raw=(int16_t)((out.bytes[0]<<8)|out.bytes[1]);double t=raw/256.0;
        if(t>0 && t<150) {*v=t;return 1;}
    }
    return 0;
}
static int ram(uint64_t *total,uint64_t *used) {
    size_t n=sizeof(*total);if(sysctlbyname("hw.memsize",total,&n,NULL,0)) return 0;
    vm_statistics64_data_t vm;mach_msg_type_number_t count=HOST_VM_INFO64_COUNT;vm_size_t pagesize;
    mach_port_t host=mach_host_self();
    int ok=host_page_size(host,&pagesize)==KERN_SUCCESS && host_statistics64(host,HOST_VM_INFO64,(host_info64_t)&vm,&count)==KERN_SUCCESS;
    mach_port_deallocate(mach_task_self(),host);if(!ok) return 0;
    *used=((uint64_t)vm.active_count+vm.wire_count+vm.compressor_page_count)*pagesize;
    return 1;
}
#else
static int open_sensors(void) {return 0;}
static int temperature(double *v) {(void)v;return 0;}
static int ram(uint64_t *t,uint64_t *u) {(void)t;(void)u;return 0;}
#endif
#include <sys/stat.h>
static int temperature_file(const char *path,double *value) {
    struct stat info;
    if(stat(path,&info)!=0 || time(NULL)-info.st_mtime>5) return 0;
    FILE *f=fopen(path,"r"); if(!f) return 0;
    char buf[80];int ok=fgets(buf,sizeof(buf),f)!=NULL;fclose(f);
    if(!ok) return 0;buf[strcspn(buf,"\r\n")]=0;
    return number(buf,0.01,149.99,value);
}
/* A first sample, stopped counter or counter reset cannot supply utilization. */
static int cpu_delta(uint64_t total,uint64_t idle,uint64_t *previous_total,uint64_t *previous_idle,double *value) {
    uint64_t elapsed=total>=*previous_total?total-*previous_total:0;
    uint64_t idle_elapsed=idle>=*previous_idle?idle-*previous_idle:0;
    int available=*previous_total && elapsed && idle>=*previous_idle && idle_elapsed<=elapsed;
    *previous_total=total;*previous_idle=idle;
    if(available) *value=100.0*(elapsed-idle_elapsed)/elapsed;
    return available;
}
/* CPU utilization from OS counters; the first sample has no elapsed interval. */
static int cpu_percent(double *value) {
    static uint64_t previous_total=0,previous_idle=0;
    uint64_t total=0,idle=0;
#ifdef _WIN32
    FILETIME ft_idle,ft_kernel,ft_user;ULARGE_INTEGER i,k,u;
    if(!GetSystemTimes(&ft_idle,&ft_kernel,&ft_user)) return 0;
    i.LowPart=ft_idle.dwLowDateTime;i.HighPart=ft_idle.dwHighDateTime;
    k.LowPart=ft_kernel.dwLowDateTime;k.HighPart=ft_kernel.dwHighDateTime;
    u.LowPart=ft_user.dwLowDateTime;u.HighPart=ft_user.dwHighDateTime;
    idle=i.QuadPart;total=k.QuadPart+u.QuadPart;
#elif defined(__APPLE__)
    host_cpu_load_info_data_t info;mach_msg_type_number_t count=HOST_CPU_LOAD_INFO_COUNT;
    mach_port_t host=mach_host_self();
    kern_return_t status=host_statistics(host,HOST_CPU_LOAD_INFO,(host_info_t)&info,&count);
    mach_port_deallocate(mach_task_self(),host);
    if(status!=KERN_SUCCESS) return 0;
    idle=info.cpu_ticks[CPU_STATE_IDLE];
    for(int state=0;state<CPU_STATE_MAX;state++) total+=info.cpu_ticks[state];
#else
    (void)value;return 0;
#endif
    return cpu_delta(total,idle,&previous_total,&previous_idle,value);
}
int main(int argc,char **argv) {
    setvbuf(stdout,NULL,_IOFBF,65536);unsigned interval=1000;int once=0;const char *file=NULL;
    for(int i=1;i<argc;i++) {
        if(!strcmp(argv[i],"--once")) once=1;
        else if(!strcmp(argv[i],"--interval") && i+1<argc) interval=interval_arg(argv[++i],250,60000);
        else if(!strcmp(argv[i],"--temperature-file") && i+1<argc) file=argv[++i];
        else {fprintf(stderr,"Usage: metrics [--once] [--interval ms] [--temperature-file path]\n");return 2;}
    }
#ifdef _WIN32
    if(FAILED(CoInitializeEx(NULL,COINIT_MULTITHREADED))) return 1;
    CoInitializeSecurity(NULL,-1,NULL,NULL,RPC_C_AUTHN_LEVEL_DEFAULT,RPC_C_IMP_LEVEL_IMPERSONATE,NULL,EOAC_NONE,NULL);
#endif
    open_sensors();
    do {
        uint64_t total=0,used=0;double temp=0;int available=file?temperature_file(file,&temp):temperature(&temp);
        double cpu=0;int cpu_available=cpu_percent(&cpu);
        event_start("telemetry","metrics");printf("\"data\":{\"cpu_percent\":");
        if(cpu_available) printf("%.2f",cpu);else printf("null");
        printf(",\"ram\":");
        if(ram(&total,&used)) printf("{\"total_bytes\":%llu,\"used_bytes\":%llu,\"percent\":%.2f}",(unsigned long long)total,(unsigned long long)used,total?100.0*used/total:0);
        else printf("null");
        printf(",\"cpu_temperature_c\":");if(available) printf("%.2f",temp);else printf("null");
        printf(",\"temperature_source\":");
        if(available) json_string(file?"local_sensor_file":
#ifdef _WIN32
            "hardware_monitor_wmi"
#else
            "apple_smc"
#endif
        ); else printf("null");
        printf(",\"temperature_status\":");
        json_string(available?"available":file?"Sensor file missing, stale (over 5s) or invalid":"No readable CPU sensor; see README for a local sensor provider");puts("}}");fflush(stdout);
        if(!once) pause_ms(interval);
    } while(!once);
#ifdef _WIN32
    if(sensors) IWbemServices_Release(sensors);CoUninitialize();
#elif defined(__APPLE__)
    if(smc) IOServiceClose(smc);
#endif
    return 0;
}
