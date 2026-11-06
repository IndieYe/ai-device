/* Internal single-attempt coordinator, not a launchd ownership proof.
 * Include after exit, fixed resources, service relay, registered publish.
 * Caller already owns management/start, not runtime. Never acquires/releases
 * management, retries requests, rolls back, or sweeps other services.
 *
 * Ordering (requirement 6.3/7.3): once the idle instance confirms its own exit,
 * the registered job is booted out BEFORE the program is replaced, so KeepAlive
 * cannot restart the old program inside the replacement window. After the swap
 * the job is bootstrapped again; `bootstrap` honours RunAtLoad, so it is the
 * single start request. The previous kickstart-after-handoff shape is no longer
 * used: kickstart only acts on an already loaded job.
 *
 * The start/runtime handoff is still mandatory and happens BEFORE `bootstrap`:
 * launchd runs the launcher as soon as the job is loaded, and that launcher
 * takes start.lock then runtime.lock with LOCK_NB. If this coordinator still
 * held either lock, the launcher would exit without exec and the job would sit
 * loaded-but-idle. Only the management lock is retained across the reload.
 *
 * `bootstrap` returns when the job is loaded, not when the target is serving.
 * Readiness is therefore observed by bounded polling of the read-only status
 * query; that is observation, never a replay of the start request.
 */
static int upgrade_time_remaining(double deadline) {
    double now=lease_clock();
    return isfinite(now) && now>=0 && now<deadline;
}

/* Bounded observation of the read-only status query. `bootstrap` only loads the
 * job, so the target may not be serving yet. A mismatched identity cannot be
 * told apart from "not yet serving" here, so every unsuccessful attempt fails
 * closed by exhausting the caller's deadline; nothing is retried for effect.
 */
static int upgrade_wait_ready(const char *management,uid_t manager,NSDictionary *paths,
                              struct managed_guards *guards,NSString *version,NSString *build,
                              double deadline) {
    for (int attempt=0; attempt<1000; attempt++) {
        if (!upgrade_time_remaining(deadline)) return 0;
        if (query_registered_ready(management,manager,paths,guards,version,build,deadline))
            return 1;
        double now=lease_clock();
        if (!isfinite(now) || now<0) return 0;
        double pause=deadline-now;
        if (!(pause>0)) return 0;
        if (pause>0.05) pause=0.05;
        struct timespec nap={(time_t)pause,(long)((pause-(double)(time_t)pause)*1e9)};
        if (nanosleep(&nap,NULL)) return 0;
    }
    return 0;
}

/* Depends on the coordinator primitives plus the read-only recovery checks;
 * the entry translation unit includes them in this order. Not self-contained on
 * purpose: the leaf observables are supplied by the enclosing translation unit
 * so tests can substitute them without pulling in lock or launchd code. */
#include "client-macos-upgrade-stages.h"

