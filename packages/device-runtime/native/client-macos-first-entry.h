/* Fixed first-program command; not a general upgrade/install-success entry.
 * Kept separate so tests can link a recording publication substitute.
 */
static int first_install_entry(int argc,char **argv) {
    if (argc!=3 || strcmp(argv[1],"first-program") ||
        strcmp(argv[2],"--confirm-legacy-clients-disabled")) {
        fprintf(stderr,"first program publication requires explicit confirmation\n");
        return 64;
    }
    if (getuid()!=0 || geteuid()!=0) {
        fprintf(stderr,"first program publication requires administrator identity\n");
        return 70;
    }
    int management=-1;
    int result=prepare_and_publish_first_program(
        "/Library/Application Support/ai-device-client-management",0,
        "/opt/ai-device-client",1,10,&management);
    if (management>=0) close(management);
    if (result) {
        fprintf(stderr,"first program publication refused; no automatic retry or rollback\n");
        return 70;
    }
    puts("AI_DEVICE_FIRST_PROGRAM_PUBLISHED_NOT_REGISTERED_V1");
    return 0;
}
