#!/usr/bin/env python3
"""Minimal ya.make -> ninja file generator (offline, no ya/ymake download).

Reads upstream ya.make files (SRCS/SRC/JOIN_SRCS/PEERDIR/ADDINCL/CFLAGS and IF/ELSE evaluation for a
fixed Linux x86_64 clang configuration) and emits a ninja file that builds a module's transitive
closure with the compiler flags given on the command line.
"""
import os
import re
import sys

ROOT = os.environ.get("SRC", os.getcwd())

# Modules provided by the system toolchain instead of by ya (libstdc++ / glibc headers).
SKIP_PEERS = (
    "contrib/libs/cxxsupp",
    "contrib/libs/linux-headers",
    "contrib/libs/libunwind",
    "contrib/libs/cxxsupp/libcxx",
    "library/cpp/sanitizer/include",
)

VARS0 = {
    "OS_LINUX": "yes", "LINUX": "yes", "UNIX": "yes", "POSIX": "yes",
    "ARCH_X86_64": "yes", "ARCH_TYPE_64": "yes", "CLANG": "yes",
    "OS_SDK": "local", "SANITIZER_TYPE": "", "MUSL": "", "OPENSOURCE": "yes",
    "TSTRING_IS_STD_STRING": "", "USE_ARCADIA_COMPILER_RUNTIME": "no",
    "ARCH_AARCH64": "", "ARCH_ARM64": "", "ARCH_ARM": "", "OS_WINDOWS": "", "OS_DARWIN": "",
    "OS_ANDROID": "", "OS_IOS": "", "OS_CYGWIN": "", "OS_EMSCRIPTEN": "", "MSVC": "", "GCC": "",
    "CLANG_CL": "", "SUN": "", "CYGWIN": "", "WITH_VALGRIND": "", "USE_SYSTEM_PYTHON": "",
    "DISABLE_INSTRUCTION_SETS": "", "NO_CUSTOM_CHAR_PTR_STD_COMPARATOR": "",
    "OS_FREEBSD": "", "OS_NETBSD": "", "OS_OPENBSD": "", "OS_SUNOS": "", "OS_NONE": "",
    "BUILD_TYPE": "RELEASE", "DEBUG": "", "RELEASE": "yes", "HOST_OS_LINUX": "yes",
    "ARCH_PPC64LE": "", "ARCH_I386": "", "ARCH_I686": "", "ARCH_ARM7": "", "ARCH_RISCV64": "",
    "ARCH_ARM6": "", "ARCH_ARM64": "", "ARCH_ARM32": "",
    "CLANG_VER": "19", "GCC_VER": "", "ALLOCATOR": "FAKE", "SSE41_ASM": "", "NO_SSE4": "",
    "AUTOCHECK": "", "YA_IDE_VENV": "", "SSE2_ASM": "", "MAPSMOBI_BUILD_TARGET": "",
    "OS_WINDOWS_FROM_TARGET": "", "BUILD_FOR_CUDA": "", "OPENSOURCE_REPLACE_ABSEIL": "",
    "SANITIZE_ADDRESS": "", "OS_DARWIN_ARM64": "",
}


def tokenize(text):
    text = re.sub(r"#[^\n]*", "", text)
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c in "()":
            out.append(c)
            i += 1
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in "()":
                j += 1
            out.append(text[i:j])
            i = j
    return out


def parse_calls(text):
    toks = tokenize(text)
    calls = []
    i = 0
    while i < len(toks):
        name = toks[i]
        if i + 1 < len(toks) and toks[i + 1] == "(":
            depth, j, args, cur = 1, i + 2, [], []
            while j < len(toks) and depth:
                t = toks[j]
                if t == "(":
                    depth += 1
                    cur.append(t)
                elif t == ")":
                    depth -= 1
                    if depth:
                        cur.append(t)
                else:
                    cur.append(t)
                j += 1
            calls.append((name, cur))
            i = j
        else:
            i += 1
    return calls


