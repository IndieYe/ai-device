/* Standalone commit executable shipped inside the package payload.
 *
 * This is the binary the postinstall script actually invokes, so its argument
 * contract must be the one the generated script uses. Both sides derive their
 * verb words from client-macos-program-verbs.h; see that header for why.
 *
 * Two verbs, deliberately separate:
 *
 *   commit   <stage> <program>   one atomic swap, keeps the old tree at stage
 *   cleanup  <stage> <program>   removes the swapped-out old tree
 *
 * They are not combined because cleaning up before a confirmed commit would
 * delete the only copy of the new program, and cleaning up immediately after
 * one would delete a tree a still-running instance may be using. The caller
 * decides when the second condition no longer holds.
 *
 * Exit codes are part of the contract the package script reads:
 *   0   the requested action completed
 *   64  usage or unknown verb — nothing was attempted
 *   70  refused, or the action was attempted and its outcome is unknown
 */
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "client-macos-bound-resources.h"
#include "client-macos-tree-owner.h"
#include "client-macos-program-verbs.h"
#include "client-macos-program-commit.h"
#include "client-macos-stage-cleanup.h"

int main(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "usage: %s <" PROGRAM_VERB_COMMIT "|" PROGRAM_VERB_CLEANUP
                        "> <stage> <program>\n", argv[0]);
        return 64;
    }
    /* Validate the verb BEFORE the identity gate. A usage error is a usage error
     * whoever runs it: reporting 70 to an unprivileged caller with a misspelled
     * verb hides the real problem and sends the reader after permissions. */
    int is_cleanup = !strcmp(argv[1], PROGRAM_VERB_CLEANUP);
    if (!is_cleanup && strcmp(argv[1], PROGRAM_VERB_COMMIT)) {
        fprintf(stderr, "unknown verb: %s\n", argv[1]);
        return 64;
    }
    if (getuid() != 0 || geteuid() != 0) {
        fprintf(stderr, "program commit and cleanup require administrator identity\n");
        return 70;
    }
    if (is_cleanup) {
        /* Disabled on purpose, and this is a decision rather than an oversight.
         *
         * Four review rounds found four ways to reach a directory that was not
         * the swapped-out old tree: `/opt/./x`, `link/`, `link/.`, and finally
         * "a plain absolute path whose MIDDLE component is a link", which no
         * amount of spelling validation can rule out. Each fix closed one
         * spelling. The shape check below now rejects the syntax-level cases,
         * but the underlying gap is not syntax: this entry takes two path
         * strings and cannot prove that the first one is the tree the commit
         * just put aside, nor that anything still needs it gone.
         *
         * Deleting a program tree on that basis is not safe to expose, so the
         * verb now refuses unconditionally. The implementation stays compiled
         * (so it keeps being type-checked and its tests keep exercising the
         * invariants) but has no callable path until cleanup is driven by the
         * coordinator with a commit receipt and the old tree's identity.
         *
         * Re-enabling requires that redesign, not another spelling fix. */
        fprintf(stderr,
                "cleanup is disabled pending coordinator-authorised cleanup "
                "(commit receipt + old tree identity)\n");
        puts("STAGE_CLEANUP_DISABLED_V1");
        return 64;
    }
    switch (commit_staged_program(argv[2], argv[3], 0, NULL, NULL)) {
        case PROGRAM_COMMIT_OK:
            puts("PROGRAM_COMMITTED_V1");
            return 0;
        case PROGRAM_COMMIT_REFUSED:
            puts("PROGRAM_COMMIT_REFUSED_V1");
            return 70;
        default:
            /* The rename failed; neither outcome is provable from here. */
            puts("PROGRAM_COMMIT_UNKNOWN_V1");
            return 70;
    }
}
