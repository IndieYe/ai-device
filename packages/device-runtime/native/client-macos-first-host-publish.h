/* Internal composition only. Production target/owner must be fixed by the
 * future root entry. No service or registration actions, no safe default.
 * Requires first-publish.h and host-conflicts.h in this translation unit.
 */
struct first_host_conflict_context {
    const char *management;
    uid_t owner;
    int management_fd;
    int confirmed;
    double timeout;
};
static int first_host_conflicts_clear(void *opaque) {
    struct first_host_conflict_context *context=opaque;
    return check_bound_host_conflicts(context->management,context->owner,
        context->management_fd,context->confirmed,context->timeout);
}
static int publish_first_program_with_host_check(const char *management,uid_t owner,
                                                 int management_fd,const char *target,
                                                 int confirmed,double timeout) {
    struct first_host_conflict_context context={management,owner,management_fd,confirmed,timeout};
    return publish_first_program(management,owner,management_fd,target,
                                 first_host_conflicts_clear,&context);
}

static inline int observe_unregistered_program_with_host_check(const char *management,uid_t owner,
                                                               int management_fd,const char *target,
                                                               int confirmed,double timeout) {
    struct first_host_conflict_context context={management,owner,management_fd,confirmed,timeout};
    return observe_unregistered_program(management,owner,management_fd,target,
                                        first_host_conflicts_clear,&context);
}
