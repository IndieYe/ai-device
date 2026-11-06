/* Explicit damaged-program repair entry. Repairs the program tree ONLY; it
 * never touches configuration, credentials, identity or business data.
 *
 * Requirement 8/11: repair must not depend on the old client being able to
 * start. Nothing here executes the installed program, reads its config, or
 * contacts it for permission; the replacement source is this package's own
 * bound resource tree, verified against the embedded inventory.
 *
 * Stop proof is deliberately NOT claimed. `--confirm-service-not-running` is a
 * caller assertion (Installer/operator has established the service is stopped),
 * not evidence this entry can produce.
 *
 * Guard model, which the leaf helpers enforce strictly:
 *
 *   management + start            -> instance may run; replacement FORBIDDEN
 *   start/runtime both released   -> the only state where ready may be queried
 *   management + start + runtime  -> publication allowed
 *
 * Scope of the runtime guarantee (deliberately narrow): `runtime.lock` is
 * inherited across exec by a COOPERATING instance, so acquiring it proves no
 * cooperating instance holds it right now. It does NOT prove the launchd job
 * is unloaded, that descendants have exited, or that a non-cooperating or
 * already-departed instance is gone. Stop proof comes from the caller's
 * `--confirm-service-not-running` assertion, never from this lock. Any wording
 * below that says "no instance" means "no cooperating instance holds the
 * lock" and nothing more. Publication and target
 * verification require all three guards (registered_guards_current), while
 * query_registered_ready requires start AND runtime to be released. These two
 * requirements cannot hold at the same time, so the entry cannot keep start
 * for the whole run:
 *
 *   1. take management+start, verify identity/source, then RELEASE start;
 *   2. query readiness in the released state (the only legal one);
 *   3. re-take start, then acquire runtime as positive proof of absence.
 *
 * The re-take in step 3 also fails when a launcher got start meanwhile, so a
 * competing start refuses the repair instead of racing the swap. An acquire
 * failure of runtime means a live instance or an unreadable lock; both refuse
 * and neither is treated as "probably stopped".
 *
 * A registered identity is not proof the program tree exists (registration_state
 * only checks registration.json), so the target is required to be present before
 * publication. Otherwise the publish primitive's ENOENT fallback would create a
 * tree and still report "replaced".
 */
