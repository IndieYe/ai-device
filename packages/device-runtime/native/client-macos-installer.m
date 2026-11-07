/* Native entry for the client program. NOT wired into inspection PKGs.
 * Compile against the exact resource-table.h of the bound client-tree.
 * First-program publication, unregistered observation and the explicit
 * single-attempt registered upgrade are the only supported verbs; install,
 * registration repair and replay remain intentionally unsupported.
 */
#define main offline_writer_main
#define EMBEDDED_RESOURCE_CHECK
#include "client-macos-writer-probe.c"
#undef main
#define IDENTITY_LIBRARY
#include "client-macos-identity.m"
#include "client-macos-bound-resources.h"
#include "client-macos-host-conflicts.h"
#include "client-macos-first-publish.h"
#include "client-macos-first-host-publish.h"
#include "client-macos-management-provision.h"
#include "client-macos-first-install.h"
#include "client-macos-first-entry.h"
#include "client-macos-unregistered-entry.h"
/* install-preflight.m also includes identity.m; its own guard makes that a
 * no-op here, so these guards can be included unconditionally. */
#define INSTALL_PREFLIGHT_LIBRARY
#include "client-macos-install-preflight.m"
#undef INSTALL_PREFLIGHT_LIBRARY
/* Dependency order mirrors the reviewed translation units: lease protocol,
 * readiness/handoff, target identity, then observation/publication and finally
 * the coordinator that composes them. */
#include "client-macos-lease-request.m"
#include "client-macos-ready-request.h"
#include "client-macos-recovery-check.h"
#include "client-macos-registered-exit.h"
/* The registered upgrade publishes by renaming the staged payload rather than
 * copying it, so it needs the commit primitive here as well. */
#include "client-macos-program-commit.h"
/* Deleting the tree the swap left behind. Required before
 * client-macos-registered-upgrade.h, which calls it once the new instance is
 * serving and the old tree is provably unused. */
#include "client-macos-stage-cleanup.h"
#include "client-macos-start-handoff.h"
#include "client-macos-target-identity.h"
#include "client-macos-registered-publish.h"
#include "client-macos-start-child.h"
#include "client-macos-service-relay.h"
#include "client-macos-registered-upgrade.h"
#include "client-macos-upgrade-entry.h"
#include "client-macos-repair-entry.h"
#include "client-macos-program-commit-entry.h"
#include "client-macos-program-verbs.h"

int main(int argc,char **argv) {
    @autoreleasepool {
        (void)managedPaths;
        (void)acquireManagement;
        /* These reviewed units stay compiled in for the upgrade path even when
         * only their dependencies are reachable from the current verbs. */
        (void)prepare_registered;
        (void)request_bound_registered_start;
        if (argc>1 && !strcmp(argv[1],"observe-unregistered-program"))
            return unregistered_observe_entry(argc,argv);
        if (argc>1 && !strcmp(argv[1],"upgrade-registered-program"))
            return upgrade_registered_entry(argc,argv);
        if (argc>1 && !strcmp(argv[1],"repair-registered-program"))
            return repair_registered_entry(argc,argv);
        if (argc>1 && !strcmp(argv[1],PROGRAM_VERB_COMMIT))
            return program_commit_entry(argc,argv);
        return first_install_entry(argc,argv);
    }
}
