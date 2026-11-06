/* Read-only after a prior handoff/unknown start. Include after target-identity
 * and start-handoff. No acquire/start/exit/publish/rollback or automatic retry.
 */
#ifndef CLIENT_MACOS_RECOVERY_OBSERVE_H
#define CLIENT_MACOS_RECOVERY_OBSERVE_H
enum registered_recovery_observation {
    RECOVERY_UNKNOWN=0,
    RECOVERY_TARGET_EXACT=1,
    RECOVERY_READY_EXACT=2
};
static int recovery_time_remaining(double deadline) {
    double now=lease_clock();
    return isfinite(now) && now>=0 && now<deadline;
}
/* Confirm the live program tree is this build.
 *
 * The expected identity is NOT taken from a tree beside this executable: a
 * shipping package carries exactly one tree, and after a commit the staged
 * source has become the PREVIOUS tree (renamex_np SWAP leaves it there).
 * Reading the expectation from that path would compare the new live tree
 * against the old tree's identity and report UNKNOWN on a successful upgrade.
 *
 * The strength of the check never came from the location of a second copy.
 * `read_target_identity` verifies the whole tree against `resource-table.h`,
 * which is compiled into this binary, and re-checks ownership and stability.
 * A tree that is not this build cannot pass that regardless of where it sits.
 * So the live path is both the thing to verify and the trustworthy source of
 * the identity to compare against: it can only return non-nil after the
 * embedded inventory matched every entry.
 */
static NSDictionary *read_live_target_identity(NSDictionary *paths,uid_t owner) {
    const char *target=[paths[@"program"] fileSystemRepresentation];
    if (!target || !*target) return nil;
    return read_target_identity(target,owner);
}

static enum registered_recovery_observation observe_registered_recovery(
    const char *management,uid_t manager,NSDictionary *paths,
    struct managed_guards *guards,double deadline) {
    if (!guards || guards->start>=0 || guards->runtime>=0 || !isfinite(deadline) ||
        !recovery_time_remaining(deadline) ||
        !registered_management_current(management,manager,paths,guards)) return RECOVERY_UNKNOWN;
    NSDictionary *expected=read_live_target_identity(paths,manager);
    const char *target=[paths[@"program"] fileSystemRepresentation];
    if (!expected || !target || !recovery_time_remaining(deadline) ||
        !registered_management_current(management,manager,paths,guards)) return RECOVERY_UNKNOWN;
    int ready=query_registered_ready(management,manager,paths,guards,
                                    expected[@"version"],expected[@"build_id"],deadline);
    /* A failed query can leave an exact tree but says NOTHING about service
     * absence, loaded definition, idle state, or permission to stop/replace.
     *
     * The re-read below is a stability check, not a second identity source: the
     * tree must still be the one accepted above. Reading the same path again
     * and comparing it to itself would be a tautology that proves nothing. */
    NSDictionary *final=read_target_identity(target,[paths[@"management_owner"] unsignedIntValue]);
    if (!final || ![final isEqualToDictionary:expected] ||
        !registered_management_current(management,manager,paths,guards) ||
        !recovery_time_remaining(deadline)) return RECOVERY_UNKNOWN;
    return ready ? RECOVERY_READY_EXACT : RECOVERY_TARGET_EXACT;
}
#endif
