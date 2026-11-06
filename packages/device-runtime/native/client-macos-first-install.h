/* Internal first-install composition; NOT a production installer entry.
 * Future entry must fix root owner and all system paths. No service actions.
 * Management fd is returned even after post-acquisition failure: caller owns
 * release and must not interpret a retained lock or stage as successful install.
 */
static int prepare_and_publish_first_program(const char *management,uid_t owner,
                                             const char *target,int confirmed,
                                             double timeout,int *management_fd) {
    if (!management_fd || *management_fd!=-1 || confirmed!=1 || !isfinite(timeout) ||
        timeout<=0 || timeout>10) return 70;
    int fd=provision_management_lock(management,owner);
    if (fd<0) return 70;
    *management_fd=fd;
    return publish_first_program_with_host_check(management,owner,fd,target,confirmed,timeout);
}