def unq(s):
    return s[1:-1] if len(s) >= 2 and s[0] == '"' and s[-1] == '"' else s


def eval_cond(args, V):
    toks = []
    for a in args:
        # split glued parens like "(A" "B)" already separated by tokenizer
        toks.append(a)
    py = []
    for t in toks:
        u = t
        if u in ("AND",):
            py.append(" and ")
        elif u in ("OR",):
            py.append(" or ")
        elif u == "NOT":
            py.append(" not ")
        elif u in ("(", ")"):
            py.append(u)
        elif u in ("==", "!=", "<", ">", "<=", ">="):
            py.append(u)
        elif u.startswith('"'):
            py.append(repr(unq(u)))
        elif re.fullmatch(r"-?\d+", u):
            py.append(u)
        else:
            val = V.get(u, "")
            py.append(repr(val))
    expr = "".join(py)
    # bare truthiness: values "", "no", "0", "false" are falsey; rewrite comparisons of strings
    # evaluate with helper
    def T(x):
        return x

    try:
        # comparisons between ints and strings: coerce numerics
        class Str(str):
            pass

        expr2 = re.sub(r"'([^']*)'", lambda m: "S(%r)" % m.group(1), expr)
        return bool(eval(expr2, {"S": S}, {}))
    except Exception:
        return False


class S(str):
    def __bool__(self):
        return str(self).lower() not in ("", "no", "0", "false")

    def _num(self, o):
        try:
            return float(self), float(o)
        except Exception:
            return None

    def __lt__(self, o):
        p = self._num(o)
        return p[0] < p[1] if p else False

    def __gt__(self, o):
        p = self._num(o)
        return p[0] > p[1] if p else False

    def __le__(self, o):
        p = self._num(o)
        return p[0] <= p[1] if p else False

    def __ge__(self, o):
        p = self._num(o)
        return p[0] >= p[1] if p else False


class Module:
    def __init__(self, path):
        self.path = path
        self.kind = None
        self.srcs = []          # (file, extra flags, simd flag)
        self.cflags = []
        self.gcflags = []
        self.conlyflags = []
        self.cxxflags = []
        self.addincl = []
        self.gaddincl = []
        self.peers = []
        self.no_util = False
        self.no_runtime = False
        self.no_warn = True
        self.extralibs = []
        self.gextralibs = []
        self.programs = []
        self.srcdirs = []


SIMD = {
    "SSE2": "-msse2", "SSE3": "-msse3", "SSSE3": "-mssse3", "SSE41": "-msse4.1", "SSE42": "-msse4.2",
    "AVX": "-mavx", "AVX2": "-mavx2 -mfma", "AVX512": "-mavx512f -mavx512bw -mavx512vl -mavx512dq",
    "POPCNT": "-mpopcnt", "PCLMUL": "-mpclmul", "AES": "-maes",
}

_cache = {}


def load(path):
    if path in _cache:
        return _cache[path]
    m = Module(path)
    _cache[path] = m
    fn = os.path.join(ROOT, path, "ya.make")
    if not os.path.exists(fn):
        fn = os.path.join(ROOT, path)
        if not os.path.isfile(fn):
            raise SystemExit("missing ya.make for " + path)
    V = dict(VARS0)
    V["ARCADIA_ROOT"] = ROOT
    V["MODDIR"] = path
    run_calls(m, V, parse_calls(open(fn).read()), path)
    return m


def expand(s, V):
    return re.sub(r"\$\{([A-Za-z_]+)\}", lambda mo: V.get(mo.group(1), ""), s).replace("${ARCADIA_ROOT}/", "")


