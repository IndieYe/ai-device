/* Explicit uninstall entry: remove the registered service and the program.
 *
 * Requirement 8: a plain uninstall removes the service and preserves
 * configuration, credentials and business data. Full data deletion is a
 * separate, explicit operation and is deliberately unreachable from here --
 * nothing below touches the runtime directory (client.json, token, state/).
 *
 * What this entry removes, in order:
 *
 *   1. the launchd job, via the same unload relay the upgrade path uses;
 *   2. the job definition (the user's LaunchAgent plist);
 *   3. registration.json, which is this machine's claim that the service is
 *      registered. Leaving it behind would keep the device in a state where
 *      "registered" is true but no definition exists, and every later install
 *      would have to be diagnosed by hand. Re-registering after an uninstall is
 *      the deliberate consequence, not an oversight;
 *   4. the program tree itself.
 *
 * Stop proof is a caller assertion, exactly as in repair: `--confirm-service-
 * not-running` says the operator has established the service is stopped. The
 * runtime.lock acquisition below is positive evidence that no COOPERATING
 * instance holds the lock right now; it does not prove the job is unloaded,
 * that descendants exited, or that a non-cooperating instance is gone.
 *
 * Ordering is deliberately "unload first, then delete the definition". A
 * definition left in place while its tree is deleted would be reloaded by the
 * next login and fail; a definition removed before unloading could leave a
 * running instance with no definition to boot out against.
 *
 * Registration is deleted only after the service is confirmed unloaded: while
 * the job may still be loaded, registration.json is the only record of which
 * user and plist the unload has to target.
 */
#include <sys/stat.h>
#include <sys/wait.h>
#include <errno.h>
/* fd-relative recursive removal that never follows a symlink. Shared with the
 * stage-cleanup path rather than reimplemented here. */
#include "client-macos-stage-cleanup.h"

#define UNINSTALL_UNLOADED "AI_DEVICE_UNINSTALL_SERVICE_REMOVED_V1"

/* The two absolute locations this entry acts on. They are compile-time
 * constants, never arguments: an uninstall that accepted a caller-supplied
 * program or management path could be pointed at anything. The test harness
 * overrides them to observe the real removal behaviour. */
#ifndef UNINSTALL_MANAGEMENT
#define UNINSTALL_MANAGEMENT "/Library/Application Support/ai-device-client-management"
#endif
#ifndef UNINSTALL_OPT
#define UNINSTALL_OPT "/opt"
#endif
#ifndef UNINSTALL_PROGRAM_NAME
#define UNINSTALL_PROGRAM_NAME "ai-device-client"
#endif
/* The management directory is root-owned in production. The test harness runs
 * as an ordinary user and overrides this to exercise the real removal path. */
#ifndef UNINSTALL_MANAGEMENT_OWNER
#define UNINSTALL_MANAGEMENT_OWNER 0
#endif

/* 1 loaded, 0 not loaded, -1 could not tell. Read-only; changes nothing. */
#ifndef UNINSTALL_HAVE_JOB_PROBE
#define UNINSTALL_HAVE_JOB_PROBE 1
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
        _exit(127);
    }
    int status = 0;
    while (waitpid(child, &status, 0) < 0) {
        if (errno != EINTR) return -1;
    }
    if (!WIFEXITED(status)) return -1;
    return WEXITSTATUS(status) == 0 ? 1 : 0;
}
#endif

