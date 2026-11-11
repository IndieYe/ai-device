/* Remove one regular file by name from a directory opened fd-relative.
 *
 * This is the only way the uninstall entry deletes a file, so it carries the
 * whole guarantee that the thing being removed is the thing that was checked.
 *
 * `name` must be a bare file name. An absolute path would make the dirfd
 * argument of fstatat/unlinkat irrelevant -- both resolve an absolute path from
 * the process root, so the O_NOFOLLOW|O_DIRECTORY open below would protect
 * nothing and every check would apply to whatever object the path names rather
 * than to the directory this call claims to be bound to. The plist removal used
 * to pass its full path here; that is exactly the bug this signature prevents.
 *
 * The directory itself is checked as well: it must be owned by `owner` and not
 * writable by group or other. Opening a path proves only that *some* directory
 * is there. If another user can write to it, they can replace the validated
 * file between the check and the unlink, and the ownership assertion on the
 * file means nothing.
 *
 * Kept in its own header so this function can be compiled and exercised
 * directly: the uninstall test harness substitutes the guard and relay
 * plumbing, and including the entry header would drag all of it in.
 */
#ifndef CLIENT_MACOS_UNINSTALL_REMOVE_H
#define CLIENT_MACOS_UNINSTALL_REMOVE_H

#include <fcntl.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int uninstall_remove_file(const char *name, const char *parent,
                                 uid_t owner, int require_owner) {
    if (!name || !*name || strchr(name, '/') ||
        !strcmp(name, ".") || !strcmp(name, "..")) return 0;
    int directory = open(parent, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (directory < 0) return 0;
    struct stat parent_info, info;
    int ok = 0;
    if (!fstat(directory, &parent_info) && S_ISDIR(parent_info.st_mode) &&
        parent_info.st_uid == owner && !(parent_info.st_mode & 0022) &&
        fstatat(directory, name, &info, AT_SYMLINK_NOFOLLOW) == 0 &&
        S_ISREG(info.st_mode) && info.st_nlink == 1 &&
        (!require_owner || info.st_uid == owner) &&
        unlinkat(directory, name, 0) == 0)
        ok = 1;
    close(directory);
    return ok;
}

#endif /* CLIENT_MACOS_UNINSTALL_REMOVE_H */
