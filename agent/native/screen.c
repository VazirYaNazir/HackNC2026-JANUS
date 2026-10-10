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
static unsigned interval_arg(const char *s,unsigned low,unsigned high) {
    double n;
    if(!number(s,low,high,&n) || floor(n)!=n) {
        fprintf(stderr,"Invalid integer interval/limit: %s\n",s); exit(2);
    }
    return (unsigned)n;
}
static void base64(const unsigned char *p,size_t n) {
    const char *abc="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    char buffer[8192];size_t used=0;
    for(size_t i=0;i<n;i+=3) {
        unsigned x=(unsigned)p[i]<<16;
        if(i+1<n) x|=(unsigned)p[i+1]<<8;
        if(i+2<n) x|=p[i+2];
        buffer[used++]=abc[x>>18];buffer[used++]=abc[(x>>12)&63];
        buffer[used++]=i+1<n?abc[(x>>6)&63]:'=';buffer[used++]=i+2<n?abc[x&63]:'=';
        if(used==sizeof(buffer)) {fwrite(buffer,1,used,stdout);used=0;}
    }
    if(used) fwrite(buffer,1,used,stdout);
}
#include <signal.h>
static volatile sig_atomic_t stopped;
static void on_stop(int n) {(void)n;stopped=1;}
#ifdef _WIN32
#include <initguid.h>
#include <wincodec.h>
static int capture(unsigned limit,unsigned char **out,size_t *size,unsigned *width,unsigned *height) {
    int sw=GetSystemMetrics(SM_CXSCREEN),sh=GetSystemMetrics(SM_CYSCREEN);if(sw<=0 || sh<=0) return 0;
    double scale=fmin(1.0,fmin((double)limit/sw,(double)limit/sh));
    *width=(unsigned)fmax(1,floor(sw*scale));*height=(unsigned)fmax(1,floor(sh*scale));
    UINT stride=(*width*3+3)&~3U;size_t bytes=(size_t)stride**height;
    HDC desktop=GetDC(NULL),dc=desktop?CreateCompatibleDC(desktop):NULL;
    if(!dc) {if(desktop) ReleaseDC(NULL,desktop);return 0;}
    BITMAPINFO info={0};info.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);info.bmiHeader.biWidth=*width;
    info.bmiHeader.biHeight=-(LONG)*height;info.bmiHeader.biPlanes=1;info.bmiHeader.biBitCount=24;info.bmiHeader.biCompression=BI_RGB;
    void *pixels=NULL;HBITMAP bitmap=CreateDIBSection(desktop,&info,DIB_RGB_COLORS,&pixels,NULL,0);
    if(!bitmap) {DeleteDC(dc);ReleaseDC(NULL,desktop);return 0;}
    HGDIOBJ old=SelectObject(dc,bitmap);SetStretchBltMode(dc,HALFTONE);SetBrushOrgEx(dc,0,0,NULL);
    int copied=StretchBlt(dc,0,0,*width,*height,desktop,0,0,sw,sh,SRCCOPY|CAPTUREBLT);GdiFlush();
    IWICImagingFactory *factory=NULL;IWICBitmapEncoder *encoder=NULL;IWICBitmapFrameEncode *frame=NULL;IStream *stream=NULL;IPropertyBag2 *options=NULL;
    HRESULT hr=copied?CoCreateInstance(&CLSID_WICImagingFactory,NULL,CLSCTX_INPROC_SERVER,&IID_IWICImagingFactory,(void **)&factory):E_FAIL;
    if(SUCCEEDED(hr)) hr=CreateStreamOnHGlobal(NULL,TRUE,&stream);
    if(SUCCEEDED(hr)) hr=IWICImagingFactory_CreateEncoder(factory,&GUID_ContainerFormatJpeg,NULL,&encoder);
    if(SUCCEEDED(hr)) hr=IWICBitmapEncoder_Initialize(encoder,stream,WICBitmapEncoderNoCache);
    if(SUCCEEDED(hr)) hr=IWICBitmapEncoder_CreateNewFrame(encoder,&frame,&options);
    if(SUCCEEDED(hr)) {
        PROPBAG2 property={0};property.pstrName=L"ImageQuality";VARIANT quality;VariantInit(&quality);
        V_VT(&quality)=VT_R4;V_R4(&quality)=0.6f;IPropertyBag2_Write(options,1,&property,&quality);
        hr=IWICBitmapFrameEncode_Initialize(frame,options);
    }
    if(SUCCEEDED(hr)) hr=IWICBitmapFrameEncode_SetSize(frame,*width,*height);
    WICPixelFormatGUID format=GUID_WICPixelFormat24bppBGR;
    if(SUCCEEDED(hr)) hr=IWICBitmapFrameEncode_SetPixelFormat(frame,&format);
    if(SUCCEEDED(hr) && !IsEqualGUID(&format,&GUID_WICPixelFormat24bppBGR)) hr=E_FAIL;
    if(SUCCEEDED(hr)) hr=IWICBitmapFrameEncode_WritePixels(frame,*height,stride,(UINT)bytes,pixels);
    if(SUCCEEDED(hr)) hr=IWICBitmapFrameEncode_Commit(frame);
    if(SUCCEEDED(hr)) hr=IWICBitmapEncoder_Commit(encoder);
    if(SUCCEEDED(hr)) {
        HGLOBAL memory=NULL;STATSTG stat={0};hr=IStream_Stat(stream,&stat,STATFLAG_NONAME);
        if(SUCCEEDED(hr) && stat.cbSize.QuadPart<=4*1024*1024) {
            hr=GetHGlobalFromStream(stream,&memory);
            if(SUCCEEDED(hr)) {
                void *p=GlobalLock(memory);*size=(size_t)stat.cbSize.QuadPart;*out=malloc(*size);
                if(!p || !*out) hr=E_OUTOFMEMORY;else memcpy(*out,p,*size);
                if(p) GlobalUnlock(memory);
            }
        } else hr=E_FAIL;
    }
    if(options) IPropertyBag2_Release(options);if(frame) IWICBitmapFrameEncode_Release(frame);
    if(encoder) IWICBitmapEncoder_Release(encoder);if(stream) IStream_Release(stream);if(factory) IWICImagingFactory_Release(factory);
    SelectObject(dc,old);DeleteObject(bitmap);DeleteDC(dc);ReleaseDC(NULL,desktop);
    if(FAILED(hr)) {free(*out);*out=NULL;return 0;}return 1;
}
#elif defined(__APPLE__)
#include <CoreGraphics/CoreGraphics.h>
#include <ImageIO/ImageIO.h>
#include <spawn.h>
#include <signal.h>
#include <sys/wait.h>
extern char **environ;
static int capture(unsigned limit,unsigned char **out,size_t *size,unsigned *width,unsigned *height) {
    if(!CGPreflightScreenCaptureAccess()) return 0;
    char path[]="/tmp/janus-screen-XXXXXX";int fd=mkstemp(path);if(fd<0) return 0;close(fd);
    char *argv[]={"/usr/sbin/screencapture","-x","-m","-t","png",path,NULL};
    pid_t child;int status=0;
    if(posix_spawn(&child,argv[0],NULL,NULL,argv,environ)!=0) {unlink(path);return 0;}
    uint64_t deadline=monotonic_ms()+5000;pid_t r;
    while((r=waitpid(child,&status,WNOHANG))==0 && !stopped && monotonic_ms()<deadline) pause_ms(10);
    if(r==0) {kill(child,SIGKILL);waitpid(child,&status,0);unlink(path);return 0;}
    if(r<0 || !WIFEXITED(status) || WEXITSTATUS(status)) {unlink(path);return 0;}
    CFURLRef url=CFURLCreateFromFileSystemRepresentation(NULL,(const UInt8 *)path,strlen(path),false);
    CGImageSourceRef source=url?CGImageSourceCreateWithURL(url,NULL):NULL;
    CGImageRef image=source?CGImageSourceCreateImageAtIndex(source,0,NULL):NULL;
    if(source) CFRelease(source);if(url) CFRelease(url);unlink(path);if(!image) return 0;
    size_t sw=CGImageGetWidth(image),sh=CGImageGetHeight(image);
    double scale=fmin(1.0,fmin((double)limit/sw,(double)limit/sh));
    *width=(unsigned)fmax(1,floor(sw*scale));*height=(unsigned)fmax(1,floor(sh*scale));
    CGColorSpaceRef colors=CGColorSpaceCreateDeviceRGB();
    CGContextRef ctx=CGBitmapContextCreate(NULL,*width,*height,8,*width*4,colors,kCGImageAlphaNoneSkipLast);
    CGColorSpaceRelease(colors);if(!ctx) {CGImageRelease(image);return 0;}
    CGContextSetInterpolationQuality(ctx,kCGInterpolationHigh);CGContextDrawImage(ctx,CGRectMake(0,0,*width,*height),image);CGImageRelease(image);
    image=CGBitmapContextCreateImage(ctx);CGContextRelease(ctx);if(!image) return 0;
    CFMutableDataRef data=CFDataCreateMutable(NULL,0);
    CGImageDestinationRef destination=data?CGImageDestinationCreateWithData(data,CFSTR("public.jpeg"),1,NULL):NULL;
    double q=0.6;CFNumberRef quality=CFNumberCreate(NULL,kCFNumberDoubleType,&q);
    const void *key=kCGImageDestinationLossyCompressionQuality,*val=quality;
    CFDictionaryRef opts=CFDictionaryCreate(NULL,&key,&val,1,&kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
    int ok=0;
    if(destination) {
        CGImageDestinationAddImage(destination,image,opts);
        if(CGImageDestinationFinalize(destination)) {
            *size=(size_t)CFDataGetLength(data);
            if(*size<=4*1024*1024 && (*out=malloc(*size))) {memcpy(*out,CFDataGetBytePtr(data),*size);ok=1;}
        }
        CFRelease(destination);
    }
    if(opts) CFRelease(opts);if(quality) CFRelease(quality);if(data) CFRelease(data);CGImageRelease(image);return ok;
}
#else
static int capture(unsigned limit,unsigned char **out,size_t *n,unsigned *w,unsigned *h) {(void)limit;(void)out;(void)n;(void)w;(void)h;return 0;}
#endif
int main(int argc,char **argv) {
    setvbuf(stdout,NULL,_IOFBF,65536);unsigned interval=1000,limit=960;int once=0;
    signal(SIGINT,on_stop);signal(SIGTERM,on_stop);
    for(int i=1;i<argc;i++) {
        if(!strcmp(argv[i],"--once")) once=1;
        else if(!strcmp(argv[i],"--interval") && i+1<argc) interval=interval_arg(argv[++i],200,60000);
        else if(!strcmp(argv[i],"--max-width") && i+1<argc) limit=interval_arg(argv[++i],64,1920);
        else {fprintf(stderr,"Usage: screen [--once] [--interval ms] [--max-width pixels]\n");return 2;}
    }
#ifdef _WIN32
    SetProcessDPIAware();if(FAILED(CoInitializeEx(NULL,COINIT_MULTITHREADED))) return 1;
#endif
    int failed=0;
    do {
        uint64_t start=monotonic_ms();unsigned char *jpeg=NULL;size_t size=0;unsigned w=0,h=0;
        if(!capture(limit,&jpeg,&size,&w,&h)) {
            failure("screen","Capture failed: requires an interactive desktop and screen recording access on macOS",0);failed=1;
        } else {
            failed=0;event_start("telemetry","screen");
            printf("\"data\":{\"mime\":\"image/jpeg\",\"width\":%u,\"height\":%u,\"display\":\"primary\",\"base64\":\"",w,h);
            base64(jpeg,size);puts("\"}}");fflush(stdout);free(jpeg);
        }
        uint64_t elapsed=monotonic_ms()-start;
        while(!once && !stopped && elapsed<interval) {pause_ms(10);elapsed=monotonic_ms()-start;}
    } while(!once && !stopped);
#ifdef _WIN32
    CoUninitialize();
#endif
    return failed?1:0;
}
