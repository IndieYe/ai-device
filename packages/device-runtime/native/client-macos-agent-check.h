/* Exact disk definition only, NOT launchd loaded-configuration evidence.
 * Include after identity.m for integerNumber helper. No service calls/writes.
 */
#ifndef CLIENT_MACOS_AGENT_CHECK_H
#define CLIENT_MACOS_AGENT_CHECK_H
static int validate_registered_agent(NSDictionary *identity,NSDictionary *paths) {
    const char *path=[paths[@"plist"] fileSystemRepresentation];
    if (!path) return 0;
    struct stat first,opened,after;
    if (lstat(path,&first)) return 0;
    int fd=open(path,O_RDONLY|O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC);
    if (fd<0) return 0;
    int valid=!fstat(fd,&opened) && S_ISREG(opened.st_mode) &&
        opened.st_uid==[identity[@"uid"] unsignedIntValue] && !(opened.st_mode & 0077) &&
        opened.st_nlink==1 && opened.st_size<=65536 &&
        opened.st_dev==first.st_dev && opened.st_ino==first.st_ino;
    unsigned char buffer[65537];size_t used=0;
    while (valid) {
        ssize_t n=read(fd,buffer+used,sizeof(buffer)-used);
        if (n<0 && errno==EINTR) continue;
        if (n<0) {valid=0;break;}
        if (!n) break;
        used+=(size_t)n;
        if (used==sizeof(buffer)) {valid=0;break;}
    }
    close(fd);
    if (!valid || lstat(path,&after) || !S_ISREG(after.st_mode) ||
        after.st_dev!=opened.st_dev || after.st_ino!=opened.st_ino ||
        after.st_uid!=opened.st_uid || (after.st_mode & 0077) || after.st_nlink!=1) return 0;
    id value=[NSPropertyListSerialization propertyListWithData:[NSData dataWithBytes:buffer length:used]
                                                     options:NSPropertyListImmutable format:NULL error:NULL];
    if (![value isKindOfClass:[NSDictionary class]]) return 0;
    NSDictionary *definition=value;
    NSString *log=[identity[@"home"] stringByAppendingPathComponent:@"Library/Logs/ai-device-client/client.log"];
    NSDictionary *expected=@{@"Label":@"local.ai-device.client",
        @"ProgramArguments":@[@"/opt/ai-device-client/bin/ai-device-client-launcher"],
        @"RunAtLoad":@YES,@"KeepAlive":@{@"SuccessfulExit":@NO},
        @"ThrottleInterval":@30,@"AbandonProcessGroup":@YES,@"ProcessType":@"Background",
        @"Umask":@63,@"StandardOutPath":log,@"StandardErrorPath":log};
    if (![definition isEqualToDictionary:expected] ||
        definition[@"RunAtLoad"]!=(__bridge id)kCFBooleanTrue ||
        definition[@"AbandonProcessGroup"]!=(__bridge id)kCFBooleanTrue ||
        ![definition[@"KeepAlive"] isKindOfClass:[NSDictionary class]] ||
        definition[@"KeepAlive"][@"SuccessfulExit"]!=(__bridge id)kCFBooleanFalse ||
        !integerNumber(definition[@"ThrottleInterval"],30,30) ||
        !integerNumber(definition[@"Umask"],63,63)) return 0;
    return 1;
}
#endif
