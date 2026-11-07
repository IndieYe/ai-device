/* Removal of the old program tree left behind by an atomic swap.
 *
 * After `commit_staged_program` succeeds with RENAME_SWAP, the *previous* tree
 * is sitting at the stage path. This header clears it. It must never be used for
 * anything else: it deletes a program tree, so every input is checked as if it
 * were hostile.
 *
 * Two-phase removal, deliberately:
 *   1. rename the stage to a pid-tagged sibling  (atomic, breaks the name)
 *   2. recursively delete that sibling
 *
 * Phase 1 exists so an interrupted delete can never leave something at the stage
 * path that looks like a fresh payload but holds half an old tree. After phase 1
 * the stage path is free, and a partial remnant is clearly named as debris.
 *
 * Identity, not text. An earlier revision decided "stage is not the live tree"
 * by comparing strings. That is not a path comparison: `/opt/x` and `/opt/./x`
 * are one directory, a symlinked parent makes two strings address one tree, and
 * APFS folds case. The overlap gate now resolves both paths and compares inode
 * pairs, and the delete is descriptor-relative.
 *
 * How this took three attempts, because the lesson matters more than the fix:
 *   1. string comparison                   -> `/opt/./x` aliases bypassed it
 *   2. lstat the raw path, then realpath   -> `link/` followed the link, because
 *                                             a trailing slash makes lstat report
 *                                             the TARGET
 *   3. reject a trailing slash             -> `link/.` deleted the target the same
 *                                             way; the kernel resolves `.` AFTER
 *                                             crossing the link
 * Each fix treated one spelling. The current rule constrains the whole spelling
 * (see path_spelling_plain) and bails on `..`, `.` and empty components outright.
 *
 * Residual windows, stated plainly rather than glossed:
 *   - the rename is bound to the committed inode via fstatat, but binding is a
 *     comparison, not an atomic check-and-rename; a replacement in the instant
 *     between that call and renamex_np is not excluded. The window is now
 *     bounded by "did someone swap this path after commit", not "was this ever
 *     the right tree"
 *   - the parent directory is opened with O_NOFOLLOW, yet the parent path itself
 *     can be replaced before open()
 *   - renamex_np semantics under these conditions are untested on a real system
 *
 * What IS now claimed: the deleted inode is the one `commit_staged_program`
 * displaced, because the receipt is the stat taken immediately before the swap
 * and the stage must still be that exact dev+inode. A tree written to the stage
 * path after the swap has a different inode and is refused. What is still NOT
 * claimed: that the commit itself displaced a tree rather than finding none --
 * that case is handled by the caller not calling cleanup at all (RENAME_EXCL
 * displaced nothing, so the receipt is zeroed).
 */
#ifndef CLIENT_MACOS_STAGE_CLEANUP_H
#define CLIENT_MACOS_STAGE_CLEANUP_H

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "client-macos-path-identity.h"

#define STAGE_CLEANUP_OK 0
#define STAGE_CLEANUP_REFUSED 1
#define STAGE_CLEANUP_INCOMPLETE 2

/* Refuses unless the target resolves, is a real directory owned by `owner`, is
 * not a symlink, sits in a bound parent, and is not the live program path, its
 * ancestor, or its descendant. Overlap is decided on resolved identity; if
 * either path cannot be resolved the answer is "refuse", because an unresolvable
 * path cannot be proven distinct from the program we just installed. */
