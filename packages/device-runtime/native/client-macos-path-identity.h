/* Path identity for destructive path decisions.
 *
 * Why this exists: a string comparison is NOT a path comparison. `/opt/x` and
 * `/opt/./x` are the same directory, a symlinked parent makes two different
 * strings address one tree, and APFS is case-insensitive by default. Every
 * previous overlap check in this install path used `strncmp`, which means the
 * "this can never delete the live program" claim rested on text, not identity.
 *
 * Rules:
 *   - Resolve before comparing. Refuse when resolution fails: an unresolvable
 *     path cannot be proven distinct from the live tree.
 *   - Compare realpaths, then compare (st_dev, st_ino). Both are needed: the
 *     realpath catches normalising aliases, the inode pair catches anything
 *     that slipped past the textual form.
 *   - Refuse whenever a relationship cannot be established.
 *
 * These helpers answer "are these the same/overlapping object right now". They
 * do not hold anything open, so a caller about to act on the result must still
 * re-verify through a descriptor (see the fd-relative delete in
 * client-macos-stage-cleanup.h).
 */
#ifndef CLIENT_MACOS_PATH_IDENTITY_H
#define CLIENT_MACOS_PATH_IDENTITY_H

#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

/* Rejects a path whose textual form can make a later resolution step travel
 * through a link the caller did not intend.
 *
 * This exists because fixing ONE spelling at a time does not work. Rejecting a
 * trailing '/' closed `link/`, then `link/.` deleted the link target just the
 * same — the slash rule never addressed the actual problem, which is that the
 * kernel resolves `.`, `..` and empty components *after* any link crossing.
 * `lstat("link/.")` reports the TARGET directory, so every later type check is
 * looking at the wrong object.
 *
 * The rule is therefore on the whole spelling, not on its last character:
 *   - absolute
 *   - no trailing '/'
 *   - no empty component ('//')
 *   - no '.' or '..' component anywhere
 *
 * Refusing is cheap and the caller controls these strings. Nothing legitimate in
 * this install path needs `..` or `/.` — the paths are fixed and built by the
 * package, not typed by a user. */
static inline int path_spelling_plain(const char *path) {
    if (!path || path[0] != '/') return 0;
    size_t length = strlen(path);
    if (length == 0 || length >= PATH_MAX) return 0;
    if (path[length - 1] == '/') return 0;
    const char *part = path + 1;
    while (*part) {
        const char *end = strchr(part, '/');
        size_t part_length = end ? (size_t)(end - part) : strlen(part);
        if (part_length == 0) return 0;
        if (part_length == 1 && part[0] == '.') return 0;
        if (part_length == 2 && part[0] == '.' && part[1] == '.') return 0;
        if (!end) break;
        part = end + 1;
    }
    return 1;
}

/* Resolve to an absolute, symlink-free, `.`/`..`-free form. Returns 0 on any
 * failure; callers must treat that as "unknown", never as "different". */
static inline int path_resolve(const char *path, char out[PATH_MAX]) {
    if (!path || !*path) return 0;
    return realpath(path, out) != NULL;
}

/* Like path_resolve, but tolerates a genuinely missing final component: the
 * parent is resolved and the last name re-attached. This is needed for a first
 * install, where the live program path does not exist yet.
 *
 * Only ENOENT is treated as "the last name is absent". Any other realpath
 * failure (EACCES, ELOOP, ENAMETOOLONG, ENOTDIR on the parent, ...) is a
 * resolution failure and must fail closed — an earlier revision fell through to
 * the missing-final-component branch on ANY error, which turned "cannot resolve"
 * into "absent" for every failure mode. */
static inline int path_resolve_allow_missing(const char *path, char out[PATH_MAX]) {
    if (!path || !*path) return 0;
    if (path_resolve(path, out)) return 1;
    if (errno != ENOENT) return 0;
    char parent[PATH_MAX];
    size_t length = strlen(path);
    if (length >= sizeof(parent)) return 0;
    memcpy(parent, path, length + 1);
    char *slash = strrchr(parent, '/');
    if (!slash || slash == parent) return 0;
    const char *base = slash + 1;
    if (!*base || !strcmp(base, ".") || !strcmp(base, "..")) return 0;
    /* ENOENT alone does not mean "the last entry does not exist". A DANGLING
     * SYMLINK at the last entry also makes realpath fail with ENOENT, and then
     * this function would rebuild the path and report success — treating a
     * present-but-broken link as an absent path. Test the entry directly: only a
     * genuine ENOENT from lstat is an absent name. */
    struct stat probe;
    if (!lstat(path, &probe)) return 0; /* it exists (e.g. dangling link) */
    if (errno != ENOENT) return 0;
    *slash = 0;
    char resolved_parent[PATH_MAX];
    if (!path_resolve(parent, resolved_parent)) return 0;
    int written = snprintf(out, PATH_MAX, "%s/%s", resolved_parent, base);
    return written > 0 && written < PATH_MAX;
}

/* 1 when one RESOLVED path contains the other, at a path-component boundary.
 *
 * Both arguments must already be output of path_resolve or
 * path_resolve_allow_missing. They are deliberately not resolved again here:
 * a first install has no live program path to resolve, and re-resolving would
 * turn "the target does not exist yet" into "refuse", which is both wrong and
 * indistinguishable from a real overlap. */
static inline int path_overlaps_resolved(const char *left, const char *right) {
    if (!left || !right || left[0] != '/' || right[0] != '/') return 1;
    if (!strcmp(left, right)) return 1;
    size_t left_length = strlen(left), right_length = strlen(right);
    if (left_length > right_length && left[right_length] == '/' &&
        !strncmp(left, right, right_length)) return 1;
    if (right_length > left_length && right[left_length] == '/' &&
        !strncmp(right, left, left_length)) return 1;
    /* Same object under two names that both resolve cleanly (bind mounts,
     * hard-linked directories are impossible on APFS, but a mounted alias is
     * not). The inode pair is the tie-breaker when neither is a prefix. */
    struct stat left_info, right_info;
    if (!lstat(left, &left_info) && !lstat(right, &right_info) &&
        left_info.st_dev == right_info.st_dev && left_info.st_ino == right_info.st_ino) return 1;
    return 0;
}

#endif
