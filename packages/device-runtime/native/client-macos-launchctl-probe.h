/* "Is this launchd job loaded?" as a three-state read.
 *
 * The uninstall entry needs to know this before it decides whether to boot the
 * job out. Two wrong answers are possible and they are not symmetric:
 *
 *   - reporting "loaded" for an absent job makes the unload relay fail on a
 *     job that is already gone, so an operator who stopped the service exactly
 *     as `--confirm-service-not-running` asks could never finish;
 *   - reporting "absent" for a job we merely failed to inspect lets the caller
 *     skip bootout and delete the program tree under a running client.
 *
 * The second is a data-integrity failure, so only a positive "no such service"
 * is allowed to mean absent. Everything else is unknown, and callers must
 * refuse on unknown.
 *
 * This lives in its own header so the predicate can be compiled and tested on
 * its own: the uninstall test harness substitutes the probe wholesale, which
 * previously meant the exit-code mapping below ran in no test at all.
 */
#ifndef CLIENT_MACOS_LAUNCHCTL_PROBE_H
#define CLIENT_MACOS_LAUNCHCTL_PROBE_H

#include <errno.h>
#include <fcntl.h>
#include <sys/wait.h>
#include <unistd.h>

/* Exit codes of `/bin/launchctl print <target>`, measured on macOS 27.0.1:
 *
 *   0    the service exists in the domain             -> loaded
 *   113  "Could not find service <label> in domain"    -> definitely absent
 *   112  "Could not find domain for user gui: <uid>"   -> domain not visible
 *        (typically the user is not logged in); says nothing about the job
 *   64   "Unrecognized target specifier"               -> malformed target
 *
 * 112 and 64 describe our own inability to look, not the job's state.
 */
#define LAUNCHCTL_EXIT_NO_SUCH_SERVICE 113

/* 1 loaded, 0 not loaded, -1 could not tell. Pure, no side effects. */
__attribute__((unused))
static int launchctl_job_state_from_status(int status) {
    if (!WIFEXITED(status)) return -1;   /* signalled: no exit status at all */
    int code = WEXITSTATUS(status);
    if (code == 0) return 1;
    if (code == LAUNCHCTL_EXIT_NO_SUCH_SERVICE) return 0;
    return -1;
}

#ifndef UNINSTALL_HAVE_JOB_PROBE
#define UNINSTALL_HAVE_JOB_PROBE 1
/* 1 loaded, 0 not loaded, -1 could not tell. Read-only; changes nothing. */
static int launchctl_job_loaded(const char *target) {
    if (!target || !*target) return -1;
    pid_t child = fork();
    if (child < 0) return -1;
    if (child == 0) {
        int null = open("/dev/null", O_RDWR);
        if (null >= 0) {
            dup2(null, STDOUT_FILENO);
            dup2(null, STDERR_FILENO);
            if (null > STDERR_FILENO) close(null);
        }
        execl("/bin/launchctl", "launchctl", "print", target, (char *)NULL);
        _exit(127);   /* our own exec failure: never a job state */
    }
    int status = 0;
    while (waitpid(child, &status, 0) < 0) {
        if (errno != EINTR) return -1;
    }
    return launchctl_job_state_from_status(status);
}
#endif

#endif /* CLIENT_MACOS_LAUNCHCTL_PROBE_H */