static inline int stage_cleanup_allowed(const char *stage, const char *program, uid_t owner) {
    if (!stage || !program || !*stage || !*program) return 0;
    /* Check the named entry BEFORE resolving it. realpath would happily follow a
     * symlinked stage to its target, and then every later check would inspect the
     * target instead of the thing we were actually told to delete. The rule is
     * "this exact name must be a real directory", so the link test has to run on
     * the name as given. */
    struct stat named;
    /* Reject the whole spelling, not just the last character.
     *
     * Rejecting a trailing '/' closed `link/` and then `link/.` removed the link
     * target just the same, because the kernel resolves `.`, `..` and empty
     * components AFTER crossing a link: `lstat("link/.")` reports the TARGET, so
     * the S_ISLNK test below inspects the wrong object. Both bypasses were
     * reproduced against this code (target tree deleted, exit 0).
     *
     * `link/..` happened to be caught by the overlap check in one layout, which
     * is luck, not a guarantee. The input contract is therefore plain absolute
     * paths only: no trailing slash, no empty component, no `.`/`..` anywhere. */
    if (!path_spelling_plain(stage)) return 0;
    if (lstat(stage, &named)) return 0;
    if (S_ISLNK(named.st_mode)) return 0;
    if (!S_ISDIR(named.st_mode)) return 0;
    if (named.st_uid != owner) return 0;
    if (named.st_mode & 0022) return 0;
    char resolved_stage[PATH_MAX], resolved_program[PATH_MAX];
    if (!path_resolve(stage, resolved_stage)) return 0;
    /* path_resolve returns the canonical form, which never ends in '/' for a
     * directory and never contains a trailing link component. */
    if (resolved_stage[0] != '/') return 0;
    /* The program path may legitimately be absent on a first install. */
    if (!path_resolve_allow_missing(program, resolved_program)) return 0;
    if (path_overlaps_resolved(resolved_stage, resolved_program)) return 0;
    /* Re-check through the resolved form as well: a symlinked PARENT is still
     * acceptable (it is how /opt is often laid out), but the final object must
     * still be the same directory we lstat'd above. */
    struct stat info;
    if (lstat(resolved_stage, &info)) return 0;
    if (S_ISLNK(info.st_mode)) return 0;
    if (!S_ISDIR(info.st_mode)) return 0;
    if (info.st_dev != named.st_dev || info.st_ino != named.st_ino) return 0;
    if (info.st_uid != owner) return 0;
    if (info.st_mode & 0022) return 0;
    char parent[PATH_MAX];
    size_t length = strlen(resolved_stage);
    if (length >= sizeof(parent)) return 0;
    memcpy(parent, resolved_stage, length + 1);
    char *slash = strrchr(parent, '/');
    if (!slash || slash == parent || !slash[1]) return 0;
    *slash = 0;
    if (!bound_directory(parent, owner)) return 0;
    return 1;
}

/* Runs the gate, then re-observes the stage and returns that observation.
 *
 * WARNING — this is NOT the binding it looks like, and an earlier comment here
 * claimed it was. `stage_cleanup_allowed` returns only a bool, so this wrapper
 * cannot hand back the stat the gate saw; it has to lstat again. The two
 * observations therefore happen at different times, and an object swapped in
 * between is returned as if it had passed. The reviewer caught exactly this:
 * "returns the validated stat" was not true of the code.
 *
 * What it does buy: the returned inode is the one that the RESOLVED path and the
 * raw path agreed on at this moment, so the later fstatat comparison is at least
 * anchored to a single observation instead of two independent ones. That is a
 * narrowing, not a closure, and it must not be described as atomic binding.
 *
 * The real fix for this gap is not another wrapper: it is the cleanup being
 * authorised by the coordinator with the commit receipt and the old tree's
 * identity, instead of an entry point that takes two path strings from argv. */
static inline int stage_cleanup_validate(const char *stage, const char *program,
                                         uid_t owner, struct stat *validated,
                                         char resolved[PATH_MAX]) {
    if (!validated || !resolved) return 0;
    if (!stage_cleanup_allowed(stage, program, owner)) return 0;
    struct stat named;
    if (lstat(stage, &named)) return 0;
    if (!path_resolve(stage, resolved)) return 0;
    struct stat info;
    if (lstat(resolved, &info)) return 0;
    if (info.st_dev != named.st_dev || info.st_ino != named.st_ino) return 0;
    *validated = info;
    return 1;
}

/* Depth-first delete of the entry `name` inside `parent`, never following a
 * symlink out of the tree.
 *
 * Everything is relative to an open descriptor: the directory we recurse into is
 * the one we opened, not the one the name resolves to a moment later. A child
 * that is swapped for a symlink between listing and opening is caught by
 * O_NOFOLLOW — openat fails, and we unlink the *link* instead of walking its
 * target.
 *
 * Returns 1 only when the whole subtree is gone. */
static inline int remove_tree_at(int parent, const char *name) {
    int fd = openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) {
        if (errno == ENOENT) return 1;
        /* Not a directory, or a symlink: remove the entry itself, never follow. */
        if (errno == ENOTDIR || errno == ELOOP) return unlinkat(parent, name, 0) == 0;
        return 0;
    }
    DIR *directory = fdopendir(fd);
    if (!directory) {
        close(fd);
        return 0;
    }
    int ok = 1;
    struct dirent *entry;
    errno = 0;
    while ((entry = readdir(directory))) {
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) {
            errno = 0;
            continue;
        }
        if (!remove_tree_at(dirfd(directory), entry->d_name)) {
            ok = 0;
            break;
        }
        errno = 0;
    }
    /* readdir returning NULL is end-of-stream only when errno stayed clear;
     * otherwise we stopped early and the tree is not fully enumerated. */
    if (ok && errno) ok = 0;
    closedir(directory);
    if (!ok) return 0;
    return unlinkat(parent, name, AT_REMOVEDIR) == 0;
}

