/* Native fixed-identity, fixed-path read-only install preflight.
 * NO program writes, service calls, stop requests or replacement permission.
 * Does not support first registration; registration must preprovision locks.
 * A busy runtime is refused, not stopped. Future lifecycle orchestration must
 * hold management/start through instance-bound exit and verified publication.
 */
#define IDENTITY_LIBRARY
#include "client-macos-identity.m"

struct managed_guards {
    int management; int start; int runtime;
    NSDictionary *__strong identity;
};

static void release_guards(struct managed_guards *guards) {
    if (guards->runtime >= 0) close(guards->runtime);
    if (guards->start >= 0) close(guards->start);
    if (guards->management >= 0) close(guards->management);
    guards->runtime = guards->start = guards->management = -1;
    guards->identity = nil;
}

static NSDictionary *prepare_start_barrier(const char *management, uid_t manager,
                                        struct managed_guards *guards) {
    guards->management = guards->start = guards->runtime = -1;
    guards->identity = nil;
    guards->management = acquireManagement(management, manager);
    if (guards->management < 0) return nil;
    NSDictionary *identity = readIdentity(management, manager);
    if (!identity) {release_guards(guards);return nil;}
    guards->identity = identity;
    NSDictionary *paths = managedPaths(identity);
    const char *directory = [paths[@"directory"] fileSystemRepresentation];
    uid_t uid = [paths[@"runtime_owner"] unsignedIntValue];
    guards->start = acquire_private_lock(directory, "start.lock", uid);
    if (guards->start < 0) {release_guards(guards);return nil;}
    return paths;
}

static int acquire_registered_runtime(const char *management, uid_t manager,
                                      NSDictionary *paths, struct managed_guards *guards) {
    if (guards->management < 0 || guards->start < 0 || guards->runtime >= 0) return 0;
    if (!private_lock_matches(guards->management,management,"management.lock",manager) ||
        !private_lock_matches(guards->start,[paths[@"directory"] fileSystemRepresentation],"start.lock",
                              [paths[@"runtime_owner"] unsignedIntValue])) return 0;
    NSDictionary *current = readIdentity(management, manager);
    if (!current || ![current isEqualToDictionary:guards->identity] ||
        ![managedPaths(current) isEqualToDictionary:paths]) return 0;
    guards->runtime = acquire_private_lock(
        [paths[@"directory"] fileSystemRepresentation], "runtime.lock",
        [paths[@"runtime_owner"] unsignedIntValue]);
    return guards->runtime >= 0;
}

static NSDictionary *prepare_registered(const char *management, uid_t manager,
                                        struct managed_guards *guards) {
    NSDictionary *paths = prepare_start_barrier(management, manager, guards);
    if (!paths) return nil;
    if (!acquire_registered_runtime(management, manager, paths, guards)) {
        release_guards(guards);return nil;
    }
    return paths;
}

#ifndef INSTALL_PREFLIGHT_LIBRARY
int main(int argc, char **argv) {
    (void)argv;
    @autoreleasepool {
        if (argc != 1 || geteuid() != 0) return 64;
        struct managed_guards guards;
        NSDictionary *paths = prepare_registered(
            "/Library/Application Support/ai-device-client-management", 0, &guards);
        if (!paths) return 70;
        /* No writer is connected here. Release before returning diagnostic. */
        release_guards(&guards);
        puts("REGISTERED_LOCKS_CHECKED_NOT_INSTALLED");
        return 0;
    }
}
#endif
