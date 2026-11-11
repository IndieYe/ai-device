/* Experimental lock-before-exec probe. Not a shipping launcher.
 * Usage: probe PRIVATE_LOCK EXECUTABLE [ARG...]
 * No fork, signals, service operations or lock-file creation.
 * The target must adopt fd 3 and set FD_CLOEXEC before spawning children.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <libgen.h>
#include <string.h>
#include <limits.h>
#include <sys/file.h>
#ifdef TEST_BEFORE_LOCK
#include <poll.h>
#endif
#include <sys/stat.h>
#include <unistd.h>

static int fail(const char *message, int fd) {
    if (fd >= 0) close(fd);
    fprintf(stderr, "lock-exec prototype: %s\n", message);
    return 70;
}

int main(int argc, char **argv) {
    if (argc < 3 || argv[1][0] != '/' || argv[2][0] != '/')
        return fail("absolute lock and executable paths required", -1);
#ifdef TEST_BEFORE_LOCK
    /* Test-only bounded handshake, omitted from ordinary builds. */
    struct pollfd gate = {STDIN_FILENO, POLLIN, 0};
    char proceed;
    if (write(STDOUT_FILENO, "BEFORE_LOCK\n", 12) != 12 ||
        poll(&gate, 1, 5000) <= 0 || read(STDIN_FILENO, &proceed, 1) != 1)
        return fail("test handshake failed", -1);
#endif
    int start_fd = -1;
#ifdef WITH_START_GATE
    /* Protocol experiment: start.lock is a sibling of the runtime lock.
     * Never create it, and do not load the target until handoff is complete. */
    char parent_path[PATH_MAX], start_path[PATH_MAX];
    if (strlen(argv[1]) >= sizeof(parent_path))
        return fail("lock path too long", -1);
    strcpy(parent_path, argv[1]);
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
        if (busy) return 75;
        return fail("start gate acquisition failed", -1);
    }
    if (lstat(start_path, &start_current) != 0 ||
        start_current.st_dev != start_info.st_dev || start_current.st_ino != start_info.st_ino)
        return fail("start gate identity changed", start_fd);
#endif
    int fd = open(argv[1], O_RDWR | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return fail("lock unavailable", -1);
    struct stat info, current;
    if (fstat(fd, &info) != 0 || !S_ISREG(info.st_mode) ||
        info.st_uid != getuid() || (info.st_mode & 0077) || info.st_nlink != 1)
        return fail("invalid private lock", fd);
    if (flock(fd, LOCK_EX | LOCK_NB) != 0) {
        int busy = (errno == EWOULDBLOCK || errno == EAGAIN);
        close(fd);
        if (busy) return 75;
        return fail("lock acquisition failed", -1);
    }
    if (lstat(argv[1], &current) != 0 || current.st_dev != info.st_dev ||
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
    execv(argv[2], &argv[2]);
    return fail("executable unavailable", 3);
}
