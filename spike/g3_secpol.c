/* G3 spike: secpol channel-connect least privilege.
 * g3 server <file>          create a channel, write "pid chid" to <file>, receive forever
 * g3 client <file> [...]    ConnectAttach + MsgSend to each server; print OK/errno per target
 * g3 as <type> <uid> <mode> <args...>
 *                           (root) switch to secpol <type> (SECPOL_TYPE_NAME), then drop to <uid>
 *                           (the policy grants setuid/setgid:<uid> only), then exec self as <mode>
 * Policy: spike/g3_spike.pol, compiled with secpolcompile, pushed at runtime with secpolpush
 * (the prebuilt image boots without a policy; on-boot loading is the P1b integration). */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/neutrino.h>
#include <secpol/secpol.h>
int main(int argc, char **argv)
{
    if (argc < 3) return 1;
    if (!strcmp(argv[1], "as")) { /* g3 as <type> <uid> <mode> <args...> : transition type, drop uid, exec self */
        secpol_file_t *hp = secpol_open(getenv("TT_POL") ? getenv("TT_POL") : "/tmp/tt/g3_spike.bin", 0);
        if (!hp) { perror("secpol_open"); return 2; }
        if (secpol_transition_type(hp, argv[2], SECPOL_TYPE_NAME) != 0) { printf("transition to %s FAILED errno=%d (%s)\n", argv[2], errno, strerror(errno)); return 2; }
        int uid = atoi(argv[3]);
        if (setgid(uid) || setuid(uid)) { perror("setuid"); return 2; }
        argv[3] = argv[0]; execv(argv[0], argv + 3); perror("execv"); return 2;
    }
    char tname[64] = "?";
    uint32_t tid = secpol_type_id();
    secpol_file_t *h = secpol_open(getenv("TT_POL") ? getenv("TT_POL") : "/tmp/tt/g3_spike.bin", 0);
    const char *nm = h ? secpol_get_type_name(h, tid) : NULL;
    snprintf(tname, sizeof tname, "%s(id %u)", nm ? nm : "?", tid);
    if (!strcmp(argv[1], "server")) {
        int chid = ChannelCreate(0);
        FILE *f = fopen(argv[2], "w"); fprintf(f, "%d %d\n", getpid(), chid); fclose(f);
        printf("server pid=%d uid=%d type=%s chid=%d\n", getpid(), getuid(), tname, chid); fflush(stdout);
        char msg[16];
        for (;;) { int rc = MsgReceive(chid, msg, sizeof msg, NULL); if (rc > 0) MsgReply(rc, 0, "ok", 3); }
    }
    for (int i = 2; i < argc; i++) {
        int pid, chid; FILE *f = fopen(argv[i], "r");
        if (!f || fscanf(f, "%d %d", &pid, &chid) != 2) { printf("bad file %s\n", argv[i]); continue; }
        fclose(f);
        int coid = ConnectAttach(0, pid, chid, _NTO_SIDE_CHANNEL, 0);
        if (coid == -1) { printf("client uid=%d type=%s -> %s: ConnectAttach DENIED errno=%d (%s)\n", getuid(), tname, argv[i], errno, strerror(errno)); continue; }
        char r[8] = "";
        int rc = MsgSend(coid, "hi", 3, r, sizeof r);
        printf("client uid=%d type=%s -> %s: ConnectAttach OK, MsgSend rc=%d reply='%s'%s\n", getuid(), tname, argv[i], rc, r, rc < 0 ? strerror(errno) : "");
        ConnectDetach(coid);
    }
    return 0;
}
