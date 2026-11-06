/* Internal read-only observation under caller-owned registered guards.
 * Include after install-preflight and the embedded resource verifier.
 * Never starts/stops services, publishes, rolls back, or reports readiness.
 * Nonnegative descriptors are a caller-lifetime check, NOT a lock proof.
 */
#ifndef CLIENT_MACOS_RECOVERY_CHECK_H
#define CLIENT_MACOS_RECOVERY_CHECK_H
#include "client-macos-private-lock.h"
#include "client-macos-tree-owner.h"
static int registered_guards_current(const char *management, uid_t manager,
                                   NSDictionary *paths, struct managed_guards *guards) {
    if (!paths || !guards->identity || guards->management < 0 ||
        guards->start < 0 || guards->runtime < 0) return 0;
    if (fcntl(guards->management,F_GETFD) < 0 || fcntl(guards->start,F_GETFD) < 0 ||
        fcntl(guards->runtime,F_GETFD) < 0) return 0;
    NSDictionary *current = readIdentity(management,manager);
    if (!current || ![current isEqualToDictionary:guards->identity] ||
        ![managedPaths(current) isEqualToDictionary:paths]) return 0;
    const char *directory=[paths[@"directory"] fileSystemRepresentation];
    uid_t uid=[paths[@"runtime_owner"] unsignedIntValue];
    return private_lock_matches(guards->management,management,"management.lock",manager) &&
        private_lock_matches(guards->start,directory,"start.lock",uid) &&
        private_lock_matches(guards->runtime,directory,"runtime.lock",uid);
}

static int registered_target_exact(const char *management, uid_t manager,
                                   NSDictionary *paths, struct managed_guards *guards) {
    if (!registered_guards_current(management,manager,paths,guards)) return 0;
    const char *target = [paths[@"program"] fileSystemRepresentation];
    if (!target || !verify_resource_tree(target) ||
        !tree_owner_valid(target,[paths[@"management_owner"] unsignedIntValue],0)) return 0;
    return registered_guards_current(management,manager,paths,guards);
}
#endif
