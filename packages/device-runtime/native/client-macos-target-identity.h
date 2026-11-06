/* Source must be the fixed bound resource tree, never a caller version pair.
 * Full embedded inventory and owner checks include this metadata file.
 */
static NSDictionary *read_target_identity(const char *source,uid_t owner) {
    if (!source || !verify_resource_tree(source) || !tree_owner_valid(source,owner,0)) return nil;
    char path[PATH_MAX];
    if (snprintf(path,sizeof(path),"%s/client-identity.json",source)>=(int)sizeof(path)) return nil;
    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC);
    if (fd<0) return nil;
    struct stat before,after,current;
    char buffer[4097];size_t used=0;int valid=1;
    if (fstat(fd,&before) || !S_ISREG(before.st_mode) || before.st_uid!=owner ||
        before.st_nlink!=1 || (before.st_mode & 0022)) {close(fd);return nil;}
    while (used<sizeof(buffer)) {
        ssize_t n=read(fd,buffer+used,sizeof(buffer)-used);
        if (n>0)used+=(size_t)n;
        else if (!n)break;
        else if (errno!=EINTR){valid=0;break;}
    }
    if (fstat(fd,&after) || lstat(path,&current) || before.st_dev!=after.st_dev ||
        before.st_ino!=after.st_ino || before.st_dev!=current.st_dev || before.st_ino!=current.st_ino)
        valid=0;
    close(fd);
    if (!valid || used>4096) return nil;
    id identity=[NSJSONSerialization JSONObjectWithData:[NSData dataWithBytes:buffer length:used]
                                               options:0 error:NULL];
    if (![identity isKindOfClass:[NSDictionary class]] || [identity count]!=2) return nil;
    NSString *version=identity[@"version"],*build=identity[@"build_id"];
    NSCharacterSet *versionInvalid=[[NSCharacterSet characterSetWithCharactersInString:
        @"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.+_-"] invertedSet];
    if (![version isKindOfClass:[NSString class]] || !version.length || version.length>64 ||
        [version rangeOfCharacterFromSet:versionInvalid].location!=NSNotFound ||
        ![build isKindOfClass:[NSString class]] || build.length!=71 || ![build hasPrefix:@"sha256:"] ||
        [[build substringFromIndex:7] rangeOfCharacterFromSet:
            [[NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"] invertedSet]].location!=NSNotFound)
        return nil;
    if (!verify_resource_tree(source) || !tree_owner_valid(source,owner,0)) return nil;
    return identity;
}
