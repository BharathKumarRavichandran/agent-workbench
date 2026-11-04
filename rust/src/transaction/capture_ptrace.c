/* Private disposable Linux x86_64 capture audit, never linked into production.
 * Observe SYS_fsync itself: rustix's configured raw backend bypasses LD_PRELOAD.
 * GET_SYSCALL_INFO distinguishes entry/exit even on newly cloned test threads.
 * Requires Linux >= 5.3; unsupported ptrace/procfs fails closed, never skips.
 * Linux execution remains unverified until the exact-SHA Ubuntu CI gate runs.
 */
#define _GNU_SOURCE
#include <sys/ptrace.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <sys/user.h>
#include <sys/syscall.h>
#include <linux/audit.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if !defined(__linux__) || !defined(__x86_64__)
#error capture_ptrace requires Linux x86_64
#endif

/* Kernel ABI for PTRACE_GET_SYSCALL_INFO (Linux 5.3); avoid libc header age. */
struct syscall_info {
    uint8_t op;
    uint8_t pad[3];
    uint32_t arch;
    uint64_t ip, sp;
    union {
        struct { uint64_t nr, args[6]; } entry;
        struct { int64_t rval; uint8_t is_error; } exit;
    } data;
};
struct thread {
    pid_t tid;
    int injected_errno;
    struct thread *next;
};
static struct thread *threads;
static const char *base, *root, *fault;
static pid_t leader;

