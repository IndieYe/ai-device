/* Explicit observation, not publication/repair/registration/start. */
static int unregistered_observe_entry(int argc,char **argv) {
    if (argc!=4 || strcmp(argv[1],"observe-unregistered-program") ||
        strcmp(argv[2],"--confirm-legacy-clients-disabled")) return 64;
    if (getuid()!=0 || geteuid()!=0) return 70;
    /* Same contract as first-program: the caller supplies the staged tree, and
     * the embedded inventory refuses anything that is not this build. */
    const char *source=argv[3];
    if (source[0]!='/') return 64;
    const char *management="/Library/Application Support/ai-device-client-management";
    int fd=acquireManagement(management,0);
    if (fd<0) return 70;
    int result=observe_unregistered_program_with_host_check(
        management,0,fd,"/opt/ai-device-client",source,1,10);
    close(fd);
    if (result) return 70;
    puts("AI_DEVICE_UNREGISTERED_PROGRAM_EXACT_NOT_REGISTERED_V1");
    return 0;
}
