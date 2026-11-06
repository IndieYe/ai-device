/* Include after lease-request.m. One connected status query, never a lease.
 * Exact build identity is supplied from trusted target metadata by caller.
 */
static int ready_request_connected(int fd,uid_t expected_uid,NSString *version,
                                   NSString *build,double deadline) {
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
    if (!response || !lease_true(response[@"ready"]) ||
        ![response[@"upgrade_id"] isEqual:nonce] ||
        ![reported_pid isKindOfClass:[NSNumber class]] ||
        CFGetTypeID((__bridge CFTypeRef)reported_pid)==CFBooleanGetTypeID() ||
        CFNumberIsFloatType((__bridge CFNumberRef)reported_pid) ||
        [reported_pid longLongValue]!=pid ||
        ![response[@"runtime_version"] isEqual:@{@"version":version,@"build_id":build}]) return 0;
    /* Server normally closes after status: inspect original PID, not socket discovery. */
    int matches=lease_process(pid,&after) && after.seconds==original.seconds && after.micros==original.micros;
    double observed=lease_clock();
    return matches && isfinite(observed) && observed>=0 && isfinite(deadline) && observed<deadline;
}
