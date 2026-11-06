/* Atomic commit of an already-verified bound resource tree to the fixed
 * program path.
 *
 * Why this exists: a package installer writes its payload in place. If the
 * payload *were* the live program path, those writes would mutate a tree the
 * running client may be executing from, and no lock can span the gap, because
 * preinstall and postinstall are separate processes and a script's lock dies
 * with the process. Rather than pretend a lock can cover that window, this
 * entry removes the window: the payload is written to a stage path that is never
 * the live path, and exactly one atomic rename publishes it.
 *
 * Scope, deliberately narrow:
 *   - It does not build, repair, verify from an external inventory, register,
 *     start, stop, boot out or roll back anything.
 *   - It does not acquire the management lock. Publication is a single
 *     renamex_np; the caller is the one that must have serialised installers.
 *     Holding a lock here would imply a guarantee this code cannot give.
 *   - It reports a token, and the caller must not read that as "started" or
 *     "installed successfully".
 *
 * Input contract: the stage path must already contain the complete, final tree
 * owned by the target owner. The check below is a negative gate (it refuses
 * obviously wrong input), not a positive proof that the tree is the intended
 * version — only the build's own inventory can attest that.
 */
#ifndef CLIENT_MACOS_PROGRAM_COMMIT_H
#define CLIENT_MACOS_PROGRAM_COMMIT_H

#define PROGRAM_COMMIT_OK 0
#define PROGRAM_COMMIT_REFUSED 1
#define PROGRAM_COMMIT_UNKNOWN 2

/* Overlap decisions are made on resolved path identity, shared with the cleanup
 * entry so the two cannot disagree about what "the same tree" means. */
#include "client-macos-path-identity.h"

/* Refuses when the stage is absent, is not a directory, is not owned by the
 * target owner, or when the parent of the live path is not a valid bound
 * directory. Then performs one RENAME_SWAP/RENAME_EXCL.
 *
 * Returns UNKNOWN when the rename itself failed: renamex_np is atomic, but a
 * failure after the kernel accepted the request cannot be distinguished from a
 * failure before it by exit status alone, so the caller must verify rather than
 * retry.
 *
 * `verify` is optional. The plain commit exists to publish a tree that the
 * Installer just wrote, and its caller has no inventory to compare against, so
 * it passes NULL and keeps the original owner/structure checks. The registered
 * upgrade path passes `verify_resource_tree` because it publishes a tree it did
 * not write itself; without that callback the only remaining check would be
 * ownership, which is weaker than what the upgrade used to get from shipping a
 * second copy of the tree. Called after the path and ownership checks and
 * immediately before the swap, so a refusal here has still performed no rename. */
static inline int commit_staged_program(const char *stage, const char *program, uid_t owner,
                                        int (*verify)(const char *root)) {
    if (!stage || !program || !*stage || !*program) return PROGRAM_COMMIT_REFUSED;
    /* Refuse identical paths: committing a stage onto itself would be a no-op
     * reported as success, which is the worst possible answer here. */
    struct stat stage_info, program_info, parent_info;
    if (lstat(stage, &stage_info) || !S_ISDIR(stage_info.st_mode) ||
        stage_info.st_uid != owner || (stage_info.st_mode & 0022))
        return PROGRAM_COMMIT_REFUSED;
    if (!tree_owner_valid(stage, owner, 0)) return PROGRAM_COMMIT_REFUSED;
    char parent[PATH_MAX];
    size_t length = strlen(program);
    if (length >= sizeof(parent)) return PROGRAM_COMMIT_REFUSED;
    memcpy(parent, program, length + 1);
    char *slash = strrchr(parent, '/');
    if (!slash || slash == parent || !slash[1]) return PROGRAM_COMMIT_REFUSED;
    *slash = 0;
    if (!bound_directory(parent, owner)) return PROGRAM_COMMIT_REFUSED;
    /* Overlap on resolved identity, not on text. `/opt/x` vs `/opt/./x`, a
     * symlinked parent and APFS case folding all defeat strncmp, and the failure
     * mode here is publishing a tree onto itself. Refuse when either path will
     * not resolve: an unprovable relationship is a refusal, not a pass.
     *
     * The program path legitimately does not exist on a first install, so it is
     * resolved through its parent, which `bound_directory` has already checked. */
    char resolved_stage[PATH_MAX], resolved_program[PATH_MAX];
    if (!path_resolve(stage, resolved_stage)) return PROGRAM_COMMIT_REFUSED;
    if (!path_resolve_allow_missing(program, resolved_program)) return PROGRAM_COMMIT_REFUSED;
    if (path_overlaps_resolved(resolved_stage, resolved_program)) return PROGRAM_COMMIT_REFUSED;
    unsigned int flags;
    if (!lstat(program, &program_info)) {
        if (!S_ISDIR(program_info.st_mode)) return PROGRAM_COMMIT_REFUSED;
        flags = RENAME_SWAP;
    } else if (errno == ENOENT) {
        flags = RENAME_EXCL;
    } else {
        return PROGRAM_COMMIT_REFUSED;
    }
    /* Parent re-validation immediately before the swap: the stage could have
     * been replaced by a wider-owning directory while it was being inspected. */
    if (lstat(parent, &parent_info) || !S_ISDIR(parent_info.st_mode) ||
        parent_info.st_uid != owner || (parent_info.st_mode & 0022) ||
        !bound_directory(parent, owner))
        return PROGRAM_COMMIT_REFUSED;
    /* The caller's optional inventory check runs last among the pre-swap
     * checks: it is the most expensive, and reaching it means the cheaper path
     * and ownership checks already passed. Refusal here performs no rename. */
    if (verify && !verify(stage)) return PROGRAM_COMMIT_REFUSED;
    if (renamex_np(stage, program, flags)) return PROGRAM_COMMIT_UNKNOWN;
    return PROGRAM_COMMIT_OK;
}

#endif