def run_calls(m, V, calls, path):
    stack = []  # (taken_any, active)
    def active():
        return all(a for _, a in stack)
    for name, args in calls:
        if name == "IF":
            r = eval_cond(args, V) if active() else False
            stack.append([r, r])
            continue
        if name == "ELSEIF":
            top = stack[-1]
            outer = all(a for _, a in stack[:-1])
            r = (not top[0]) and outer and eval_cond(args, V)
            top[0] = top[0] or r
            top[1] = r
            continue
        if name == "ELSE":
            top = stack[-1]
            outer = all(a for _, a in stack[:-1])
            r = (not top[0]) and outer
            top[0] = True
            top[1] = r
            continue
        if name == "ENDIF":
            stack.pop()
            continue
        if not active():
            continue
        args = [expand(unq(a), V) for a in args]
        if name in ("LIBRARY", "PROGRAM", "FUZZ", "PROTO_LIBRARY", "DLL", "RESOURCES_LIBRARY",
                    "FUZZ_PROGRAM"):
            m.kind = name
        elif name == "UNITTEST_FOR":
            m.kind = "UNITTEST"
            m.srcdirs.append(args[0])
            m.peers += [args[0], "library/cpp/testing/unittest_main"]
        elif name == "GTEST":
            m.kind = "GTEST"
            m.peers.append("library/cpp/testing/gtest_main")
        elif name == "UNITTEST":
            m.kind = "UNITTEST"
            m.peers.append("library/cpp/testing/unittest_main")
        elif name == "SRCDIR":
            m.srcdirs += args
        elif name == "NO_UTIL":
            m.no_util = True
        elif name == "NO_RUNTIME":
            m.no_runtime = True
            m.no_util = m.no_util
        elif name == "NO_COMPILER_WARNINGS":
            pass
        elif name in ("DEFAULT", "SET"):
            if name == "SET" or args[0] not in V or True:
                if name == "DEFAULT" and args[0] in V and V[args[0]] != "":
                    continue
                V[args[0]] = " ".join(args[1:])
        elif name == "ENABLE":
            V[args[0]] = "yes"
        elif name == "DISABLE":
            V[args[0]] = ""
        elif name == "INCLUDE":
            f = os.path.join(ROOT, args[0])
            if os.path.exists(f):
                run_calls(m, V, parse_calls(open(f).read()), path)
        elif name == "PEERDIR":
            skip_next = False
            for a in args:
                if a in ("ADDINCL", "GLOBAL", "FOR"):
                    continue
                if a.startswith("${") or a.startswith("$"):
                    continue
                m.peers.append(a)
        elif name == "ADDINCL":
            glob = False
            i = 0
            while i < len(args):
                a = args[i]
                if a == "GLOBAL":
                    glob = True
                elif a in ("ONE_LEVEL",):
                    pass
                elif a == "FOR":
                    i += 1  # skip kind, then its path
                    glob = False
                    i += 1
                    glob = False
                    continue
                else:
                    (m.gaddincl if glob else m.addincl).append(a)
                    glob = False
                i += 1
        elif name == "CFLAGS":
            glob = False
            for a in args:
                if a == "GLOBAL":
                    glob = True
                else:
                    (m.gcflags if glob else m.cflags).append(a)
                    glob = False
        elif name == "CONLYFLAGS":
            m.conlyflags += [a for a in args if a != "GLOBAL"]
        elif name == "CXXFLAGS":
            m.cxxflags += [a for a in args if a != "GLOBAL"]
        elif name == "EXTRALIBS":
            m.extralibs += args
        elif name == "LDFLAGS":
            m.extralibs += args
        elif name in ("SRCS", "SRC", "JOIN_SRCS", "SRCS_GLOBAL", "GLOBAL_SRCS"):
            files = [a for a in args if a != "GLOBAL"]
            if name == "JOIN_SRCS":
                files = files[1:]
            if name == "SRC":
                f, extra = files[0], files[1:]
                m.srcs.append((f, extra))
            else:
                for f in files:
                    m.srcs.append((f, []))
        elif name.startswith("SRC_C_") or name.startswith("SRC_CPP_"):
            ext = name.split("_")[-1]
            if ext in SIMD:
                m.srcs.append((args[0], SIMD[ext].split() + args[1:]))
            else:
                m.srcs.append((args[0], args[1:]))
        elif name == "SRC_C_NO_LTO":
            m.srcs.append((args[0], args[1:]))


