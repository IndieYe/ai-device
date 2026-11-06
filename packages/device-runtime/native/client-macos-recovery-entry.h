/* Observation only: never acquire start/runtime, repair, start or replay.
 * Exact tree/ready results do not authorize replacement or prove loaded plist.
 */
static int recovery_observe_entry(int argc,char **argv) {
    if (argc!=2 || strcmp(argv[1],"recovery-observe")) return 64;
    if (getuid()!=0 || geteuid()!=0) return 70;
    const char *management="/Library/Application Support/ai-device-client-management";
    struct managed_guards guards={.management=-1,.start=-1,.runtime=-1,.identity=nil};
    double now=lease_clock();
    if (!isfinite(now) || now<0) return 70;
    double deadline=now+10;
    guards.management=acquireManagement(management,0);
    enum registered_recovery_observation result=RECOVERY_UNKNOWN;
    if (guards.management>=0) {
        guards.identity=readIdentity(management,0);
        NSDictionary *paths=guards.identity ? managedPaths(guards.identity) : nil;
        if (paths) result=observe_registered_recovery(management,0,paths,&guards,deadline);
    }
    release_guards(&guards);
    if (result==RECOVERY_READY_EXACT) {
        puts("AI_DEVICE_RECOVERY_READY_EXACT_V1");
        return 0;
    }
    if (result==RECOVERY_TARGET_EXACT) {
        puts("AI_DEVICE_RECOVERY_TARGET_EXACT_V1");
        return 0;
    }
    puts("AI_DEVICE_RECOVERY_UNKNOWN_V1");
    return 70;
}
