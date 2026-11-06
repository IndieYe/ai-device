/* Internal fixed service load/unload relay. Caller holds management continuity.
 *
 * Shape is fixed: one module name, one inherited management fd on fd4, no path,
 * UID, label or command reachable from argv or environment. It only relays the
 * already validated registered target; it never selects a service itself.
 * Signals target only this invocation's helper, never a launchd-owned PID.
 * A success token means launchctl reported success for that one attempt, NOT an
 * ownership or stopped proof; unknown outcomes are never replayed here.
 */
#ifndef CLIENT_MACOS_SERVICE_RELAY_H
#define CLIENT_MACOS_SERVICE_RELAY_H
#include "client-macos-start-child.h"
#include <spawn.h>
#include <errno.h>
#include <signal.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <math.h>

#define SERVICE_RELAY_LOAD_MODULE "device_runtime.client_macos_service_load_entry"
#define SERVICE_RELAY_UNLOAD_MODULE "device_runtime.client_macos_service_unload_entry"
#define SERVICE_RELAY_LOAD_TOKEN "AI_DEVICE_REGISTERED_SERVICE_LOADED_V1\n"
#define SERVICE_RELAY_UNLOAD_TOKEN "AI_DEVICE_REGISTERED_SERVICE_UNLOADED_V1\n"

/* Tests may define service_relay_clock before including this header to drive
 * the wait loop deterministically; production falls back to the monotonic
 * clock below. */
#ifndef service_relay_clock
static double service_relay_clock(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC,&now)) return -1;
    return now.tv_sec+now.tv_nsec/1e9;
}
#endif