/* Confirms the entry `name` in `parent_fd` is still the object described by
 * `expected`.
 *
 * Split out as its own function precisely so the check/use binding can be tested:
 * in production the gap between validation and rename is hard to hit on purpose,
 * but the predicate itself is easy to feed a mismatched pair. Returns 1 only when
 * the entry is a non-symlink directory owned by `owner`, not group/other writable,
 * and the same device+inode as `expected`. */
static inline int stage_entry_bound(int parent_fd, const char *name,
                                    const struct stat *expected, uid_t owner) {
    struct stat bound;
    if (fstatat(parent_fd, name, &bound, AT_SYMLINK_NOFOLLOW)) return 0;
    if (S_ISLNK(bound.st_mode) || !S_ISDIR(bound.st_mode)) return 0;
    if (bound.st_uid != owner || (bound.st_mode & 0022)) return 0;
    return bound.st_dev == expected->st_dev && bound.st_ino == expected->st_ino;
}

/* Removes the swapped-out old tree.
 *
 * Only call after a successful commit: before that, the stage holds the NEW tree
 * and deleting it destroys the only downloaded copy. Returns INCOMPLETE when the
 * directory was renamed away but not fully deleted — the install is still valid,
 * the residue is just debris, and the caller must report it rather than retry.
 *
 * `replaced` is the commit receipt: the stat of the tree that RENAME_SWAP
 * displaced, captured by `commit_staged_program` immediately before the swap.
 *
 * This is the whole difference between this function and the withdrawn cleanup
 * verb. That verb took two path strings and had to guess whether the stage still
 * held the displaced tree; every spelling of that guess was eventually defeated
 * (see the header comment). Here the caller states which inode it displaced, and
 * the stage must BE that inode or nothing is deleted. A replacement written after
 * the swap has a different inode and is refused.
 *
 * NULL means "the caller has no receipt". That is refused rather than degraded
 * to a path-only check -- falling back is exactly how the old hole would return.
 * On a first install (RENAME_EXCL) nothing was displaced, so the caller must not
 * call this at all.
 *
 * The parent directory is opened once and every later step is relative to that
 * descriptor, so the rename cannot be redirected by swapping the parent path.
 */
static inline int cleanup_swapped_stage(const char *stage, const char *program, uid_t owner,
                                        const struct stat *replaced) {
    /* Validate ONCE and keep the identity the gate approved. The earlier shape
     * validated, returned a bool, then re-lstat'd — so the rename was bound to a
     * fresh observation rather than to the checked object. */
    if (!replaced) return STAGE_CLEANUP_REFUSED;
    struct stat validated;
    char resolved[PATH_MAX];
    if (!stage_cleanup_validate(stage, program, owner, &validated, resolved))
        return STAGE_CLEANUP_REFUSED;
    char parent[PATH_MAX];
    if (strlen(resolved) >= sizeof(parent)) return STAGE_CLEANUP_REFUSED;
    strcpy(parent, resolved);
    char *slash = strrchr(parent, '/');
    if (!slash || slash == parent || !slash[1]) return STAGE_CLEANUP_REFUSED;
    const char *base = slash + 1;
    *slash = 0;
    /* The debris name must fit next to the stage name, not be truncated. */
    char debris[NAME_MAX + 1];
    int written = snprintf(debris, sizeof(debris), "%s.old-%ld", base, (long)getpid());
    if (written < 0 || written >= (int)sizeof(debris)) return STAGE_CLEANUP_REFUSED;
    int parent_fd = open(parent, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (parent_fd < 0) return STAGE_CLEANUP_REFUSED;
    /* Bind to the inode the COMMIT displaced, not to the one this call happened
     * to observe. A stage replaced after the swap fails here, which is the point:
     * the previous shape compared the observation with itself and could only
     * prove "this path looked acceptable", never "this is the tree I displaced". */
    if (!stage_entry_bound(parent_fd, base, replaced, owner) ||
        validated.st_dev != replaced->st_dev || validated.st_ino != replaced->st_ino) {
        close(parent_fd);
        return STAGE_CLEANUP_REFUSED;
    }
    /* RENAME_EXCL, not rename: an existing debris entry means an earlier cleanup
     * was interrupted, and overwriting it would silently destroy that evidence. */
    if (renameatx_np(parent_fd, base, parent_fd, debris, RENAME_EXCL)) {
        close(parent_fd);
        return STAGE_CLEANUP_REFUSED;
    }
    /* Phase 2: delete the debris. A failure here does not invalidate the
     * install, and the residue is already named as debris. */
    int removed = remove_tree_at(parent_fd, debris);
    close(parent_fd);
    if (!removed) return STAGE_CLEANUP_INCOMPLETE;
    return STAGE_CLEANUP_OK;
}

#endif
