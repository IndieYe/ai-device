/* Fixed root read-only observer. Not wired into any PKG; never run in tests.
 * Compile with resource-table.h for the exact sibling resources/client-tree.
 */
#define main offline_writer_main
#define EMBEDDED_RESOURCE_CHECK
#include "client-macos-writer-probe.c"
#undef main
#define INSTALL_PREFLIGHT_LIBRARY
#include "client-macos-install-preflight.m"
#include "client-macos-lease-request.m"
#include "client-macos-registered-exit.h"
#include "client-macos-recovery-check.h"
#include "client-macos-bound-resources.h"
#include "client-macos-ready-request.h"
#include "client-macos-start-handoff.h"
#include "client-macos-target-identity.h"
#include "client-macos-recovery-observe.h"
#include "client-macos-recovery-entry.h"

int main(int argc,char **argv) {
    @autoreleasepool {
        (void)prepare_registered;
        (void)exit_registered_original;
        (void)registered_target_exact;
        (void)handoff_registered_start;
        return recovery_observe_entry(argc,argv);
    }
}
