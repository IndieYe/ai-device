/* Offline resource verifier, no writes or execution of verified content.
 * Build-generated resource-table.h binds exact contents to this executable.
 * Trusted immutable package resources; not signing or hostile local isolation.
 */
#include <CommonCrypto/CommonDigest.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
struct resource { const char *path; int kind; unsigned int mode; long long size; const char *value; };
#include "resource-table.h"

static int check_one(const char *root, const struct resource *r) {
    char path[PATH_MAX]; struct stat s;
    if (snprintf(path,sizeof(path),"%s/%s",root,r->path) >= (int)sizeof(path) || lstat(path,&s)) return 0;
    if (r->kind == 2) {
        char target[PATH_MAX];
        if (!S_ISLNK(s.st_mode)) return 0;
        ssize_t n = readlink(path,target,sizeof(target)-1);
        if (n < 0 || n >= (ssize_t)sizeof(target)-1) return 0;
        target[n] = 0;
        return !strcmp(target,r->value);
    }
    if ((s.st_mode & 07777) != r->mode) return 0;
    if (r->kind == 1) return S_ISDIR(s.st_mode);
    if (!S_ISREG(s.st_mode) || s.st_nlink != 1 || s.st_size != r->size) return 0;
    int fd = open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC);
    if (fd < 0) return 0;
    struct stat opened;
    if (fstat(fd,&opened) || opened.st_ino != s.st_ino || opened.st_dev != s.st_dev) {close(fd);return 0;}
    CC_SHA256_CTX context;
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    CC_SHA256_Init(&context);
    unsigned char buffer[65536], digest[CC_SHA256_DIGEST_LENGTH];
    ssize_t n; long long total = 0;
    while ((n=read(fd,buffer,sizeof(buffer))) > 0) {
        total += n;
        if (total > r->size) {close(fd);return 0;}
        CC_SHA256_Update(&context,buffer,(CC_LONG)n);
    }
    CC_SHA256_Final(digest,&context);
#pragma clang diagnostic pop
    close(fd);
    if (n < 0 || total != r->size) return 0;
    char hex[65];
    for (int i=0;i<32;i++) snprintf(hex+i*2,3,"%02x",digest[i]);
    return !strcmp(hex,r->value);
}

static int count_tree(const char *root, unsigned int depth, size_t *count) {
    if (depth > 64) return 0;
    DIR *d=opendir(root); if (!d) return 0;
    struct dirent *entry; int ok=1;
    errno=0;
    while ((entry=readdir(d))) {
        if (!strcmp(entry->d_name,".") || !strcmp(entry->d_name,"..")) continue;
        if (++*count > RESOURCE_COUNT) {ok=0;break;}
        char path[PATH_MAX];struct stat s;
        if (snprintf(path,sizeof(path),"%s/%s",root,entry->d_name)>=(int)sizeof(path) || lstat(path,&s)) {ok=0;break;}
        if (S_ISDIR(s.st_mode) && !count_tree(path,depth+1,count)) {ok=0;break;}
        errno=0;
    }
    if (errno) ok=0;
    closedir(d);return ok;
}

static int verify_resource_tree(const char *root) {
    struct stat s;
    if (lstat(root,&s) || !S_ISDIR(s.st_mode)) return 0;
    for (size_t i=0;i<RESOURCE_COUNT;i++) if (!check_one(root,&resources[i])) return 0;
    size_t count=0;
    return count_tree(root,0,&count) && count == RESOURCE_COUNT;
}

#ifndef RESOURCE_CHECK_LIBRARY
int main(int argc, char **argv) {
    (void)argv;
    if (argc != 1) return 64;
    char executable[PATH_MAX], resolved[PATH_MAX], root[PATH_MAX];
    uint32_t size=sizeof(executable);
    if (_NSGetExecutablePath(executable,&size) || !realpath(executable,resolved)) return 70;
    char *slash=strrchr(resolved,'/'); if (!slash) return 70;*slash=0;
    if (snprintf(root,sizeof(root),"%s/resources/client-tree",resolved)>=(int)sizeof(root)) return 70;
    if (!verify_resource_tree(root)) return 70;
    puts("RESOURCE_VERIFIED_NOT_INSTALLED");
    return 0;
}
#endif
