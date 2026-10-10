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
/* Nonblocking bounded input, including partial pipe writes. 1=line, 0=wait,
 * -1=EOF, -2=overlong command (discarded in full). */
static int input_line(char *out,size_t capacity) {
    static char buf[256]; static size_t used=0; static int overflow=0;
    for(;;) {
        char c;
#ifdef _WIN32
        HANDLE h=GetStdHandle(STD_INPUT_HANDLE); DWORD n=0,ready=0;
        if(GetFileType(h)==FILE_TYPE_PIPE) {
            if(!PeekNamedPipe(h,NULL,0,NULL,&ready,NULL)) return -1;
            if(!ready) return 0;
        } else if(WaitForSingleObject(h,0)!=WAIT_OBJECT_0) return 0;
        if(!ReadFile(h,&c,1,&n,NULL) || !n) return -1;
#else
        struct pollfd p={STDIN_FILENO,POLLIN,0};
        int r=poll(&p,1,0); if(r<0 && errno==EINTR) continue;
        if(r<=0) return r<0?-1:0;
        ssize_t n=read(STDIN_FILENO,&c,1); if(n<=0) return -1;
#endif
        if(c=='\r') continue;
        if(c=='\n') {
            if(overflow || used>=capacity) {used=0; overflow=0; return -2;}
            memcpy(out,buf,used); out[used]=0; used=0; return 1;
        }
        if(used<sizeof(buf)-1) buf[used++]=c; else overflow=1;
    }
}
#if defined(_WIN32)
#include <initguid.h>
#include <mmdeviceapi.h>
#include <endpointvolume.h>
static IAudioEndpointVolume *endpoint;
static int open_audio(void) {
    IMMDeviceEnumerator *e=NULL; IMMDevice *d=NULL;
    HRESULT hr=CoCreateInstance(&CLSID_MMDeviceEnumerator,NULL,CLSCTX_ALL,
        &IID_IMMDeviceEnumerator,(void **)&e);
    if(SUCCEEDED(hr)) hr=IMMDeviceEnumerator_GetDefaultAudioEndpoint(e,eRender,eMultimedia,&d);
    if(SUCCEEDED(hr)) hr=IMMDevice_Activate(d,&IID_IAudioEndpointVolume,CLSCTX_ALL,NULL,(void **)&endpoint);
    if(d) IMMDevice_Release(d); if(e) IMMDeviceEnumerator_Release(e);
    return SUCCEEDED(hr);
}
static int audio_get(double *value,int *muted) {
    float v; BOOL m;
    if(endpoint) {IAudioEndpointVolume_Release(endpoint);endpoint=NULL;}
    if(!open_audio()) return 0;
    if(FAILED(IAudioEndpointVolume_GetMasterVolumeLevelScalar(endpoint,&v)) ||
       FAILED(IAudioEndpointVolume_GetMute(endpoint,&m))) return 0;
    *value=v*100; *muted=m; return 1;
}
static int audio_set(double v) {
    if(endpoint) {IAudioEndpointVolume_Release(endpoint);endpoint=NULL;}
    if(!open_audio()) return 0;
    return SUCCEEDED(IAudioEndpointVolume_SetMasterVolumeLevelScalar(endpoint,(float)(v/100),NULL));
}
#elif defined(__APPLE__)
#include <CoreAudio/CoreAudio.h>
static AudioDeviceID device(void) {
    AudioDeviceID d=0; UInt32 n=sizeof(d);
    AudioObjectPropertyAddress p={kAudioHardwarePropertyDefaultOutputDevice,kAudioObjectPropertyScopeGlobal,kAudioObjectPropertyElementMain};
    if(AudioObjectGetPropertyData(kAudioObjectSystemObject,&p,0,NULL,&n,&d)!=noErr) return 0;
    return d;
}
static int channel_get(AudioDeviceID d,UInt32 ch,Float32 *v) {
    AudioObjectPropertyAddress p={kAudioDevicePropertyVolumeScalar,kAudioDevicePropertyScopeOutput,ch};
    UInt32 n=sizeof(*v); return AudioObjectGetPropertyData(d,&p,0,NULL,&n,v)==noErr;
}
static int audio_get(double *v,int *muted) {
    AudioDeviceID d=device(); Float32 a=0,b=0; if(!d) return 0;
    if(channel_get(d,0,&a)) *v=a*100;
    else {
        int left=channel_get(d,1,&a),right=channel_get(d,2,&b);
        if(!left && !right) return 0;
        *v=(left && right?(a+b)/2:left?a:b)*100;
    }
    UInt32 m=0,n=sizeof(m);
    AudioObjectPropertyAddress p={kAudioDevicePropertyMute,kAudioDevicePropertyScopeOutput,kAudioObjectPropertyElementMain};
    *muted=AudioObjectGetPropertyData(d,&p,0,NULL,&n,&m)==noErr && m; return 1;
}
static int audio_set(double v) {
    AudioDeviceID d=device(); if(!d) return 0; Float32 f=(Float32)(v/100);
    AudioObjectPropertyAddress p={kAudioDevicePropertyVolumeScalar,kAudioDevicePropertyScopeOutput,kAudioObjectPropertyElementMain};
    Boolean writable=0;
    if(AudioObjectIsPropertySettable(d,&p,&writable)==noErr && writable)
        return AudioObjectSetPropertyData(d,&p,0,NULL,sizeof(f),&f)==noErr;
    int count=0;
    for(UInt32 ch=1;ch<=2;ch++) {
        p.mElement=ch;
        if(AudioObjectIsPropertySettable(d,&p,&writable)==noErr && writable) {
            if(AudioObjectSetPropertyData(d,&p,0,NULL,sizeof(f),&f)!=noErr) return 0;
            count++;
        }
    }
    return count>0;
}
#else
static int audio_get(double *v,int *m) {(void)v;(void)m;return 0;}
static int audio_set(double v) {(void)v;return 0;}
#endif
static void volume_event(int command) {
    double v=0; int m=0;
    if(!audio_get(&v,&m)) {failure("volume","Default output device has no accessible volume control",command); return;}
    event_start(command?"command_result":"telemetry","volume");
    printf("\"ok\":true,\"data\":{\"value\":%.2f,\"muted\":%s}}\n",v,m?"true":"false");fflush(stdout);
}
int main(int argc,char **argv) {
    setvbuf(stdout,NULL,_IOFBF,65536);
    unsigned interval=500; int once=0;
    for(int i=1;i<argc;i++) {
        if(!strcmp(argv[i],"--once")) once=1;
        else if(!strcmp(argv[i],"--interval") && i+1<argc) interval=interval_arg(argv[++i],100,60000);
        else {fprintf(stderr,"Usage: volume [--once] [--interval milliseconds]; stdin: set 0..100\n"); return 2;}
    }
#ifdef _WIN32
    if(FAILED(CoInitializeEx(NULL,COINIT_MULTITHREADED))) return 1;
#endif
    volume_event(0);
    uint64_t next=monotonic_ms()+interval;
    while(!once) {
        char line[256],value[128],extra; int r=input_line(line,sizeof(line));
        if(r==-1) break;
        if(r==-2) failure("volume","Command too long",1);
        if(r==1) {
            double v;
            if(!strcmp(line,"stop")) break;
            if(sscanf(line,"set %127s %c",value,&extra)!=1 || !number(value,0,100,&v)) failure("volume","Expected set 0..100",1);
            else if(!audio_set(v)) failure("volume","Volume change failed or device is read-only",1);
            else volume_event(1);
        }
        if(monotonic_ms()>=next) {volume_event(0); next=monotonic_ms()+interval;}
        pause_ms(10);
    }
#ifdef _WIN32
    if(endpoint) IAudioEndpointVolume_Release(endpoint); CoUninitialize();
#endif
    return 0;
}
