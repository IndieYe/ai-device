/* Include after install-preflight and lease-request.m. No service operations.
 * Requires caller-owned management/start guards, not runtime. Failures retain
 * guards; caller decides explicit cleanup. Never retries requests or connects.
 */
#include <fcntl.h>
#include "client-macos-registered-connect.h"
#include "client-macos-agent-check.h"
static int registered_exit_current(const char *management,uid_t manager,
                                  NSDictionary *paths,struct managed_guards *guards) {
    if (!paths || !guards->identity || guards->management<0 || guards->start<0 ||
        guards->runtime>=0 || fcntl(guards->management,F_GETFD)<0 ||
        !private_lock_matches(guards->management,management,"management.lock",manager) ||
        !private_lock_matches(guards->start,[paths[@"directory"] fileSystemRepresentation],"start.lock",
                              [paths[@"runtime_owner"] unsignedIntValue])) return 0;
    NSDictionary *identity=readIdentity(management,manager);
    return identity && [identity isEqualToDictionary:guards->identity] &&
        [managedPaths(identity) isEqualToDictionary:paths] && validate_registered_agent(identity,paths);
}
static int exit_registered_original(const char *management,uid_t manager,
                                    NSDictionary *paths,struct managed_guards *guards,
                                    double deadline) {
    if (!registered_exit_current(management,manager,paths,guards)) return LEASE_UNKNOWN;
    int fd=connect_registered_socket(paths,deadline);
    if (fd<0) return LEASE_UNKNOWN;
    if (!registered_exit_current(management,manager,paths,guards)) {
        close(fd);return LEASE_UNKNOWN;
    }
    struct lease_original original;
    int result=lease_request_connected(fd,[paths[@"runtime_owner"] unsignedIntValue],deadline,&original);
    if (result==LEASE_ACCEPTED && lease_wait_original_exit(&original,deadline)!=1) result=LEASE_UNKNOWN;
    close(fd);
    if (result!=LEASE_ACCEPTED) return result;
    if (!registered_exit_current(management,manager,paths,guards) ||
        !acquire_registered_runtime(management,manager,paths,guards)) return LEASE_UNKNOWN;
    return LEASE_ACCEPTED; /* Runtime lock acquired, NOT launchd unloaded/ready. */
}
