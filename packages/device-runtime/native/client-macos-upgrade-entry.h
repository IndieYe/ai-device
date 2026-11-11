/* Explicit single-attempt registered upgrade entry. NOT wired into inspection
 * PKGs, and it never installs, registers, repairs or replays.
 *
 * This entry only composes already-reviewed primitives: identity-bound
 * management/start acquisition, the frozen target identity from the bound
 * resource tree, and upgrade_registered_once. It does not decide whether a
 * replacement is permitted; that remains the caller's/Installer's contract.
 *
 * Requires an explicit acknowledgement flag because the call irreversibly
 * requests the running instance to exit and swaps the program tree.
 *
 * The coordinator returns a stage code rather than a bare success flag, because
 * the on-disk state differs per stage. Two of those stages are recoverable by a
 * single bootstrap of the still-present definition (unload never removes the
 * plist); the rest are reported and left alone.
 */
#include "client-macos-upgrade-stages.h"
static int upgrade_registered_entry(int argc,char **argv) {
    if (argc!=4 || strcmp(argv[1],"upgrade-registered-program") ||
        strcmp(argv[2],"--confirm-idle-instance-exits")) return 64;
    if (getuid()!=0 || geteuid()!=0) return 70;
    /* The staged payload this package just wrote. Passing it in, instead of
     * carrying a second copy of the tree beside this executable, is what keeps
     * one install to one tree: the embedded inventory (~300 KB) verifies a
     * source it does not itself contain. */
    const char *source=argv[3];
    if (!source[0] || source[0]!='/') return 64;
    const char *management="/Library/Application Support/ai-device-client-management";
    struct managed_guards guards={.management=-1,.start=-1,.runtime=-1,.identity=nil};
    double now=lease_clock();
    if (!isfinite(now) || now<0) return 70;
    double deadline=now+120;
    NSDictionary *paths=prepare_start_barrier(management,0,&guards);
    if (!paths) {release_guards(&guards);return 70;}
    /* start is held; exit_registered_original takes runtime only after the
     * idle instance has confirmed its own exit. A busy instance returns
     * LEASE_UNKNOWN and this entry stops without replacement or retry. */
    int stage=upgrade_registered_staged_once(management,0,paths,&guards,source,deadline);
    if (stage==UPGRADE_STAGE_OK) {
        release_guards(&guards);
        puts("AI_DEVICE_UPGRADE_READY_EXACT_V1");
        return 0;
    }
    /* The job is unloaded at these two stages and the definition still exists,
     * so one bootstrap of the same definition restores service without a
     * reinstall and without rolling the program back. Locks are re-verified
     * inside; if that fails nothing is touched and the stage is reported. */
    if ((stage==UPGRADE_STAGE_AFTER_UNLOAD || stage==UPGRADE_STAGE_AFTER_HANDOFF)) {
        if (upgrade_recover_after_stage(management,0,paths,&guards,stage,deadline)) {
            release_guards(&guards);
            puts("AI_DEVICE_UPGRADE_RECOVERED_LOADED_NOT_READY_V1");
            return 70;
        }
        release_guards(&guards);
        puts("AI_DEVICE_UPGRADE_RECOVERY_REFUSED_OR_FAILED_V1");
        return 70;
    }
    release_guards(&guards);
    /* No recovery on these stages on purpose: a side effect was attempted and
     * its outcome is unknown, or the job is already loaded. */
    switch (stage) {
        case UPGRADE_STAGE_EXIT_UNKNOWN:
            puts("AI_DEVICE_UPGRADE_EXIT_UNKNOWN_V1");
            break;
        case UPGRADE_STAGE_UNLOAD_UNKNOWN:
            puts("AI_DEVICE_UPGRADE_UNLOAD_UNKNOWN_V1");
            break;
        case UPGRADE_STAGE_PUBLISH_UNKNOWN:
            puts("AI_DEVICE_UPGRADE_PUBLISH_UNKNOWN_V1");
            break;
        case UPGRADE_STAGE_HANDOFF_UNKNOWN:
            /* Published, but the start/runtime release was not confirmed. Not
             * recoverable here: a bootstrap could race a launcher blocked on a
             * lock this process may still hold. */
            puts("AI_DEVICE_UPGRADE_HANDOFF_UNKNOWN_V1");
            break;
        case UPGRADE_STAGE_AFTER_LOAD:
            puts("AI_DEVICE_UPGRADE_UNKNOWN_AT_LOAD_V1");
            break;
        case UPGRADE_STAGE_NOT_READY:
            puts("AI_DEVICE_UPGRADE_LOADED_NOT_READY_V1");
            break;
        default:
            puts("AI_DEVICE_UPGRADE_REFUSED_V1");
            break;
    }
    return 70;
}
