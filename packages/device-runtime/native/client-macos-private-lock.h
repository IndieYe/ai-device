/* Existing stable private lock only; never create, unlink, chown or repair.
 * Owner is explicit: root management and registered-user locks differ.
 * Caller controls ordering; busy/unknown never grants write permission.
 */
#ifndef CLIENT_MACOS_PRIVATE_LOCK_H
#define CLIENT_MACOS_PRIVATE_LOCK_H
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

static inline int private_lock_matches(int fd,const char *directory,const char *name,uid_t owner) {
    struct stat parent,opened,current;
    char path[PATH_MAX];
    if (fd<0 || !directory || lstat(directory,&parent) || !S_ISDIR(parent.st_mode) ||
        parent.st_uid!=owner || (parent.st_mode & 0077) ||
        snprintf(path,sizeof(path),"%s/%s",directory,name)>=(int)sizeof(path) ||
        fstat(fd,&opened) || lstat(path,&current)) return 0;
    return S_ISREG(opened.st_mode) && S_ISREG(current.st_mode) &&
        opened.st_dev==current.st_dev && opened.st_ino==current.st_ino &&
        opened.st_uid==owner && current.st_uid==owner &&
        !(opened.st_mode & 0077) && !(current.st_mode & 0077) &&
        opened.st_nlink==1 && current.st_nlink==1;
}

/* Continuous lock continuity check, used before any action that depends on
 * holding the lock. private_lock_matches only proves the descriptor still
 * addresses the same private file; it says nothing about whether flock is
 * still held (a closed or unlocked descriptor matches just as well). Re-probe
 * the held lock through a second descriptor on the SAME path: a live exclusive
 * lock must refuse that probe with EWOULDBLOCK. Any other outcome means the
 * lock is not demonstrably held, so the caller must not act.
 *
 * This excludes cooperating processes only. It is not a service-job,
 * descendant or non-cooperating-instance property. */
static inline int private_lock_held(int fd,const char *directory,const char *name,uid_t owner) {
    if (!private_lock_matches(fd,directory,name,owner)) return 0;
    char path[PATH_MAX];
    if (snprintf(path,sizeof(path),"%s/%s",directory,name)>=(int)sizeof(path)) return 0;
    int probe=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC);
    if (probe<0) return 0;
    struct stat opened,probed;
    int same = !fstat(fd,&opened) && !fstat(probe,&probed) &&
        opened.st_dev==probed.st_dev && opened.st_ino==probed.st_ino;
    int result = 0;
    if (same) {
        if (flock(probe,LOCK_EX|LOCK_NB)) {
            /* Expected: the live exclusive holder blocks this second opener.
             * Only EWOULDBLOCK/EAGAIN proves contention; any other errno is an
             * unknown lock state, never treated as held. */
            result = (errno==EWOULDBLOCK || errno==EAGAIN);
        } else {
            /* The probe acquired it, so nothing is holding the lock. Drop the
             * accidental acquisition immediately; it never extends our view. */
            flock(probe,LOCK_UN);
            result = 0;
        }
    }
    close(probe);
    return result;
}
static int acquire_private_lock(const char *directory, const char *name, uid_t owner) {
    struct stat parent, final_parent, info, current;
    if (lstat(directory, &parent) || !S_ISDIR(parent.st_mode) ||
        parent.st_uid != owner || (parent.st_mode & 0077)) return -1;
    char path[PATH_MAX];
    if (snprintf(path, sizeof(path), "%s/%s", directory, name) >= (int)sizeof(path)) return -1;
    int fd = open(path, O_RDWR | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return -1;
    if (fstat(fd, &info) || !S_ISREG(info.st_mode) || info.st_uid != owner ||
        (info.st_mode & 0077) || info.st_nlink != 1) {close(fd);return -1;}
    if (flock(fd, LOCK_EX | LOCK_NB)) {
        int busy = errno == EAGAIN || errno == EWOULDBLOCK;
        close(fd); return busy ? -2 : -1;
    }
    if (lstat(path, &current) || info.st_dev != current.st_dev || info.st_ino != current.st_ino ||
        current.st_uid != owner || (current.st_mode & 0077) || current.st_nlink != 1 ||
        lstat(directory, &final_parent) || parent.st_dev != final_parent.st_dev ||
        parent.st_ino != final_parent.st_ino || final_parent.st_uid != owner ||
        (final_parent.st_mode & 0077)) {close(fd);return -1;}
    return fd;
}
#endif
