/* Command wrapper for the staged-program commit.
 *
 * Separate from the pure function so the entry can be exercised with a
 * recording substitute, exactly like the other native entries here. It owns no
 * policy: root identity, argument shape, and one call.
 *
 * It is invoked by the package's postinstall, which already runs as root inside
 * the installer transaction. It deliberately does not take the management lock:
 * holding it here would imply that concurrent installers are excluded, and this
 * entry cannot check that. The lock of record for upgrades remains the
 * coordinator's; this command only performs the rename.
 */
#ifndef CLIENT_MACOS_PROGRAM_COMMIT_ENTRY_H
#define CLIENT_MACOS_PROGRAM_COMMIT_ENTRY_H
#include "client-macos-program-commit.h"
#include "client-macos-program-verbs.h"

/* Same verb word as the packaged binary and the generated postinstall. */
static int program_commit_entry(int argc, char **argv) {
    if (argc != 4 || strcmp(argv[1], PROGRAM_VERB_COMMIT)) {
        fprintf(stderr, "usage: %s " PROGRAM_VERB_COMMIT " <stage> <program>\n", argv[0]);
        return 64;
    }
    if (getuid() != 0 || geteuid() != 0) {
        fprintf(stderr, "program commit requires administrator identity\n");
        return 70;
    }
    int result = commit_staged_program(argv[2], argv[3], 0, NULL);
    switch (result) {
        case PROGRAM_COMMIT_OK:
            puts("PROGRAM_COMMITTED_V1");
            return 0;
        case PROGRAM_COMMIT_REFUSED:
            puts("PROGRAM_COMMIT_REFUSED_V1");
            return 70;
        default:
            /* Neither "done" nor "not done" is provable from here. */
            puts("PROGRAM_COMMIT_UNKNOWN_V1");
            return 70;
    }
}

#endif