static int service_relay_relay(const char *management,uid_t owner,int management_fd,
                               const char *tree,const char *module,const char *token,
                               double deadline) {
    if (!tree || !*tree || !module || !token || !isfinite(deadline) || !(deadline>0) ||
        !private_lock_held(management_fd,management,"management.lock",owner)) return 0;
    /* Read the clock ONCE and validate that single reading. Comparing an
     * unvalidated result twice lets a NaN slip through both `<0` and
     * `>=deadline` (both false for NaN) and reach posix_spawn. */
    double entry_now=service_relay_clock();
    if (!isfinite(entry_now) || entry_now<0 || entry_now>=deadline) return 0;
    /* The tree is an explicit argument, never self-located.
     *
     * This relay used to derive its interpreter from
     * `<exe dir>/resources/client-tree`. That only held while the upgrade entry
     * shipped a second copy of the program tree beside itself. A shipping
     * package now carries exactly one tree, so there is nothing beside the
     * entry to locate and the helper must be told which verified tree to run:
     *
     *   unload  - the staged source this package wrote, inventoried and owned
     *             by the administrator before any side effect was requested.
     *   load    - the live program path, which is the OLD tree when the job is
     *             restored after a bounded recovery and the NEW tree after a
     *             successful commit.
     *
     * Taking it as a parameter also fixes the direction of trust: the helper
     * runs the tree the caller has just verified instead of whatever happens
     * to sit next to this executable. The checks below still run, so a caller
     * cannot pass an unverified or wrongly owned tree. */
    char python[PATH_MAX];
    if (!verify_resource_tree(tree) || !tree_owner_valid(tree,owner,0) ||
        snprintf(python,sizeof python,"%s/runtime/bin/python3",tree)>=(int)sizeof python)
        return 0;
    int output[2];
    if (pipe(output)) return 0;
    /* Non-blocking read end: the loop below polls both child state and output,
     * so a blocking read would defeat the deadline whenever the helper stays
     * alive without producing the token. */
    int flags=fcntl(output[0],F_GETFL);
    if (flags<0 || fcntl(output[0],F_SETFL,flags|O_NONBLOCK)) {
        close(output[0]);close(output[1]);return 0;
    }
    int inherited=fcntl(management_fd,F_DUPFD_CLOEXEC,10);
    if (inherited<0) {close(output[0]);close(output[1]);return 0;}
    posix_spawn_file_actions_t actions;
    posix_spawnattr_t attributes;
    /* Track each initialisation separately: `a || b` short-circuits, so a
     * failure of the second init would leak the first one if we only knew the
     * combined result. */
    int actions_ready=posix_spawn_file_actions_init(&actions)==0;
    int attr_ready=0;
    int setup=1;
    if (actions_ready) {
        attr_ready=posix_spawnattr_init(&attributes)==0;
        if (attr_ready) {
            sigset_t empty;sigemptyset(&empty);
            setup = posix_spawnattr_setsigmask(&attributes,&empty) ||
                posix_spawnattr_setflags(&attributes,POSIX_SPAWN_CLOEXEC_DEFAULT) ||
                posix_spawn_file_actions_addopen(&actions,STDIN_FILENO,"/dev/null",O_RDONLY,0) ||
                posix_spawn_file_actions_adddup2(&actions,output[1],STDOUT_FILENO) ||
                posix_spawn_file_actions_addopen(&actions,STDERR_FILENO,"/dev/null",O_WRONLY,0) ||
                posix_spawn_file_actions_adddup2(&actions,inherited,4);
        }
    }
    char *arguments[]={(char *)python,(char *)"-I",(char *)"-B",(char *)"-m",
                       (char *)module,(char *)"--inherit-management-lock",NULL};
    char *environment[]={(char *)"PATH=/usr/bin:/bin",(char *)"LANG=C",NULL};
    pid_t child=-1;
    int spawned=setup ? setup : posix_spawn(&child,python,&actions,&attributes,arguments,environment);
    if (attr_ready) posix_spawnattr_destroy(&attributes);
    if (actions_ready) posix_spawn_file_actions_destroy(&actions);
    close(output[1]);close(inherited);
    if (spawned) {close(output[0]);return 0;}
    char response[128];size_t used=0;
    int status=0,reaped=0,eof=0,valid=1;
    /* Sample the clock once per iteration and treat any invalid or expired
     * reading as a hard stop: several independent clock reads could each look
     * valid while the total elapsed time already exceeded the deadline. */
    for (;;) {
        double now=service_relay_clock();
        if (!isfinite(now) || now<0 || now>=deadline) {valid=0;break;}
        ssize_t count=read(output[0],response+used,sizeof(response)-used);
        if (count>0) {used+=(size_t)count;if (used==sizeof(response)) {valid=0;break;}}
        else if (!count) eof=1;
        else if (errno!=EINTR && errno!=EAGAIN && errno!=EWOULDBLOCK) {valid=0;break;}
        if (!reaped) {
            pid_t result=waitpid(child,&status,WNOHANG);
            if (result==child) reaped=1;
            else if (result<0 && errno!=EINTR) {valid=0;break;}
        }
        if (reaped && eof) break;
        struct timespec pause={0,1000000};nanosleep(&pause,NULL);
    }
    close(output[0]);
    if (!reaped) {start_child_stop_child(child,&status);return 0;}
    size_t expected=strlen(token);
    double observed=service_relay_clock();
    return valid && eof && isfinite(observed) && observed>=0 && observed<deadline &&
        WIFEXITED(status) && WEXITSTATUS(status)==0 &&
        used==expected && !memcmp(response,token,expected) &&
        private_lock_held(management_fd,management,"management.lock",owner);
}

static int load_registered_service_relay(const char *management,uid_t owner,
                                         int management_fd,const char *tree,
                                         double deadline) {
    return service_relay_relay(management,owner,management_fd,tree,
        SERVICE_RELAY_LOAD_MODULE,SERVICE_RELAY_LOAD_TOKEN,deadline);
}

static int unload_registered_service_relay(const char *management,uid_t owner,
                                           int management_fd,const char *tree,
                                           double deadline) {
    return service_relay_relay(management,owner,management_fd,tree,
        SERVICE_RELAY_UNLOAD_MODULE,SERVICE_RELAY_UNLOAD_TOKEN,deadline);
}
#endif
