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
#ifdef __APPLE__
#include <ApplicationServices/ApplicationServices.h>
#endif
#include <signal.h>
static volatile sig_atomic_t stopped;
static void on_stop(int n) {(void)n;stopped=1;}
static int held[3];
static int mouse_move(double dx,double dy) {
#ifdef _WIN32
    INPUT in={0}; in.type=INPUT_MOUSE; in.mi.dx=(LONG)dx; in.mi.dy=(LONG)dy; in.mi.dwFlags=MOUSEEVENTF_MOVE;
    return SendInput(1,&in,sizeof(in))==1;
#elif defined(__APPLE__)
    if(!AXIsProcessTrusted()) return 0;
    CGEventRef current=CGEventCreate(NULL); if(!current) return 0;
    CGPoint p=CGEventGetLocation(current); CFRelease(current); p.x+=dx; p.y+=dy;
    CGRect bounds=CGDisplayBounds(CGMainDisplayID());
    p.x=fmax(CGRectGetMinX(bounds),fmin(CGRectGetMaxX(bounds)-1,p.x));
    p.y=fmax(CGRectGetMinY(bounds),fmin(CGRectGetMaxY(bounds)-1,p.y));
    CGEventType type=held[0]?kCGEventLeftMouseDragged:held[1]?kCGEventRightMouseDragged:held[2]?kCGEventOtherMouseDragged:kCGEventMouseMoved;
    CGMouseButton b=held[1]?kCGMouseButtonRight:held[2]?kCGMouseButtonCenter:kCGMouseButtonLeft;
    CGEventRef e=CGEventCreateMouseEvent(NULL,type,p,b); if(!e) return 0;
    CGEventPost(kCGHIDEventTap,e); CFRelease(e); return 1;
#else
    (void)dx;(void)dy;return 0;
#endif
}
static int mouse_button(int b,int down) {
#ifdef _WIN32
    DWORD flags[3][2]={{MOUSEEVENTF_LEFTUP,MOUSEEVENTF_LEFTDOWN},{MOUSEEVENTF_RIGHTUP,MOUSEEVENTF_RIGHTDOWN},{MOUSEEVENTF_MIDDLEUP,MOUSEEVENTF_MIDDLEDOWN}};
    INPUT in={0}; in.type=INPUT_MOUSE; in.mi.dwFlags=flags[b][down];
    if(SendInput(1,&in,sizeof(in))!=1) return 0;
#elif defined(__APPLE__)
    if(!AXIsProcessTrusted()) return 0;
    CGEventRef current=CGEventCreate(NULL); if(!current) return 0;
    CGPoint p=CGEventGetLocation(current); CFRelease(current);
    CGEventType types[3][2]={{kCGEventLeftMouseUp,kCGEventLeftMouseDown},{kCGEventRightMouseUp,kCGEventRightMouseDown},{kCGEventOtherMouseUp,kCGEventOtherMouseDown}};
    CGMouseButton buttons[3]={kCGMouseButtonLeft,kCGMouseButtonRight,kCGMouseButtonCenter};
    CGEventRef e=CGEventCreateMouseEvent(NULL,types[b][down],p,buttons[b]); if(!e) return 0;
    CGEventPost(kCGHIDEventTap,e); CFRelease(e);
#else
    (void)b;(void)down;return 0;
#endif
    held[b]=down; return 1;
}
static int mouse_scroll(int amount) {
#ifdef _WIN32
    INPUT in={0}; in.type=INPUT_MOUSE; in.mi.dwFlags=MOUSEEVENTF_WHEEL; in.mi.mouseData=(DWORD)(amount*WHEEL_DELTA);
    return SendInput(1,&in,sizeof(in))==1;
#elif defined(__APPLE__)
    if(!AXIsProcessTrusted()) return 0;
    CGEventRef e=CGEventCreateScrollWheelEvent(NULL,kCGScrollEventUnitLine,1,amount); if(!e) return 0;
    CGEventPost(kCGHIDEventTap,e); CFRelease(e); return 1;
#else
    (void)amount;return 0;
#endif
}
static void report_mouse(int command,double x,double y) {
    event_start(command?"command_result":"telemetry","mouse");
    printf("\"ok\":true,\"data\":{\"joystick_x\":%.4f,\"joystick_y\":%.4f,\"buttons\":[%s,%s,%s],\"position\":",x,y,held[0]?"true":"false",held[1]?"true":"false",held[2]?"true":"false");
#ifdef _WIN32
    POINT p;if(GetCursorPos(&p)) printf("{\"x\":%ld,\"y\":%ld}",p.x,p.y);else printf("null");
#elif defined(__APPLE__)
    CGEventRef e=CGEventCreate(NULL);
    if(e) {CGPoint p=CGEventGetLocation(e);printf("{\"x\":%.2f,\"y\":%.2f}",p.x,p.y);CFRelease(e);}else printf("null");
#else
    printf("null");
#endif
    puts("}}");fflush(stdout);
}
int main(int argc,char **argv) {
    setvbuf(stdout,NULL,_IOFBF,65536); double speed=900;
    signal(SIGINT,on_stop);signal(SIGTERM,on_stop);
    if(argc==3 && !strcmp(argv[1],"--speed") && number(argv[2],1,5000,&speed)) {}
    else if(argc!=1) {fprintf(stderr,"Usage: mouse [--speed pixels-per-second]\n");return 2;}
    double x=0,y=0,rx=0,ry=0; uint64_t updated=0,last=monotonic_ms(),report=0;
    while(!stopped) {
        char line[256],a[80],b[80],extra; int r=input_line(line,sizeof(line));
        if(r==-1) break;
        if(r==-2) failure("mouse","Command too long",1);
        if(r==1) {
            int ok=0;
            if(!strcmp(line,"stop")) break;
            double nx,ny;
            if(sscanf(line,"move %79s %79s %c",a,b,&extra)==2 && number(a,-1,1,&nx) && number(b,-1,1,&ny)) {
                x=nx;y=ny;
                if(fabs(x)<0.08) x=0; if(fabs(y)<0.08) y=0;
                updated=monotonic_ms(); ok=mouse_move(0,0);
                if(!ok) x=y=0;
            } else if(sscanf(line,"button %79s %79s %c",a,b,&extra)==2) {
                int button=!strcmp(a,"left")?0:!strcmp(a,"right")?1:!strcmp(a,"middle")?2:-1;
                if(button>=0 && (!strcmp(b,"down") || !strcmp(b,"up"))) {
                    ok=mouse_button(button,!strcmp(b,"down")); updated=monotonic_ms();
                }
            } else if(sscanf(line,"scroll %79s %c",a,&extra)==1) {
                double n; if(number(a,-100,100,&n) && floor(n)==n) ok=mouse_scroll((int)n);
            }
            if(ok) report_mouse(1,x,y); else failure("mouse","Invalid command or input permission denied",1);
        }
        uint64_t now=monotonic_ms();
        if(now-updated>250) {
            x=y=rx=ry=0;
            /* Release held buttons too: a lost connection must never leave a drag active. */
            for(int i=0;i<3;i++) if(held[i]) mouse_button(i,0);
        }
        double dt=fmin((double)(now-last)/1000,0.05); last=now;
        rx+=x*speed*dt; ry+=y*speed*dt;
        int dx=(int)rx,dy=(int)ry; rx-=dx; ry-=dy;
        if((dx || dy) && !mouse_move(dx,dy)) {x=y=0;failure("mouse","Mouse injection failed",0);}
        if(now>=report) {report_mouse(0,x,y);report=now+100;}
        pause_ms(8);
    }
    for(int i=0;i<3;i++) if(held[i]) mouse_button(i,0);
    return 0;
}
