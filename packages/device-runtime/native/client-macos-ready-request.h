/* Include after lease-request.m. One connected status query, never a lease.
 * Exact build identity is supplied from trusted target metadata by caller.
 *
 * The return value stays 1-on-success / 0-on-any-failure, which is what every
 * caller and the native tests already assert. The *cause* travels through an
 * optional out parameter instead of the result, so a caller that only needs a
 * verdict passes NULL and one that logs passes a slot.
 */
#include "client-macos-ready-cause.h"

/* Returns 1 when the target is serving exactly this version and build, 0
 * otherwise. `cause` is optional: pass NULL when the caller only needs the
 * verdict, which is every caller except the one that logs a give-up. */
static int ready_request_connected(int fd,uid_t expected_uid,NSString *version,
                                   NSString *build,double deadline,ready_cause *cause) {
    if (cause) *cause=READY_NO_RESPONSE;
    if (![version isKindOfClass:[NSString class]] || !version.length ||
        ![build isKindOfClass:[NSString class]] || build.length!=71 ||
        ![build hasPrefix:@"sha256:"]) return 0;
    NSString *digits=[build substringFromIndex:7];
    NSCharacterSet *invalid=[[NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"] invertedSet];
    if ([digits rangeOfCharacterFromSet:invalid].location!=NSNotFound) return 0;
    uid_t uid;gid_t gid;pid_t pid=0;socklen_t size=sizeof(pid);int no_signal=1;
    struct lease_original original,after;
    if (setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&no_signal,sizeof(no_signal)) ||
        getpeereid(fd,&uid,&gid) || uid!=expected_uid ||
        getsockopt(fd,SOL_LOCAL,LOCAL_PEERPID,&pid,&size) || size!=sizeof(pid) ||
        !lease_process(pid,&original)) return 0;
    NSString *nonce=[[NSUUID UUID] UUIDString];
    NSData *request=[NSJSONSerialization dataWithJSONObject:@{@"action":@"status",@"upgrade_id":nonce}
                                                    options:0 error:NULL];
    if (!request) return 0;
    NSMutableData *line=[request mutableCopy];unsigned char newline='\n';
    [line appendBytes:&newline length:1];
    if (!lease_bytes(fd,(void *)line.bytes,line.length,1,deadline)) return 0;
    NSDictionary *response=lease_json(fd,deadline);
    id reported_pid=response[@"pid"];
    if (!response) return 0;
    if (!lease_true(response[@"ready"])) {
        if (cause) *cause=READY_REPORTED_NOT_READY;
        return 0;
    }
    if (![response[@"upgrade_id"] isEqual:nonce]) {
        if (cause) *cause=READY_NONCE_MISMATCH;
        return 0;
    }
    if (![reported_pid isKindOfClass:[NSNumber class]] ||
        CFGetTypeID((__bridge CFTypeRef)reported_pid)==CFBooleanGetTypeID() ||
        CFNumberIsFloatType((__bridge CFNumberRef)reported_pid) ||
        [reported_pid longLongValue]!=pid) {
        if (cause) *cause=READY_PID_MISMATCH;
        return 0;
    }
    /* The case worth naming in detail: the client is up and answering, but its
     * runtime digest is not the one this package expects. That means the
     * installed bytes disagree with the recorded identity -- a packaging
     * defect, not a slow start. */
    if (![response[@"runtime_version"] isEqual:@{@"version":version,@"build_id":build}]) {
        if (cause) *cause=READY_VERSION_MISMATCH;
        return 0;
    }
    /* Server normally closes after status: inspect original PID, not socket discovery. */
    int matches=lease_process(pid,&after) && after.seconds==original.seconds && after.micros==original.micros;
    double observed=lease_clock();
    if (!(matches && isfinite(observed) && observed>=0 && isfinite(deadline) && observed<deadline)) {
        if (cause) *cause=READY_PROCESS_CHANGED;
        return 0;
    }
    if (cause) *cause=READY_CAUSE_NONE;
    return 1;
}
