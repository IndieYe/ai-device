/* Internal once-only upgrade callback. Include after start-handoff and
 * start-child. Parent retains management exclusion even on unknown outcome.
 */
#ifndef CLIENT_MACOS_REGISTERED_START_H
#define CLIENT_MACOS_REGISTERED_START_H
struct registered_start_context {
    const char *management;
    uid_t manager;
    NSDictionary *paths;
    struct managed_guards *guards;
    const char *source;
    double deadline;
    int attempted;
};
static int request_registered_start_once(void *opaque) {
    struct registered_start_context *context=opaque;
    if (!context || context->attempted) return 0;
    /* Consume even a refused attempt: callers must explicitly inspect recovery,
     * not reuse an old callback after repairing/changing guards. */
    context->attempted=1;
    struct managed_guards *guards=context->guards;
    double now=start_child_clock();
    if (!guards || guards->start>=0 || guards->runtime>=0 ||
        !isfinite(context->deadline) || now<0 || now>=context->deadline ||
        !registered_management_current(context->management,context->manager,context->paths,guards)) return 0;
    double remaining=context->deadline-start_child_clock();
    if (remaining<=0) return 0;
    if (remaining>10) remaining=10;
    int requested=request_bound_registered_start(context->management,context->manager,
                                                 guards->management,context->source,remaining);
    return requested && start_child_clock()<context->deadline &&
        guards->start<0 && guards->runtime<0 &&
        registered_management_current(context->management,context->manager,context->paths,guards);
}
#endif