def collect(top):
    """Return (modules in dependency order, global addincl, global cflags)."""
    order, seen = [], set()

    def visit(p):
        if p in seen:
            return
        seen.add(p)
        if any(p == s or p.startswith(s + "/") for s in SKIP_PEERS):
            return
        m = load(p)
        for q in m.peers:
            visit(q)
        if not m.no_util and not m.no_runtime and p != "util" and not p.startswith("util/") \
                and not p.startswith("contrib/"):
            visit("util")
        order.append(m)

    visit(top)
    return order


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--cc", required=True)
    ap.add_argument("--cxx", required=True)
    ap.add_argument("--cflags", default="")
    ap.add_argument("--cxxflags", default="")
    ap.add_argument("--toolflags", default="-O1")
    ap.add_argument("--objdir", required=True)
    ap.add_argument("--bindir", required=True)
    ap.add_argument("--standalone-obj", required=True)
    ap.add_argument("--extra-obj", dest="extra_obj", action="append", default=[])
    ap.add_argument("--fuzzer", default="-fsanitize=fuzzer")
    ap.add_argument("--ragel", default="contrib/tools/ragel6/bin")
    ap.add_argument("--testflags", default="-O1 -g0")
    ap.add_argument("--testdir", default="")
    ap.add_argument("--test-extra-obj", dest="test_extra_obj", action="append", default=[])
    ap.add_argument("--test", action="append", default=[], help="name=module-dir (normal flags, no sanitizers)")
    ap.add_argument("--exe", action="append", default=[], help="name=module-dir")
    a = ap.parse_args()

    lines = ["ninja_required_version = 1.7", "builddir = " + os.path.dirname(os.path.abspath(a.out)),
             "rule cc", "  command = %s $flags -MMD -MF $out.d -c $in -o $out" % a.cc,
             "  depfile = $out.d", "  deps = gcc",
             "rule cxx", "  command = %s $flags -MMD -MF $out.d -c $in -o $out" % a.cxx,
             "  depfile = $out.d", "  deps = gcc",
             "rule ar", "  command = rm -f $out && ar rcs $out $in",
             "rule ragel", "  command = $ragel -CG2 -L -I. -o $out $in",
             "rule link", "  command = $cmd", ""]
    emitted = set()

    def closure(mod, objdir, cflags, cxxflags, ragel_bin):
        order = collect(mod)
        gincl, gcfl, extra = [], [], []
        for m in order:
            gincl += m.gaddincl
            gcfl += m.gcflags
            extra += m.extralibs
        libs, top_objs = [], []
        for m in order:
            objs = []
            for f, fl in m.srcs:
                if f.endswith(SKIP_EXT) and not f.endswith(".rl6"):
                    continue
                src = None
                for d in [m.path] + m.srcdirs + [""]:
                    c = os.path.join(d, f)
                    if os.path.exists(os.path.join(ROOT, c)):
                        src = c
                        break
                if src is None:
                    print("warning: missing source %s (from %s)" % (src or f, m.path), file=sys.stderr)
                    continue
                incl = " ".join("-I" + i for i in (m.addincl + m.gaddincl + gincl))
                flags = cflags + " " + BASE_C + incl + " " + " ".join(m.cflags + gcfl + fl)
                oname = src.replace("/", "__")
                implicit = ""
                if src.endswith(".rl6"):
                    gen = os.path.join(objdir, "gen", oname + ".cpp")
                    if gen not in emitted:
                        emitted.add(gen)
                        lines.append("build %s: ragel %s | %s" % (gen, src, ragel_bin))
                        lines.append("  ragel = " + ragel_bin)
                    src = gen
                    # generated file includes by repo-relative path
                o = os.path.join(objdir, oname + ".o")
                if src.endswith(".c") or src.endswith((".S", ".s")):
                    rule = "cc"
                    if src.endswith(".c"):
                        flags += " " + " ".join(m.conlyflags)
                else:
                    rule = "cxx"
                    flags += " " + cxxflags + " " + " ".join(m.cxxflags)
                if o not in emitted:
                    emitted.add(o)
                    lines.append("build %s: %s %s" % (o, rule, src))
                    lines.append("  flags = " + flags)
                objs.append(o)
            if m is order[-1]:
                top_objs = objs
                continue
            if objs:
                lib = os.path.join(objdir, m.path.replace("/", "__") + ".a")
                if lib not in emitted:
                    emitted.add(lib)
                    lines.append("build %s: ar %s" % (lib, " ".join(objs)))
                libs.append(lib)
        return top_objs, libs, extra

    # ragel6 (in-tree source) builds with plain flags, no sanitizers
    tool_obj = os.path.join(a.objdir, "tool")
    ragel_bin = os.path.join(a.bindir, "ragel6")
    t_objs, t_libs, t_extra = closure(a.ragel, tool_obj, a.toolflags, a.toolflags + " -std=c++20", ragel_bin)
    lines.append("build %s: link %s %s" % (ragel_bin, " ".join(t_objs), " ".join(t_libs)))
    lines.append("  cmd = %s %s %s %s -o %s" % (a.cxx, a.toolflags, " ".join(t_objs), " ".join(t_libs), ragel_bin))

    exes = []
    for spec in a.exe:
        name, mod = spec.split("=")
        top_objs, libs, extra = closure(mod, a.objdir, a.cflags, a.cxxflags, ragel_bin)
        for mode, drv in (("", a.fuzzer), ("-standalone", a.standalone_obj)):
            out = os.path.join(a.bindir, name + mode)
            cmd = "%s %s %s %s %s -Wl,--start-group %s -Wl,--end-group %s -lpthread -ldl -lrt -latomic -o %s" % (
                a.cxx, a.cflags, " ".join(top_objs), " ".join(a.extra_obj), drv,
                " ".join(reversed(libs)), " ".join(extra), out)
            lines.append("build %s: link %s %s %s | %s" % (
                out, " ".join(top_objs), " ".join(libs), " ".join(a.extra_obj), a.standalone_obj))
            lines.append("  cmd = " + cmd)
            exes.append(out)
    for spec in a.test:
        name, mod = spec.split("=")
        top_objs, libs, extra = closure(mod, os.path.join(a.testdir, name + "-obj"), a.testflags,
                                        a.testflags + " -std=c++20 -include format", ragel_bin)
        out = os.path.join(a.testdir, name)
        cmd = "%s %s %s %s -Wl,--start-group %s -Wl,--end-group %s -lpthread -ldl -lrt -latomic -o %s" % (
            a.cxx, a.testflags, " ".join(top_objs), " ".join(a.test_extra_obj), " ".join(reversed(libs)), " ".join(extra), out)
        lines.append("build %s: link %s %s %s" % (out, " ".join(top_objs), " ".join(libs), " ".join(a.test_extra_obj)))
        lines.append("  cmd = " + cmd)
        exes.append(out)
    lines.append("default " + " ".join(exes))
    open(a.out, "w").write("\n".join(lines) + "\n")


BASE_C = ("-D_GNU_SOURCE -D_FILE_OFFSET_BITS=64 -D_LARGEFILE_SOURCE -D__STDC_CONSTANT_MACROS "
          "-D__STDC_FORMAT_MACROS -D__STDC_LIMIT_MACROS -D_THREAD_SAFE -D_PTHREADS -D_REENTRANT "
          "-DNDEBUG -DFAKEID=mayhem -fPIC -w "
          "-msse2 -msse3 -mssse3 -msse4.1 -msse4.2 -mpopcnt -mcx16 -DLIBCXX_BUILDING_LIBGCC "
          "-I. -Icontrib/libs/cxxsupp/system_stl/include ")

SKIP_EXT = (".rl5", ".gperf", ".proto", ".fbs", ".in", ".py", ".pyx", ".cu", ".swg", ".l", ".y",
            ".ypp", ".lex", ".yy", ".h", ".inc", ".asm", ".masm", ".json")

if __name__ == "__main__":
    main()