static inline int upgrade_registered_once(const char *management,uid_t manager,
                                   NSDictionary *paths,struct managed_guards *guards,
                                   NSString *version,NSString *build,double deadline,
                                   int (*request_start)(void *),void *start_context) {
    (void)request_start;
    (void)start_context;
    if (![version isKindOfClass:[NSString class]] || !version.length ||
        ![build isKindOfClass:[NSString class]] || build.length!=71 ||
        ![build hasPrefix:@"sha256:"] ||
        [[build substringFromIndex:7] rangeOfCharacterFromSet:
            [[NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"] invertedSet]].location!=NSNotFound ||
        !isfinite(deadline) || !upgrade_time_remaining(deadline) ||
        !registered_exit_current(management,manager,paths,guards)) return UPGRADE_STAGE_REFUSED;
    /* Validate immutable input BEFORE irreversibly requesting the old exit. */
    char source[PATH_MAX];
    if (!locate_bound_resources(source,manager) || !verify_resource_tree(source) ||
        !tree_owner_valid(source,manager,0) || !upgrade_time_remaining(deadline))
        return UPGRADE_STAGE_REFUSED;
    /* A busy instance never accepted the exit request, so nothing was
     * requested and nothing was swapped: that is a clean refusal, and it is the
     * answer the requirement calls for ("busy refuses the upgrade, keeps normal
     * admission"). Only an attempted request whose outcome is unconfirmed stays
     * unknown. Reporting BUSY as EXIT_UNKNOWN would tell the caller a side
     * effect may have happened when none did, and the operator contract for the
     * two codes differs: REFUSED may be retried, EXIT_UNKNOWN must not be. */
    int exited = exit_registered_original(management,manager,paths,guards,deadline);
    if (exited == LEASE_BUSY) return UPGRADE_STAGE_REFUSED;
    if (exited != LEASE_ACCEPTED) return UPGRADE_STAGE_EXIT_UNKNOWN;
    if (!registered_guards_current(management,manager,paths,guards) ||
        !upgrade_time_remaining(deadline)) return UPGRADE_STAGE_EXIT_UNKNOWN;
    /* No restart source may survive the swap: unload before publishing. A
     * failed unload does NOT prove the job is still loaded, so its state is
     * unknown. Loading the definition here could start a second instance. */
    if (!unload_registered_service_relay(management,manager,guards->management,deadline))
        return UPGRADE_STAGE_UNLOAD_UNKNOWN;
    if (!registered_guards_current(management,manager,paths,guards) ||
        !upgrade_time_remaining(deadline)) return UPGRADE_STAGE_AFTER_UNLOAD;
    /* Publication is the point of no return for the program tree: it may have
     * replaced the tree and then failed verification, so the on-disk version is
     * no longer certain and must not be loaded blindly. */
    if (publish_registered_target(management,manager,paths,guards,source) ||
        !registered_target_exact(management,manager,paths,guards) ||
        !upgrade_time_remaining(deadline)) return UPGRADE_STAGE_PUBLISH_UNKNOWN;
    /* Release start/runtime BEFORE loading the job, otherwise the launcher
     * launchd spawns sees both locks busy and exits without exec. Management
     * stays exclusive, and the program is already swapped, so no old program
     * can be started in this window.
     *
     * A failure here is NOT "handed off". The program is published, but
     * start/runtime may still be held by this process (the close that failed
     * left the descriptor open) or may have been dropped without the final
     * identity re-check confirming it. Reporting AFTER_HANDOFF would tell
     * recovery the locks are free and let it bootstrap into a launcher that then
     * fails to take start.lock — the exact deadlock the release order exists to
     * avoid. Report the unknown stage instead and let recovery refuse. */
    if (!handoff_registered_start(management,manager,paths,guards)) return UPGRADE_STAGE_HANDOFF_UNKNOWN;
    /* Reload starts exactly once: bootstrap loads the definition and honours
     * RunAtLoad. It must not be replayed on an unknown outcome. */
    if (!load_registered_service_relay(management,manager,guards->management,deadline))
        return UPGRADE_STAGE_AFTER_LOAD;
    if (!registered_management_current(management,manager,paths,guards) ||
        !upgrade_time_remaining(deadline)) return UPGRADE_STAGE_AFTER_LOAD;
    return upgrade_wait_ready(management,manager,paths,guards,version,build,deadline)
        ? UPGRADE_STAGE_OK : UPGRADE_STAGE_NOT_READY;
}

/* Bounded, single-attempt recovery for the ONLY two stages whose on-disk state
 * is certain: the registered job is unloaded while the definition still exists
 * (unload only boots out; it never removes the plist).
 *
 *   AFTER_UNLOAD     job unloaded, program still the OLD one (publication never
 *                    started). Restoring service here runs the PREVIOUS program
 *                    again; it is a restart, not a rollback and not an upgrade.
 *   AFTER_HANDOFF    job unloaded, NEW program already published and verified,
 *                    start/runtime released and the release confirmed; only the
 *                    bootstrap is missing.
 *
 * Deliberately NOT recovered:
 *   EXIT_UNKNOWN      the old instance was asked to exit; no program was swapped
 *                     and the job is still loaded.
 *   UNLOAD_UNKNOWN    a failed bootout does not prove the job is still loaded.
 *   PUBLISH_UNKNOWN   publication may have already replaced the tree.
 *   HANDOFF_UNKNOWN   the NEW program is published but start/runtime may still
 *                     be held here; bootstrapping now can race a launcher that
 *                     is blocked on start.lock.
 *   AFTER_LOAD        the bootstrap outcome is unknown; replaying is forbidden.
 *   NOT_READY         the job is loaded and may already be running.
 *   REFUSED           nothing was requested.
 *
 * Lock precondition. Recovery must not load while this process still holds
 * start/runtime, because the launcher launchd spawns takes them non-blocking
 * and would exit without exec; the normal path releases them for exactly this
 * reason. So AFTER_UNLOAD performs the same handoff first. AFTER_HANDOFF has
 * already done it and must NOT reacquire those two locks: they belong to the
 * launcher now.
 *
 * Ownership caveat, deliberately not papered over: registered_management_current
 * and registered_guards_current verify fd identity and object matching, and
 * private_lock_held additionally observes contention on a second opener. That
 * proves the lock is not free; it does NOT positively prove this process is the
 * holder (a released lock taken by another process can look identical). This is
 * why recovery additionally refuses when the lock is observably free, and why it
 * never treats a match as permission to skip the deadline. It is not a stronger
 * guarantee than the check actually provides. */
static inline int upgrade_recover_after_stage(const char *management,uid_t manager,
                                      NSDictionary *paths,struct managed_guards *guards,
                                      int stage,double deadline) {
    if (stage!=UPGRADE_STAGE_AFTER_UNLOAD && stage!=UPGRADE_STAGE_AFTER_HANDOFF)
        return 0;
    /* Management excludes other writers for the whole sequence and is never
     * released by handoff. Require it to be observably held: an obviously free
     * management lock means the sequence is no longer ours to finish. */
    if (!registered_management_confirmed(management,manager,paths,guards)) return 0;
    if (stage==UPGRADE_STAGE_AFTER_UNLOAD) {
        /* start/runtime are still ours: refuse if they are not, because that
         * means a launcher already raced into the window. */
        if (!registered_guards_current(management,manager,paths,guards)) return 0;
        if (!upgrade_time_remaining(deadline)) return 0;
        /* Release them so the restored job can actually start, exactly like the
         * forward path does before its load. No acquisition happens here. */
        if (!handoff_registered_start(management,manager,paths,guards)) return 0;
    } else if (guards->start>=0 || guards->runtime>=0) {
        /* AFTER_HANDOFF claims start/runtime were released. If this process still
         * holds either descriptor, that claim is false — the stage was recorded
         * by an older build, or the handoff failed after publication. Loading now
         * would start a job whose launcher cannot take start.lock, so refuse
         * instead of trusting the stage code over the descriptor state. */
        return 0;
    }
    if (!registered_management_confirmed(management,manager,paths,guards)) return 0;
    if (!upgrade_time_remaining(deadline)) return 0;
    /* Exactly one bootstrap of the same definition; never retried here, and the
     * result is a load receipt only, never a readiness claim. */
    return load_registered_service_relay(management,manager,guards->management,deadline);
}

/* Preferred caller interface: identity comes from the inventoried bound tree.
 * Assumes immutable trusted installer resources throughout this one attempt.
 */
static inline int upgrade_registered_bound_once(const char *management,uid_t manager,
                                         NSDictionary *paths,struct managed_guards *guards,
                                         double deadline,int (*request_start)(void *),void *context) {
    if (!isfinite(deadline) || !upgrade_time_remaining(deadline) ||
        !registered_exit_current(management,manager,paths,guards)) return UPGRADE_STAGE_REFUSED;
    char source[PATH_MAX];
    if (!locate_bound_resources(source,manager)) return UPGRADE_STAGE_REFUSED;
    NSDictionary *identity=read_target_identity(source,manager);
    if (!identity || !upgrade_time_remaining(deadline)) return UPGRADE_STAGE_REFUSED;
    return upgrade_registered_once(management,manager,paths,guards,
        identity[@"version"],identity[@"build_id"],deadline,request_start,context);
}
