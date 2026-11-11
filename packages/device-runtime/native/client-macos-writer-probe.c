/* OFFLINE EXPERIMENT ONLY. Generic paths must never be used as an installer.
 * Writes only a caller-created private temporary directory; no service calls,
 * fork, exec, signals, archive extraction, removal or lock-file creation.
 * Tests supply a tiny source tree and already-provisioned stable lock files.
 * copyfile executes in this same process; renamex_np exchanges whole trees.
 * Not a claim of power-loss durability, trusted input validation or production
 * lifecycle/Installer integration. Fault exits deliberately leave evidence.
 */
#include <copyfile.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

#include "client-macos-tree-check-probe.h"
#ifdef EMBEDDED_RESOURCE_CHECK
#define RESOURCE_CHECK_LIBRARY
#include "client-macos-resource-check.c"
#endif

static const char *fault;
static int paused;
static int pause_copy;

static int fail(const char *message) {
    fprintf(stderr, "offline writer probe: %s\n", message);
    return 70;
}

static void checkpoint(const char *name) {
    if (fault && !strcmp(fault, name)) _exit(86);
}

static int on_copy(int what, int stage, copyfile_state_t state,
                   const char *from, const char *to, void *context) {
    (void)state; (void)from; (void)to; (void)context;
    if (what == COPYFILE_RECURSE_FILE && stage == COPYFILE_FINISH && !paused) {
        paused = 1;
        checkpoint("during-copy");
        if (pause_copy) {
            struct pollfd input = {STDIN_FILENO, POLLIN, 0};
            char proceed;
            if (write(STDOUT_FILENO, "COPYING\n", 8) != 8 ||
                poll(&input, 1, 5000) <= 0 || read(STDIN_FILENO, &proceed, 1) != 1)
                return COPYFILE_QUIT;
        }
    }
    return COPYFILE_CONTINUE;
}

static int private_directory(const char *path) {
    struct stat s;
    return lstat(path, &s) == 0 && S_ISDIR(s.st_mode) &&
           s.st_uid == getuid() && !(s.st_mode & 0077);
}

#include "client-macos-private-lock.h"
static int acquire(const char *directory, const char *name, uid_t owner) {
    return acquire_private_lock(directory, name, owner);
}

/* Caller MUST continuously own management/start/runtime guards.
 * Does not acquire or release caller locks. OFFLINE experiment only.
 */
#include "client-macos-tree-owner.h"
static int publish_guarded_target_checked(const char *source, const char *current,
                                          const char *stage, const uid_t *owner, int create_only,
                                          int (*before_publish)(void *), void *context) {
    checked_entries = 0; checked_bytes = 0;
    if (!equal_tree(source, source, 0)) return fail("source tree unsupported");
#ifdef EMBEDDED_RESOURCE_CHECK
    if (!verify_resource_tree(source)) return fail("source differs from embedded inventory");
#endif
    if (owner && !tree_owner_valid(source,*owner,0)) return fail("source ownership invalid");
    checkpoint("before-copy");
    copyfile_state_t state = copyfile_state_alloc();
    if (!state) return fail("copy state unavailable");
    if (copyfile_state_set(state, COPYFILE_STATE_STATUS_CB, (void *)on_copy))
        return fail("callback unavailable");
    int result = copyfile(source, stage, state,
                          COPYFILE_RECURSIVE | COPYFILE_DATA | COPYFILE_STAT |
                          COPYFILE_EXCL | COPYFILE_NOFOLLOW);
    copyfile_state_free(state);
    if (result) return fail("copy failed; stage retained");
    checkpoint("after-copy");
    checked_entries = 0; checked_bytes = 0;
    if (!equal_tree(source, stage, 0)) return fail("stage verification failed; retained");
#ifdef EMBEDDED_RESOURCE_CHECK
    if (!verify_resource_tree(stage)) return fail("stage differs from embedded inventory");
#endif
    if (owner && !tree_owner_valid(stage,*owner,0)) return fail("stage ownership invalid; retained");
    struct stat existing;
    unsigned int flags;
    if (create_only) {
        /* The first-install contract must never select exchange semantics,
         * even if the target appears after an earlier absence check. */
        flags = RENAME_EXCL;
    } else if (lstat(current, &existing) == 0) {
        if (!S_ISDIR(existing.st_mode)) return fail("current must be a directory");
        flags = RENAME_SWAP;
    } else if (errno == ENOENT) {
        flags = RENAME_EXCL;
    } else return fail("current unavailable");
    if (before_publish && !before_publish(context))
        return fail("publication guard changed; stage retained");
    if (renamex_np(stage, current, flags)) return fail("atomic publication unsupported or failed");
    checkpoint("after-swap");
    return 0;
}