#include <sys/stat.h>
static int repair_registered_entry(int argc,char **argv) {
    if (argc!=3 || strcmp(argv[1],"repair-registered-program") ||
        strcmp(argv[2],"--confirm-service-not-running")) return 64;
    if (getuid()!=0 || geteuid()!=0) return 70;
    const char *management="/Library/Application Support/ai-device-client-management";
    struct managed_guards guards={.management=-1,.start=-1,.runtime=-1,.identity=nil};
    double now=lease_clock();
    if (!isfinite(now) || now<0) return 70;
    double deadline=now+120;
    NSDictionary *paths=prepare_start_barrier(management,0,&guards);
    if (!paths) {release_guards(&guards);return 70;}
    /* A registered identity is required: repair replaces an existing program,
     * it is not first installation and must not create one. */
    if (registration_state(management,0,guards.management)!=1) {
        release_guards(&guards);
        puts("AI_DEVICE_REPAIR_REFUSED_NOT_REGISTERED_V1");
        return 70;
    }
    char source[PATH_MAX];
    if (!locate_bound_resources(source,0) || !verify_resource_tree(source) ||
        !tree_owner_valid(source,0,0)) {
        release_guards(&guards);
        puts("AI_DEVICE_REPAIR_REFUSED_SOURCE_INVALID_V1");
        return 70;
    }
    NSDictionary *expected=read_target_identity(source,0);
    if (!expected) {
        release_guards(&guards);
        puts("AI_DEVICE_REPAIR_REFUSED_SOURCE_INVALID_V1");
        return 70;
    }
    const char *directory=[paths[@"directory"] fileSystemRepresentation];
    uid_t runtime_owner=[paths[@"runtime_owner"] unsignedIntValue];
    /* Ready may only be queried with start AND runtime released, and holding
     * start is required for publication. Release our own start fd here; the
     * management lock still excludes other writers, and a launcher that slips
     * in is caught by the runtime acquisition below. */
    int start_fd=guards.start;
    guards.start=-1;
    if (close(start_fd)) {
        release_guards(&guards);
        puts("AI_DEVICE_REPAIR_UNKNOWN_OR_REFUSED_V1");
        return 70;
    }
    int serving=query_registered_ready(management,0,paths,&guards,
                                      expected[@"version"],expected[@"build_id"],deadline);
    if (serving) {
        release_guards(&guards);
        puts("AI_DEVICE_REPAIR_REFUSED_SERVICE_RUNNING_V1");
        return 70;
    }
    /* Re-check the deadline before every step that has a side effect. The
     * initial check is an admission gate; resource verification and the ready
     * probe can consume the whole budget, and publishing after the deadline
     * would defeat the operator's expectation that a timed-out run did nothing. */
    double phase_now=lease_clock();
    if (!isfinite(phase_now) || phase_now<0 || phase_now>=deadline) {
        release_guards(&guards);
        puts("AI_DEVICE_REPAIR_UNKNOWN_OR_REFUSED_V1");
        return 70;
    }
    /* Re-take start; -2 means another start is in flight, so refuse rather than
     * race it. -1 means the gate is unusable, which is equally a refusal but a
     * DIFFERENT diagnosis: reporting it as "service running" would send the
     * operator looking for a process that does not exist. */
    guards.start=acquire_private_lock(directory,"start.lock",runtime_owner);
    if (guards.start<0) {
        int busy=guards.start==-2;
        release_guards(&guards);
        puts(busy ? "AI_DEVICE_REPAIR_REFUSED_SERVICE_RUNNING_V1"
                  : "AI_DEVICE_REPAIR_UNKNOWN_OR_REFUSED_V1");
        return 70;
    }
    /* Same deadline guard before the runtime acquisition: once start is held
     * this is the last point where refusing is still free of side effects. */
    phase_now=lease_clock();
    if (!isfinite(phase_now) || phase_now<0 || phase_now>=deadline) {
        release_guards(&guards);
        puts("AI_DEVICE_REPAIR_UNKNOWN_OR_REFUSED_V1");
        return 70;
    }
    /* Positive evidence of absence, narrowly: a COOPERATING instance inherits
     * runtime.lock across exec, so this acquisition cannot succeed while one is
     * alive. It does not prove the job is unloaded, that descendants exited, or
     * that a non-cooperating instance is gone. A busy lock (-2) or unusable
     * lock (<0) is refused, never assumed stopped. */
    if (!acquire_registered_runtime(management,0,paths,&guards)) {
        int busy=guards.runtime==-2;
        release_guards(&guards);
        puts(busy ? "AI_DEVICE_REPAIR_REFUSED_SERVICE_RUNNING_V1"
                  : "AI_DEVICE_REPAIR_UNKNOWN_OR_REFUSED_V1");
        return 70;
    }
    /* Replace the existing tree atomically (RENAME_SWAP) under all three
     * guards; a failed publication retains the stage and reports nothing as
     * success. Config/identity/state are outside this path entirely.
     *
     * A registered identity does NOT imply the program tree is present:
     * registration_state only checks registration.json. Without this explicit
     * precondition the publication primitive would fall back to RENAME_EXCL on
     * ENOENT and CREATE a target while reporting "replaced". Repair is not
     * first installation, so an absent target is refused. */
    const char *target=[paths[@"program"] fileSystemRepresentation];
    struct stat target_info;
    if (!target || lstat(target,&target_info) || !S_ISDIR(target_info.st_mode)) {
        release_guards(&guards);
        puts("AI_DEVICE_REPAIR_REFUSED_TARGET_ABSENT_V1");
        return 70;
    }
    /* Final deadline guard immediately before the irreversible swap. */
    phase_now=lease_clock();
    if (!isfinite(phase_now) || phase_now<0 || phase_now>=deadline) {
        release_guards(&guards);
        puts("AI_DEVICE_REPAIR_UNKNOWN_OR_REFUSED_V1");
        return 70;
    }
    if (publish_registered_target(management,0,paths,&guards,source) ||
        !registered_target_exact(management,0,paths,&guards)) {
        release_guards(&guards);
        puts("AI_DEVICE_REPAIR_UNKNOWN_OR_REFUSED_V1");
        return 70;
    }
    release_guards(&guards);
    puts("AI_DEVICE_REPAIR_PROGRAM_REPLACED_NOT_STARTED_V1");
    return 0;
}