static void die(const char *what) {
    perror(what);
    /* EXITKILL kills all tracees if the audit fails, including cloned threads. */
    exit(125);
}
static struct thread *thread_for(pid_t tid) {
    for (struct thread *t = threads; t; t = t->next)
        if (t->tid == tid) return t;
    struct thread *t = calloc(1, sizeof(*t));
    if (!t) die("calloc");
    t->tid = tid;
    t->next = threads;
    threads = t;
    return t;
}
static void forget(pid_t tid) {
    struct thread **link = &threads;
    while (*link && (*link)->tid != tid) link = &(*link)->next;
    if (*link) {
        struct thread *old = *link;
        *link = old->next;
        free(old);
    }
}
static void filename(char *out, size_t size, const char *name) {
    int n = snprintf(out, size, "%s/%s", base, name);
    if (n < 0 || (size_t)n >= size) { errno = ENAMETOOLONG; die("audit path"); }
}
static void append(const char *name, const char *value) {
    char path[PATH_MAX];
    filename(path, sizeof(path), name);
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    if (fd < 0) die("audit open");
    size_t size = strlen(value);
    if (write(fd, value, size) != (ssize_t)size) die("audit write");
    if (close(fd)) die("audit close");
}
static const char *role_for(pid_t tid, uint64_t fd) {
    char file[PATH_MAX], phase[32] = {0}, path[PATH_MAX];
    filename(file, sizeof(file), "phase");
    int f = open(file, O_RDONLY | O_CLOEXEC);
    if (f < 0) die("phase open");
    ssize_t n = read(f, phase, sizeof(phase) - 1);
    if (n < 0) die("phase read");
    if (close(f)) die("phase close");
    if (strcmp(phase, "capture") && strcmp(phase, "discard")) return NULL;
    int length = snprintf(file, sizeof(file), "/proc/%ld/fd/%llu",
                          (long)tid, (unsigned long long)fd);
    if (length < 0 || (size_t)length >= sizeof(file)) die("proc path");
    n = readlink(file, path, sizeof(path) - 1);
    if (n < 0) die("proc fd readlink");
    if ((size_t)n == sizeof(path) - 1) { errno = ENAMETOOLONG; die("proc fd path"); }
    path[n] = '\0';
    if (!strcmp(phase, "capture")) {
        if (!strcmp(path, root)) return "capture-parent";
        const char *prefix = "/.agent-workbench-quarantine-";
        size_t len = strlen(root);
        if (!strncmp(path, root, len) && !strncmp(path + len, prefix, strlen(prefix)))
            return "capture-evidence";
    } else if (!strcmp(path, root)) return "discard-parent";
    return NULL;
}
static int audit(pid_t tid, uint64_t fd) {
    const char *role = role_for(tid, fd);
    if (!role) return 0;
    char line[128];
    snprintf(line, sizeof(line), "%s\n", role);
    append("syscalls", line);
    snprintf(line, sizeof(line), "%ld %ld %s\n", (long)tid, (long)leader, role);
    append("trace", line);
    if (strcmp(fault, role)) return 0;
    char path[PATH_MAX];
    filename(path, sizeof(path), "hit");
    int f = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (f < 0) {
        if (errno == EEXIST) return 0;
        die("hit open");
    }
    if (close(f)) die("hit close");
    return getenv("AW_CAPTURE_EINTR") ? EINTR : EIO;
}
int main(int argc, char **argv) {
    base = getenv("AW_CAPTURE_BASE");
    root = getenv("AW_CAPTURE_ROOT");
    fault = getenv("AW_CAPTURE_FAULT");
    if (argc < 2 || !base || !root || !fault) {
        fprintf(stderr, "capture_ptrace: missing command or audit environment\n");
        return 125;
    }
    leader = fork();
    if (leader < 0) die("fork");
    if (!leader) {
        if (ptrace(PTRACE_TRACEME, 0, NULL, NULL) < 0) die("TRACEME");
        raise(SIGSTOP);
        execvp(argv[1], argv + 1);
        die("execvp");
    }
    int status;
    if (waitpid(leader, &status, 0) != leader || !WIFSTOPPED(status)) die("initial wait");
    long options = PTRACE_O_TRACESYSGOOD | PTRACE_O_TRACECLONE |
                   PTRACE_O_TRACEFORK | PTRACE_O_TRACEVFORK |
                   PTRACE_O_TRACEEXEC | PTRACE_O_EXITKILL;
    if (ptrace(PTRACE_SETOPTIONS, leader, NULL, (void *)options) < 0) die("SETOPTIONS");
    (void)thread_for(leader);
    if (ptrace(PTRACE_SYSCALL, leader, NULL, NULL) < 0) die("initial SYSCALL");
    int exit_code = 125, saw_leader_exit = 0;
    for (;;) {
        pid_t tid = waitpid(-1, &status, __WALL);
        if (tid < 0) {
            if (errno == EINTR) continue;
            if (errno == ECHILD && saw_leader_exit) break;
            die("waitpid");
        }
        if (WIFEXITED(status) || WIFSIGNALED(status)) {
            if (tid == leader) {
                exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
                saw_leader_exit = 1;
            }
            forget(tid);
            continue;
        }
        if (!WIFSTOPPED(status)) { errno = EINVAL; die("unexpected wait status"); }
        struct thread *t = thread_for(tid);
        int sig = WSTOPSIG(status), deliver = 0;
        unsigned int event = (unsigned int)status >> 16;
        if (sig == (SIGTRAP | 0x80)) {
            struct syscall_info info = {0};
            long size = ptrace((enum __ptrace_request)0x420e, tid, (void *)sizeof(info), &info);
            if (size < 0) die("GET_SYSCALL_INFO (Linux >=5.3 required)");
            if (info.arch != AUDIT_ARCH_X86_64) { errno = ENOTSUP; die("tracee architecture"); }
            if (info.op == 1 && info.data.entry.nr == SYS_fsync) {
                if (t->injected_errno) { errno = EINVAL; die("pending injection at entry"); }
                t->injected_errno = audit(tid, info.data.entry.args[0]);
                if (t->injected_errno) {
                    struct user_regs_struct regs;
                    if (ptrace(PTRACE_GETREGS, tid, NULL, &regs) < 0) die("GETREGS entry");
                    regs.orig_rax = (unsigned long long)-1; /* do not execute this fsync */
                    if (ptrace(PTRACE_SETREGS, tid, NULL, &regs) < 0) die("SETREGS entry");
                }
            } else if (info.op == 2 && t->injected_errno) {
                struct user_regs_struct regs;
                if (ptrace(PTRACE_GETREGS, tid, NULL, &regs) < 0) die("GETREGS exit");
                regs.rax = (unsigned long long)-(long long)t->injected_errno;
                if (ptrace(PTRACE_SETREGS, tid, NULL, &regs) < 0) die("SETREGS exit");
                t->injected_errno = 0;
            } else if (info.op != 1 && info.op != 2) {
                errno = EINVAL; die("unexpected syscall stop");
            }
        } else if (event && sig == SIGTRAP) {
            /* Auto-attached children inherit tracing options. Their initial
             * SIGSTOP may arrive before or after this parent's clone event. */
        } else if (sig != SIGSTOP) {
            deliver = sig;
        }
        if (ptrace(PTRACE_SYSCALL, tid, NULL, (void *)(long)deliver) < 0) die("SYSCALL");
    }
    return exit_code;
}
