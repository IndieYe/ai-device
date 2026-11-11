/* Connected-fd only internal protocol. No connect/retry, signals or service calls.
 * Caller owns fd, absolute monotonic deadline, management/start guards.
 * ACCEPTED is not original-process exit or replacement permission.
 */
#import <Foundation/Foundation.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/proc_info.h>
#include <libproc.h>
#include <poll.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>
#include <limits.h>
#include <math.h>

enum lease_request_result { LEASE_UNKNOWN=-1, LEASE_BUSY=0, LEASE_ACCEPTED=1 };
struct lease_original { pid_t pid; uint64_t seconds; uint64_t micros; };
static double lease_clock(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC,&now)) return -1;
    return now.tv_sec+now.tv_nsec/1e9;
}
static int lease_wait(int fd,short events,double deadline) {
    for (;;) {
        double now=lease_clock(), remaining=deadline-now;
        if (now<0 || !isfinite(remaining) || remaining<=0 || remaining>INT_MAX/1000.0) return 0;
        struct pollfd input={fd,events,0};
        int result=poll(&input,1,(int)(remaining*1000)+1);
        if (result<0 && errno==EINTR) continue;
        double observed=lease_clock();
        if (!isfinite(observed) || observed<0 || observed>=deadline) return 0;
        return result>0 && (input.revents & events);
    }
}
static int lease_bytes(int fd,void *data,size_t size,int writing,double deadline) {
    size_t offset=0;
    while (offset<size) {
        if (!lease_wait(fd,writing?POLLOUT:POLLIN,deadline)) return 0;
        ssize_t count=writing ? send(fd,(char *)data+offset,size-offset,MSG_DONTWAIT) :
            recv(fd,(char *)data+offset,size-offset,MSG_DONTWAIT);
        if (count<0 && (errno==EINTR || errno==EAGAIN || errno==EWOULDBLOCK)) continue;
        if (count<=0) return 0;
        offset+=(size_t)count;
    }
    return 1;
}
static NSDictionary *lease_json(int fd,double deadline) {
    unsigned char data[4096];size_t length=0;
    while (length<sizeof(data)) {
        if (!lease_bytes(fd,&data[length],1,0,deadline)) return nil;
        if (data[length++]=='\n') {
            id result=[NSJSONSerialization JSONObjectWithData:[NSData dataWithBytes:data length:length]
                                                    options:0 error:NULL];
            return [result isKindOfClass:[NSDictionary class]] ? result : nil;
        }
    }
    return nil;
}
static int lease_process(pid_t pid,struct lease_original *identity) {
    struct proc_bsdinfo info;
    if (pid<=0 || proc_pidinfo(pid,PROC_PIDTBSDINFO,0,&info,sizeof(info)) != sizeof(info)) return 0;
    identity->pid=pid;identity->seconds=info.pbi_start_tvsec;identity->micros=info.pbi_start_tvusec;
    return 1;
}
/* 1 original gone/reused, 0 still same, -1 unknown. No signals. */
static int lease_original_state(const struct lease_original *original) {
    if (!original || original->pid<=0 || !original->seconds || original->micros>=1000000)
        return -1;
    struct proc_bsdinfo info;
    errno=0;
    int result=proc_pidinfo(original->pid,PROC_PIDTBSDINFO,0,&info,sizeof(info));
    if (result==sizeof(info))
        return info.pbi_start_tvsec!=original->seconds || info.pbi_start_tvusec!=original->micros;
    return result==0 && errno==ESRCH ? 1 : -1;
}
static int lease_wait_original_exit(const struct lease_original *original,double deadline) {
    for (;;) {
        double now=lease_clock();
        if (!isfinite(now) || now<0 || !isfinite(deadline) || now>=deadline) return -1;
        int state=lease_original_state(original);
        double observed=lease_clock();
        if (!isfinite(observed) || observed<0 || observed>=deadline) return -1;
        if (state) return state;
        double remaining=deadline-observed;
        struct timespec pause={0,(long)(fmin(remaining,0.01)*1e9)};
        if (nanosleep(&pause,NULL) && errno!=EINTR) return -1;
    }
}
static int lease_true(id value) { return value==(__bridge id)kCFBooleanTrue; }
static int lease_false(id value) { return value==(__bridge id)kCFBooleanFalse; }
static int lease_request_connected(int fd,uid_t expected_uid,double deadline,struct lease_original *original) {
    uid_t uid;gid_t gid;pid_t pid=0;socklen_t size=sizeof(pid);
    int no_signal=1;
    if (!original || setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&no_signal,sizeof(no_signal)) ||
        getpeereid(fd,&uid,&gid) || uid!=expected_uid ||
        getsockopt(fd,SOL_LOCAL,LOCAL_PEERPID,&pid,&size) || size!=sizeof(pid) ||
        !lease_process(pid,original)) return LEASE_UNKNOWN;
    char acquire[]="{\"action\":\"acquire\"}\n";
    if (!lease_bytes(fd,acquire,sizeof(acquire)-1,1,deadline)) return LEASE_UNKNOWN;
    NSDictionary *response=lease_json(fd,deadline);
    if (!response) return LEASE_UNKNOWN;
    if (lease_false(response[@"acquired"])) return LEASE_BUSY;
    if (!lease_true(response[@"acquired"])) return LEASE_UNKNOWN;
    unsigned char challenge[33],echo[32];challenge[0]='P';
    arc4random_buf(challenge+1,32);
    if (!lease_bytes(fd,challenge,sizeof(challenge),1,deadline) ||
        !lease_bytes(fd,echo,sizeof(echo),0,deadline) || memcmp(challenge+1,echo,32)) return LEASE_UNKNOWN;
    struct lease_original after;
    if (!lease_process(pid,&after) || after.seconds!=original->seconds || after.micros!=original->micros)
        return LEASE_UNKNOWN;
    unsigned char exit_request='X';
    if (!lease_bytes(fd,&exit_request,1,1,deadline)) return LEASE_UNKNOWN;
    response=lease_json(fd,deadline);
    return response && lease_true(response[@"exit_committed"]) ? LEASE_ACCEPTED : LEASE_UNKNOWN;
}
