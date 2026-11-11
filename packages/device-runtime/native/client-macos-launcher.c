/* Fixed macOS arm64 managed-service entry. No arguments or HOME lookup.
 * Both stable locks must be provisioned before publishing the LaunchAgent.
 * fd 3 is adopted and made CLOEXEC by client_macos_entry before imports.
 * This protocol does not authorize replacing this executable with Installer.
 */
#include <errno.h>
#include <pwd.h>
#include <fcntl.h>
#include <stdio.h>
#include <libgen.h>
#include <string.h>
#include <limits.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

static int fail(const char *message, int fd) {
    if (fd >= 0) close(fd);
    fprintf(stderr, "ai-device-client launcher: %s\n", message);
    return 70;
}

int main(int argc, char **argv) {
    (void)argv;
    if (argc != 1 || getuid() != geteuid() || getuid() < 501)
        return fail("ordinary runtime account and no arguments required", -1);
    struct passwd *account = getpwuid(getuid());
    if (!account || !account->pw_dir || account->pw_dir[0] != '/' ||
        !strcmp(account->pw_dir, "/") || !strcmp(account->pw_dir, "/var/root"))
        return fail("account home unavailable", -1);
    char lock_path[PATH_MAX], config_path[PATH_MAX];
    const char *suffix = "/Library/Application Support/ai-device-client";
    if (snprintf(lock_path, sizeof(lock_path), "%s%s/runtime.lock", account->pw_dir, suffix)
            >= (int)sizeof(lock_path) ||
        snprintf(config_path, sizeof(config_path), "%s%s/client.json", account->pw_dir, suffix)
            >= (int)sizeof(config_path))
        return fail("account path too long", -1);
    int start_fd = -1;
    /* start.lock is a sibling of the runtime lock.
     * Never create it, and do not load the target until handoff is complete. */
    char parent_path[PATH_MAX], start_path[PATH_MAX];
    if (strlen(lock_path) >= sizeof(parent_path))
        return fail("lock path too long", -1);
    strcpy(parent_path, lock_path);
    char *parent = dirname(parent_path);
    struct stat directory;
    if (lstat(parent, &directory) != 0 || !S_ISDIR(directory.st_mode) ||
        directory.st_uid != getuid() || (directory.st_mode & 0077))
        return fail("invalid private lock directory", -1);
    if (snprintf(start_path, sizeof(start_path), "%s/start.lock", parent) >= (int)sizeof(start_path))
        return fail("gate path too long", -1);
    start_fd = open(start_path, O_RDWR | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    struct stat start_info, start_current;
    if (start_fd < 0 || fstat(start_fd, &start_info) != 0 ||
        !S_ISREG(start_info.st_mode) || start_info.st_uid != getuid() ||
        (start_info.st_mode & 0077) || start_info.st_nlink != 1)
        return fail("invalid start gate", start_fd);
    if (flock(start_fd, LOCK_SH | LOCK_NB) != 0) {
        int busy = (errno == EWOULDBLOCK || errno == EAGAIN);
        close(start_fd);
        if (busy) return 0;
        return fail("start gate acquisition failed", -1);
    }
    if (lstat(start_path, &start_current) != 0 ||
        start_current.st_dev != start_info.st_dev || start_current.st_ino != start_info.st_ino)
        return fail("start gate identity changed", start_fd);
    int fd = open(lock_path, O_RDWR | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return fail("lock unavailable", -1);
    struct stat info, current;
    if (fstat(fd, &info) != 0 || !S_ISREG(info.st_mode) ||
        info.st_uid != getuid() || (info.st_mode & 0077) || info.st_nlink != 1)
        return fail("invalid private lock", fd);
    if (flock(fd, LOCK_EX | LOCK_NB) != 0) {
        int busy = (errno == EWOULDBLOCK || errno == EAGAIN);
        close(fd);
        if (busy) return 0;
        return fail("lock acquisition failed", -1);
    }
    if (lstat(lock_path, &current) != 0 || current.st_dev != info.st_dev ||
        current.st_ino != info.st_ino)
        return fail("lock identity changed", fd);
    /* Runtime lifetime exclusion now protects target loading. */
    if (start_fd >= 0) close(start_fd);
    /* dup2 clears close-on-exec; handle fd==3 explicitly as well. */
    if (fd != 3) {
        if (dup2(fd, 3) < 0) return fail("lock handoff failed", fd);
        close(fd);
    }
    if (fcntl(3, F_SETFD, 0) < 0) return fail("lock handoff failed", 3);
    char *const target[] = {
        "/opt/ai-device-client/runtime/bin/python3", "-I", "-B", "-m",
        "device_runtime.client_macos_entry", "--inherit-runtime-lock",
        "--config", config_path, NULL
    };
    execv(target[0], target);
    return fail("executable unavailable", 3);
}
