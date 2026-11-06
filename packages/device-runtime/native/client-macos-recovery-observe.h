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
static enum registered_recovery_observation observe_registered_recovery(
    const char *management,uid_t manager,NSDictionary *paths,
    struct managed_guards *guards,double deadline) {
    if (!guards || guards->start>=0 || guards->runtime>=0 || !isfinite(deadline) ||
        !recovery_time_remaining(deadline) ||
        !registered_management_current(management,manager,paths,guards)) return RECOVERY_UNKNOWN;
    char source[PATH_MAX];
    if (!locate_bound_resources(source,manager)) return RECOVERY_UNKNOWN;
    NSDictionary *expected=read_target_identity(source,manager);
    const char *target=[paths[@"program"] fileSystemRepresentation];
    if (!expected || !target || !recovery_time_remaining(deadline)) return RECOVERY_UNKNOWN;
    NSDictionary *actual=read_target_identity(target,[paths[@"management_owner"] unsignedIntValue]);
    if (!actual || ![actual isEqualToDictionary:expected] || !recovery_time_remaining(deadline) ||
        !registered_management_current(management,manager,paths,guards)) return RECOVERY_UNKNOWN;
    int ready=query_registered_ready(management,manager,paths,guards,
                                    expected[@"version"],expected[@"build_id"],deadline);
    /* A failed query can leave an exact tree but says NOTHING about service
     * absence, loaded definition, idle state, or permission to stop/replace. */
    NSDictionary *final=read_target_identity(target,[paths[@"management_owner"] unsignedIntValue]);
    if (!final || ![final isEqualToDictionary:expected] ||
        !registered_management_current(management,manager,paths,guards) ||
        !recovery_time_remaining(deadline)) return RECOVERY_UNKNOWN;
    return ready ? RECOVERY_READY_EXACT : RECOVERY_TARGET_EXACT;
}
#endif