static int publish_guarded_tree(const char *source, const char *destination) {
    char stage[PATH_MAX], current[PATH_MAX];
    if (snprintf(stage, sizeof(stage), "%s/stage-%ld", destination, (long)getpid()) >= (int)sizeof(stage) ||
        snprintf(current, sizeof(current), "%s/current", destination) >= (int)sizeof(current))
        return fail("path too long");
    return publish_guarded_target_checked(source,current,stage,NULL,0,NULL,NULL);
}

int main(int argc, char **argv) {
#ifdef OFFLINE_BOUND_WRITER
    /* Non-installable sandbox entry: all paths anchored to this executable.
     * Never use this experimental destination as the production /opt path. */
    if (argc != 1) return fail("bound offline writer accepts no arguments");
    char executable[PATH_MAX], resolved[PATH_MAX];
    uint32_t size = sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) || !realpath(executable, resolved))
        return fail("executable path unavailable");
    char *slash = strrchr(resolved, '/');
    if (!slash) return fail("executable directory unavailable");
    *slash = 0;
    char source[PATH_MAX], destination[PATH_MAX], lock_directory[PATH_MAX];
    if (snprintf(source, sizeof(source), "%s/resources/client-tree", resolved) >= (int)sizeof(source) ||
        snprintf(destination, sizeof(destination), "%s/offline-destination", resolved) >= (int)sizeof(destination) ||
        snprintf(lock_directory, sizeof(lock_directory), "%s/offline-locks", resolved) >= (int)sizeof(lock_directory))
        return fail("bound paths too long");
    char *bound[] = {argv[0], source, destination, lock_directory, "none", "no-pause", NULL};
    argv = bound; argc = 6;
#endif
    if (argc != 6 || argv[1][0] != '/' || argv[2][0] != '/' || argv[3][0] != '/')
        return fail("SOURCE PRIVATE_DEST PRIVATE_LOCKS FAULT PAUSE required");
    fault = argv[4];
    pause_copy = !strcmp(argv[5], "pause");
    if (!private_directory(argv[1]) || !private_directory(argv[2]) ||
        !private_directory(argv[3])) return fail("private directories required");
#ifdef EMBEDDED_RESOURCE_CHECK
    if (!strcmp(fault, "observe")) {
        char public_tree[PATH_MAX];
        if (snprintf(public_tree, sizeof(public_tree), "%s/current", argv[2]) >= (int)sizeof(public_tree))
            return fail("observation path too long");
        if (!verify_resource_tree(public_tree)) return 76;
        puts("TARGET_TREE_EXACT_READY_NOT_CHECKED");
        return 0;
    }
#endif
    const char *names[] = {"management.lock", "start.lock", "runtime.lock"};
    int locks[3];
    for (int i = 0; i < 3; i++) {
        if (i == 2 && !strcmp(argv[5], "stop")) {
            /* Offline bridge: THIS process owns the closed start gate while
             * another process performs the instance-bound lease protocol.
             * EOF/timeout does not authorize writes or replay an exit request.
             * Production requires framed, authenticated phase results. */
            struct pollfd input = {STDIN_FILENO, POLLIN, 0};
            char proceed;
            if (write(STDOUT_FILENO, "GATED\n", 6) != 6 ||
                poll(&input, 1, 5000) <= 0 || read(STDIN_FILENO, &proceed, 1) != 1 ||
                proceed != 'x') return fail("exit coordination unknown; no writes");
        }
        locks[i] = acquire(argv[3], names[i], getuid());
        if (locks[i] < 0) return locks[i] == -2 ? 75 : fail("lock unavailable");
    }
    int result = publish_guarded_tree(argv[1], argv[2]);
    if (result) return result;
    /* Old tree (upgrade) or no stage (first install) retained. Never rollback. */
    for (int i = 2; i >= 0; i--) close(locks[i]);
    if (write(STDOUT_FILENO, "PUBLISHED\n", 10) != 10) return fail("completion observation failed");
    return 0;
}