static int uninstall_remove_file(const char *path, const char *parent,
                                 uid_t owner, int require_owner) {
    int directory = open(parent, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (directory < 0) return 0;
    struct stat info;
    int ok = 0;
    if (fstatat(directory, path, &info, AT_SYMLINK_NOFOLLOW) == 0 &&
        S_ISREG(info.st_mode) && info.st_nlink == 1 &&
        (!require_owner || info.st_uid == owner) &&
        unlinkat(directory, path, 0) == 0)
        ok = 1;
    close(directory);
    return ok;
}

static int uninstall_registered_entry(int argc, char **argv) {
    if (argc != 3 || strcmp(argv[1], "uninstall-registered-program") ||
        strcmp(argv[2], "--confirm-service-not-running"))
        return 64;
    if (getuid() != 0 || geteuid() != 0) return 70;

    const char *management = UNINSTALL_MANAGEMENT;
    struct managed_guards guards = {.management = -1, .start = -1, .runtime = -1, .identity = nil};
    double now = lease_clock();
    if (!isfinite(now) || now < 0) return 70;
    double deadline = now + 120;

    NSDictionary *paths = prepare_start_barrier(management, 0, &guards);
    if (!paths) {release_guards(&guards); return 70;}

    /* Uninstalling is not first installation: without a registration there is
     * no service to remove, and guessing a user would be exactly the inference
     * registration exists to prevent. */
    if (registration_state(management, 0, guards.management) != 1) {
        release_guards(&guards);
        puts("AI_DEVICE_UNINSTALL_REFUSED_NOT_REGISTERED_V1");
        return 70;
    }

    /* The relay boots out through the installed tree, which must still be the
     * tree this package built. Checking it before the side effect means a
     * damaged or replaced program refuses here instead of half-unloading. */
    const char *program = [paths[@"program"] fileSystemRepresentation];
    if (!program || !verify_resource_tree(program) || !tree_owner_valid(program, 0, 0)) {
        release_guards(&guards);
        puts("AI_DEVICE_UNINSTALL_REFUSED_PROGRAM_INVALID_V1");
        return 70;
    }

    /* Positive evidence that no cooperating instance is alive. A busy lock or
     * an unusable one refuses; neither is read as "probably stopped". */
    if (!acquire_registered_runtime(management, 0, paths, &guards)) {
        int busy = guards.runtime == -2;
        release_guards(&guards);
        puts(busy ? "AI_DEVICE_UNINSTALL_REFUSED_SERVICE_RUNNING_V1"
                  : "AI_DEVICE_UNINSTALL_UNKNOWN_OR_REFUSED_V1");
        return 70;
    }

    /* Unload only when the job is actually loaded.
     *
     * `bootout` on a job that is not loaded exits 3 ("No such process"), which
     * the shared unload path reports as an unknown outcome. For an upgrade
     * that is right -- the job was guaranteed loaded before the window opened.
     * For an uninstall it is not: an operator who already stopped the service
     * (which is exactly what --confirm-service-not-running asserts) would be
     * refused forever, because the precondition for a successful bootout is
     * the state they were told to create.
     *
     * `launchctl print` on the registered target answers "is it loaded"
     * without changing anything: 0 when loaded, nonzero when not. Only 0 leads
     * to bootout; a failure to read the state refuses rather than assuming.
     */
    int loaded = launchctl_job_loaded([paths[@"service"] fileSystemRepresentation]);
    if (loaded < 0) {
        release_guards(&guards);
        puts("AI_DEVICE_UNINSTALL_UNKNOWN_OR_REFUSED_V1");
        return 70;
    }
    if (loaded == 1) {
        if (!unload_registered_service_relay(management, 0, guards.management, program, deadline)) {
            /* A failed bootout on a loaded job does not prove it is still
             * loaded. Registration stays: it is the record needed to retry
             * against the same target. */
            release_guards(&guards);
            puts("AI_DEVICE_UNINSTALL_UNLOAD_UNKNOWN_V1");
            return 70;
        }
        if (!registered_guards_current(management, 0, paths, &guards)) {
            release_guards(&guards);
            puts("AI_DEVICE_UNINSTALL_UNKNOWN_OR_REFUSED_V1");
            return 70;
        }
    }

    if (!registered_guards_current(management, 0, paths, &guards)) {
        release_guards(&guards);
        puts("AI_DEVICE_UNINSTALL_UNKNOWN_OR_REFUSED_V1");
        return 70;
    }

    /* Definition first: with the job unloaded, a leftover plist would be
     * reloaded at the next login and would point at a tree we are about to
     * delete. */
    NSString *plist = paths[@"plist"];
    if (!plist || !uninstall_remove_file([plist fileSystemRepresentation],
            [[plist stringByDeletingLastPathComponent] fileSystemRepresentation],
            [paths[@"runtime_owner"] unsignedIntValue], 1)) {
        release_guards(&guards);
        puts("AI_DEVICE_UNINSTALL_DEFINITION_FAILED_V1");
        return 70;
    }

    /* Only now that nothing can be reloaded: drop the registration claim. */
    if (!uninstall_remove_file("registration.json", management,
                               UNINSTALL_MANAGEMENT_OWNER, 1)) {
        release_guards(&guards);
        puts("AI_DEVICE_UNINSTALL_IDENTITY_FAILED_V1");
        return 70;
    }

    /* Remove the program tree last. A failure here leaves the device
     * unregistered with no definition -- recoverable by reinstalling, and
     * reported rather than hidden. */
    const char *parent = UNINSTALL_OPT;
    const char *name = UNINSTALL_PROGRAM_NAME;
    int opt = open(parent, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    int removed = 0;
    if (opt >= 0) {
        struct stat info;
        if (fstatat(opt, name, &info, AT_SYMLINK_NOFOLLOW) == 0 && S_ISDIR(info.st_mode))
            removed = remove_tree_at(opt, name);
        close(opt);
    }
    if (!removed) {
        release_guards(&guards);
        puts("AI_DEVICE_UNINSTALL_PROGRAM_REMAINS_V1");
        return 70;
    }

    /* Configuration, token and state are intentionally untouched: they live in
     * the user's runtime directory, outside everything above. */
    release_guards(&guards);
    puts(UNINSTALL_UNLOADED);
    return 0;
}
