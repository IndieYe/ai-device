/* Read-only native registration identity boundary. No services or writes.
 * Caller must hold management exclusion; filesystem checks aren't an ACL or
 * malicious-local-code sandbox. Production entry supplies fixed directory/root.
 *
 * Multiple translation-unit owners include this file (first-program entry and
 * install preflight), so the guard keeps single-definition semantics without
 * forcing callers to coordinate macros.
 */
#ifndef AI_DEVICE_IDENTITY_IMPLEMENTATION
#define AI_DEVICE_IDENTITY_IMPLEMENTATION 1
#import <Foundation/Foundation.h>
#include <fcntl.h>
#include <pwd.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

static BOOL integerNumber(id value, long long minimum, long long maximum) {
    if (![value isKindOfClass:[NSNumber class]] || CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID()) return NO;
    const char *kind = [value objCType];
    if (!strcmp(kind, @encode(float)) || !strcmp(kind, @encode(double))) return NO;
    long long n = [value longLongValue];
    return n >= minimum && n <= maximum;
}

static BOOL cleanString(id value, BOOL user) {
    if (![value isKindOfClass:[NSString class]] || ![value length]) return NO;
    NSCharacterSet *controls = [NSCharacterSet controlCharacterSet];
    if ([value rangeOfCharacterFromSet:controls].location != NSNotFound) return NO;
    if (user && ([value hasPrefix:@"-"] || [value rangeOfCharacterFromSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]].location != NSNotFound)) return NO;
    return YES;
}

static NSDictionary *readIdentity(const char *directory, uid_t owner) {
    struct stat parent, initial, opened, current;
    if (lstat(directory,&parent) || !S_ISDIR(parent.st_mode) || parent.st_uid != owner || (parent.st_mode & 0077)) return nil;
    NSString *path = [[NSString stringWithUTF8String:directory] stringByAppendingPathComponent:@"registration.json"];
    const char *file = [path fileSystemRepresentation];
    if (lstat(file,&initial)) return nil;
    int fd = open(file,O_RDONLY|O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC);
    if (fd < 0) return nil;
    BOOL valid = !fstat(fd,&opened) && S_ISREG(opened.st_mode) && opened.st_uid == owner &&
        !(opened.st_mode & 0077) && opened.st_nlink == 1 && opened.st_size <= 8192 &&
        opened.st_ino == initial.st_ino && opened.st_dev == initial.st_dev;
    unsigned char buffer[8193]; size_t used = 0;
    if (valid) {
        for (;;) {
            ssize_t n = read(fd,buffer+used,sizeof(buffer)-used);
            if (n < 0) {valid=NO;break;}
            if (!n) break;
            used += (size_t)n;
            if (used == sizeof(buffer)) {valid=NO;break;}
        }
    }
    close(fd);
    struct stat final_parent;
    if (!valid || lstat(file,&current) || current.st_ino != opened.st_ino || current.st_dev != opened.st_dev ||
        current.st_uid != owner || (current.st_mode & 0077) || current.st_nlink != 1 ||
        lstat(directory,&final_parent) || final_parent.st_ino != parent.st_ino ||
        final_parent.st_dev != parent.st_dev || final_parent.st_uid != owner ||
        (final_parent.st_mode & 0077)) return nil;
    NSData *data = [NSData dataWithBytes:buffer length:used];
    id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
    if (![value isKindOfClass:[NSDictionary class]]) return nil;
    NSSet *keys = [NSSet setWithArray:@[@"schema_version",@"user",@"uid",@"gid",@"home"]];
    if (![[NSSet setWithArray:[value allKeys]] isEqualToSet:keys]) return nil;
    if (!integerNumber(value[@"schema_version"],1,1) || !integerNumber(value[@"uid"],501,UINT32_MAX) ||
        !integerNumber(value[@"gid"],0,UINT32_MAX) || !cleanString(value[@"user"],YES) || !cleanString(value[@"home"],NO)) return nil;
    NSString *home=value[@"home"];
    if (![home hasPrefix:@"/"] || [home isEqualToString:@"/"] || [home isEqualToString:@"/var/root"] ||
        [[home pathComponents] containsObject:@".."] || ![[home stringByStandardizingPath] isEqualToString:home]) return nil;
    struct passwd *account = getpwnam([value[@"user"] UTF8String]);
    if (!account || account->pw_uid != [value[@"uid"] unsignedIntValue] || account->pw_gid != [value[@"gid"] unsignedIntValue] ||
        !account->pw_dir || strcmp(account->pw_dir,[home fileSystemRepresentation]) ||
        !account->pw_name || strcmp(account->pw_name,[value[@"user"] UTF8String]) ||
        !account->pw_shell || strstr(account->pw_shell,"nologin") || strstr(account->pw_shell,"false")) return nil;
    return value;
}

#include "client-macos-private-lock.h"
static int acquireManagement(const char *directory, uid_t owner) {
    return acquire_private_lock(directory, "management.lock", owner);
}

/* -1 unknown/invalid, 0 explicitly unregistered, 1 valid registration.
 * Requires an already acquired management fd; missing directory/lock is NOT
 * first-install authorization. Caller must still check legacy conflicts.
 */
static inline int registration_state(const char *directory,uid_t owner,int management_fd) {
    if (!private_lock_matches(management_fd,directory,"management.lock",owner)) return -1;
    NSString *record=[[NSString stringWithUTF8String:directory] stringByAppendingPathComponent:@"registration.json"];
    const char *path=[record fileSystemRepresentation];
    struct stat info;
    if (!path) return -1;
    if (lstat(path,&info)) {
        int missing=errno==ENOENT;
        return missing && private_lock_matches(management_fd,directory,"management.lock",owner) ? 0 : -1;
    }
    if (!readIdentity(directory,owner)) return -1;
    return private_lock_matches(management_fd,directory,"management.lock",owner) ? 1 : -1;
}

static NSDictionary *managedPaths(NSDictionary *identity) {
    /* Only call after readIdentity succeeded. No environment/console lookup,
     * user-controlled install prefix, or second mutable identity registry. */
    NSString *directory = [identity[@"home"] stringByAppendingPathComponent:@"Library/Application Support/ai-device-client"];
    return @{
        @"program": @"/opt/ai-device-client",
        @"management": @"/Library/Application Support/ai-device-client-management",
        @"management_owner": @0,
        @"runtime_owner": identity[@"uid"],
        @"directory": directory,
        @"runtime_lock": [directory stringByAppendingPathComponent:@"runtime.lock"],
        @"start_gate": [directory stringByAppendingPathComponent:@"start.lock"],
        @"config": [directory stringByAppendingPathComponent:@"client.json"],
        @"plist": [identity[@"home"] stringByAppendingPathComponent:@"Library/LaunchAgents/local.ai-device.client.plist"],
        @"service": [NSString stringWithFormat:@"gui/%@/local.ai-device.client", identity[@"uid"]]
    };
}

#ifndef IDENTITY_LIBRARY
int main(int argc, char **argv) {
    (void)argv;
    @autoreleasepool {
        if (argc != 1 || geteuid() != 0) return 64;
        const char *directory = "/Library/Application Support/ai-device-client-management";
        int management = acquireManagement(directory,0);
        if (management < 0) return 70;
        NSDictionary *identity = readIdentity(directory,0);
        if (!identity || !managedPaths(identity)) {close(management);return 70;}
        close(management);
        puts("REGISTERED_ACCOUNT_MATCHED_NOT_INSTALLED");
        return 0;
    }
}
#endif
#endif /* AI_DEVICE_IDENTITY_IMPLEMENTATION */
