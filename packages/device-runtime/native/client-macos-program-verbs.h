/* Single source of truth for the program commit/cleanup verb words.
 *
 * Why this exists: the postinstall script asked for `commit-program` while the
 * packaged binary accepted only `commit`, so the one path that actually ships
 * could never publish a tree — it exited 64 on an unknown verb. Nothing caught
 * it because the script test matched the text it had just generated, and the
 * entry test exercised a different wrapper that accepted the other spelling.
 *
 * The fix is not "align two spellings by hand". Both the generated script and
 * the binary now derive the verbs from this header, so a rename has one edit
 * site, and a new verb cannot be added in one place and forgotten in the other.
 *
 * Keep this file free of C-only constructs: scripts/build-client-pkg.py reads
 * the string literals with a plain regex.
 */
#ifndef CLIENT_MACOS_PROGRAM_VERBS_H
#define CLIENT_MACOS_PROGRAM_VERBS_H

#define PROGRAM_VERB_COMMIT "commit"
#define PROGRAM_VERB_CLEANUP "cleanup"

#endif
