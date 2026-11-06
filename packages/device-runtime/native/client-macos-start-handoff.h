/* Internal transition only. No launchctl/start or loaded-definition proof.
 * Include after recovery-check and ready-request. Caller owns all lifetimes.
 */
#include "client-macos-agent-check.h"
static int registered_management_current(const char *management,uid_t manager,
                                         NSDictionary *paths,struct managed_guards *guards) {
    if (!paths || !guards->identity || guards->management<0 ||
        !private_lock_matches(guards->management,management,"management.lock",manager)) return 0;
    NSDictionary *identity=readIdentity(management,manager);
    return identity && [identity isEqualToDictionary:guards->identity] &&
        [managedPaths(identity) isEqualToDictionary:paths] && validate_registered_agent(identity,paths);
}
/* Stricter than registered_management_current and deliberately still bounded:
 * the lock object must match AND a second opener must be excluded, so a lock
 * that is observably FREE is rejected. It cannot prove WHICH process holds it
 * (a lock released here and taken elsewhere looks identical through this
 * check), and a shared holder also blocks the probe. Use it to refuse an
 * obviously abandoned lock, never as an ownership or exclusivity proof. */
static inline int registered_management_confirmed(const char *management,uid_t manager,
                                                 NSDictionary *paths,struct managed_guards *guards) {
    if (!registered_management_current(management,manager,paths,guards)) return 0;
    return private_lock_held(guards->management,management,"management.lock",manager);
}
static int handoff_registered_start(const char *management,uid_t manager,
                                    NSDictionary *paths,struct managed_guards *guards) {
    if (!registered_guards_current(management,manager,paths,guards) ||
        !registered_management_current(management,manager,paths,guards)) return 0;
    /* Keep start exclusive until runtime is closed. Never drop management. */
    int runtime=guards->runtime;guards->runtime=-1;
    if (close(runtime)) return 0;
    int start=guards->start;guards->start=-1;
    if (close(start)) return 0;
    return registered_management_current(management,manager,paths,guards);
}
static int ready_registered_connected(int fd,const char *management,uid_t manager,
                                      NSDictionary *paths,struct managed_guards *guards,
                                      NSString *version,NSString *build,double deadline) {
    if (guards->start>=0 || guards->runtime>=0 ||
        !registered_management_current(management,manager,paths,guards)) return 0;
    if (!ready_request_connected(fd,[paths[@"runtime_owner"] unsignedIntValue],version,build,deadline)) return 0;
    return registered_management_current(management,manager,paths,guards);
}

#include "client-macos-registered-connect.h"
static int query_registered_ready(const char *management,uid_t manager,
                                   NSDictionary *paths,struct managed_guards *guards,
                                   NSString *version,NSString *build,double deadline) {
    if (guards->start>=0 || guards->runtime>=0 ||
        !registered_management_current(management,manager,paths,guards)) return 0;
    int fd=connect_registered_socket(paths,deadline);
    if (fd<0) return 0;
    int ready=ready_registered_connected(fd,management,manager,paths,guards,version,build,deadline);
    close(fd);
    return ready;
}
