/* Internal only: fixed-resource read-only diagnostic, no installation/service calls.
 * Caller continuously owns management exclusion; never release on unknown.
 * Signals below target ONLY the diagnostic child created here, never a client.
 */
#ifndef CLIENT_MACOS_HOST_CONFLICTS_H
#define CLIENT_MACOS_HOST_CONFLICTS_H
#include <spawn.h>
#include <signal.h>
#include <sys/wait.h>
#include <time.h>
#include <math.h>

static double conflict_clock(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC,&now)) return -1;
    return now.tv_sec+now.tv_nsec/1e9;
}
static int conflict_time_remaining(double deadline) {
    double now=conflict_clock();
    return isfinite(now) && now>=0 && isfinite(deadline) && now<deadline;
}
static int conflict_reap(pid_t child,int *status,double deadline) {
    while (conflict_time_remaining(deadline)) {
        pid_t result=waitpid(child,status,WNOHANG);
        if (result==child) return 1;
        if (result<0 && errno!=EINTR) return 0;
        struct timespec pause={0,1000000};nanosleep(&pause,NULL);
    }
    return 0;
}
static int conflict_stop_child(pid_t child,int *status) {
    /* This probe cannot spawn descendants. No shell or process-group signals. */
    if (kill(child,SIGTERM) && errno!=ESRCH) return 0;
    if (conflict_reap(child,status,conflict_clock()+0.2)) return 1;
    if (kill(child,SIGKILL) && errno!=ESRCH) return 0;
    return conflict_reap(child,status,conflict_clock()+0.5);
}
static int check_bound_host_conflicts(const char *management,uid_t owner,int management_fd,
                                     int confirmed,double timeout) {
    if (confirmed!=1 || !isfinite(timeout) || timeout<=0 || timeout>10 ||
        !private_lock_matches(management_fd,management,"management.lock",owner)) return 0;
    double start=conflict_clock();
    if (!isfinite(start) || start<0) return 0;
    double deadline=start+timeout;
    char source[PATH_MAX],python[PATH_MAX];
    if (!locate_bound_resources(source,owner) || !verify_resource_tree(source) ||
        !tree_owner_valid(source,owner,0) ||
        snprintf(python,sizeof(python),"%s/runtime/bin/python3",source)>=(int)sizeof(python) ||
        !private_lock_matches(management_fd,management,"management.lock",owner) ||
        !conflict_time_remaining(deadline)) return 0;
    int output[2];
    if (pipe(output)) return 0;
    if (fcntl(output[0],F_SETFD,FD_CLOEXEC)<0 || fcntl(output[1],F_SETFD,FD_CLOEXEC)<0 ||
        fcntl(output[0],F_SETFL,O_NONBLOCK)<0) {close(output[0]);close(output[1]);return 0;}
    posix_spawn_file_actions_t actions;
    posix_spawnattr_t attributes;
    if (posix_spawn_file_actions_init(&actions)) {close(output[0]);close(output[1]);return 0;}
    if (posix_spawnattr_init(&attributes)) {
        posix_spawn_file_actions_destroy(&actions);close(output[0]);close(output[1]);return 0;
    }
    int setup=posix_spawnattr_setflags(&attributes,POSIX_SPAWN_CLOEXEC_DEFAULT);
    setup |= posix_spawn_file_actions_addopen(&actions,STDIN_FILENO,"/dev/null",O_RDONLY,0);
    setup |= posix_spawn_file_actions_adddup2(&actions,output[1],STDOUT_FILENO);
    setup |= posix_spawn_file_actions_addopen(&actions,STDERR_FILENO,"/dev/null",O_WRONLY,0);
    char *arguments[]={python,"-I","-B","-m","device_runtime.client_macos_conflicts",
                       "--confirm-legacy-clients-disabled",NULL};
    char *environment[]={"PATH=/usr/bin:/bin","LANG=C",NULL};
    pid_t child=-1;
    int spawned=setup ? setup : posix_spawn(&child,python,&actions,&attributes,arguments,environment);
    posix_spawn_file_actions_destroy(&actions);posix_spawnattr_destroy(&attributes);
    close(output[1]);
    if (spawned) {close(output[0]);return 0;}
    char response[128];size_t used=0;
    int status=0,reaped=0,eof=0,valid=1;
    while (conflict_time_remaining(deadline)) {
        ssize_t count=read(output[0],response+used,sizeof(response)-used);
        if (count>0) {used+=(size_t)count;if (used==sizeof(response)) {valid=0;break;}}
        else if (!count) eof=1;
        else if (errno!=EINTR && errno!=EAGAIN) {valid=0;break;}
        if (!reaped) {
            pid_t result=waitpid(child,&status,WNOHANG);
            if (result==child) reaped=1;
            else if (result<0 && errno!=EINTR) {valid=0;break;}
        }
        if (reaped && eof) break;
        struct timespec pause={0,1000000};nanosleep(&pause,NULL);
    }
    close(output[0]);
    if (!reaped) {conflict_stop_child(child,&status);return 0;}
    const char expected[]="AI_DEVICE_HOST_CONFLICTS_CLEAR_V1\n";
    return valid && eof && conflict_time_remaining(deadline) && WIFEXITED(status) && WEXITSTATUS(status)==0 &&
        used==sizeof(expected)-1 && !memcmp(response,expected,sizeof(expected)-1) &&
        private_lock_matches(management_fd,management,"management.lock",owner);
}
#endif
