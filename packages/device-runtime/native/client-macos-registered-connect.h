/* Single bounded connection to fixed registered socket. No retries. */
#ifndef CLIENT_MACOS_REGISTERED_CONNECT_H
#define CLIENT_MACOS_REGISTERED_CONNECT_H
#include <fcntl.h>
static int connect_registered_socket(NSDictionary *paths,double deadline) {
    if (!paths || !isfinite(deadline)) return -1;
    double now=lease_clock();
    if (!isfinite(now) || now<0 || now>=deadline) return -1;
    NSString *socket_path=[paths[@"directory"] stringByAppendingPathComponent:@"state/maintenance.sock"];
    const char *path=[socket_path fileSystemRepresentation];
    struct sockaddr_un address={0};
    if (!path || strlen(path)>=sizeof(address.sun_path)) return -1;
    address.sun_family=AF_UNIX;strcpy(address.sun_path,path);
    int fd=socket(AF_UNIX,SOCK_STREAM,0);
    if (fd<0) return -1;
    int flags=fcntl(fd,F_GETFL);
    if (flags<0 || fcntl(fd,F_SETFL,flags|O_NONBLOCK) || fcntl(fd,F_SETFD,FD_CLOEXEC)) {
        close(fd);return -1;
    }
    int connected=connect(fd,(struct sockaddr *)&address,sizeof(address));
    if (connected && errno==EINPROGRESS && lease_wait(fd,POLLOUT,deadline)) {
        int error=0;socklen_t size=sizeof(error);
        if (!getsockopt(fd,SOL_SOCKET,SO_ERROR,&error,&size) && !error) connected=0;
    }
    double observed=lease_clock();
    if (connected || !isfinite(observed) || observed<0 || observed>=deadline) {close(fd);return -1;}
    return fd;
}
#endif
