# The SteamOS device a port installs on: this machine (the porter runs on the device, in Desktop Mode or over SSH) or another one reached over SSH (user@host, or an alias
# of ~/.ssh/config). Both answer the same calls: a shell script, a folder pushed or pulled, a file put; so install.py does not care which.
# Over SSH: rsync when both ends have it (it skips the files already there: an update re-sends only what changed), else tar through ssh.
import os, shlex, shutil, subprocess
from pathlib import Path

from porter.upstream import BuildError

SSH_OPTS = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=15']   # never a password prompt in the middle of a port: keys or an agent


class DeviceError(BuildError):
    pass


class Local:
    """This machine"""
    name = 'this machine'
    remote = False

    def sh(self, script, input=None, check=True, timeout=600):
        r = subprocess.run(['sh', '-c', script], input=input, capture_output=True, timeout=timeout)
        if check and r.returncode: raise DeviceError(f'{script.splitlines()[0][:80]}: exit {r.returncode}: {r.stderr.decode(errors="replace").strip()[-400:]}')
        return r

    def push_tree(self, src, dst, keep=(), last=(), progress=None):
        """dst becomes a copy of src (files hard-linked when on the same volume); files in keep (paths in the folder) that dst already has stay as they are. The copy
        is made beside dst and swapped in whole, so last (the manifest, written last elsewhere) needs nothing more here"""
        src, dst = Path(src), Path(dst).expanduser()
        tmp = dst.with_name(dst.name + '.partial')
        shutil.rmtree(tmp, ignore_errors=True)
        n = 0
        for root, _, files in os.walk(src):
            for f in files:
                s = Path(root) / f; d = tmp / s.relative_to(src); d.parent.mkdir(parents=True, exist_ok=True)
                try: os.link(s, d)
                except OSError: shutil.copy2(s, d)
                n += 1
                if progress and n % 500 == 0: progress(n)
        for k in keep:   # the device's own, over the pack's: linked, not read (the Steam Deck engine's data image is gigabytes)
            if (dst / k).is_file():
                (tmp / k).unlink(missing_ok=True); (tmp / k).parent.mkdir(parents=True, exist_ok=True)
                try: os.link(dst / k, tmp / k)
                except OSError: shutil.copy2(dst / k, tmp / k)
        if dst.exists(): shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True); tmp.rename(dst)

    def pull_tree(self, src, dst):
        src = Path(src).expanduser()
        if not src.is_dir(): return []
        got = []
        for p in src.iterdir():
            if p.is_file() and not (Path(dst) / p.name).is_file():
                Path(dst).mkdir(parents=True, exist_ok=True); shutil.copy2(p, Path(dst) / p.name); got.append(p.name)
        return got

    def home(self): return str(Path.home())


class Ssh:
    """A device over SSH"""
    remote = True

    def __init__(self, target, ssh='ssh', rsync=None):
        self.target, self.ssh, self.rsync = target, ssh or 'ssh', rsync
        self.name = target
        self._remote_rsync = None

    def _cmd(self, script): return [self.ssh, *SSH_OPTS, self.target, 'sh -c ' + shlex.quote(script)]

    def sh(self, script, input=None, check=True, timeout=600):
        try: r = subprocess.run(self._cmd(script), input=input, capture_output=True, timeout=timeout)
        except FileNotFoundError: raise DeviceError(f'{self.ssh} not found: install an OpenSSH client') from None
        except subprocess.TimeoutExpired: raise DeviceError(f'{self.target}: no answer in {timeout} s') from None
        if r.returncode == 255: raise DeviceError(f'cannot reach {self.target} over SSH: {r.stderr.decode(errors="replace").strip()[-300:]} '
                                                  '(SSH on, and a key: ssh-copy-id ' + self.target + ')')
        if check and r.returncode: raise DeviceError(f'{self.target}: {script.splitlines()[0][:80]}: exit {r.returncode}: {r.stderr.decode(errors="replace").strip()[-400:]}')
        return r

    def home(self): return self.sh('printf %s "$HOME"').stdout.decode()

    def has_rsync(self):
        if self._remote_rsync is None: self._remote_rsync = bool(self.rsync) and self.sh('command -v rsync', check=False).returncode == 0
        return self._remote_rsync

    def push_tree(self, src, dst, keep=(), last=(), progress=None):
        src = Path(src); q = shlex.quote(dst)
        self.sh(f'mkdir -p {q}')
        if self.has_rsync():   # only what changed; --delete drops an older version's files, keep stays the device's own
            if last: self.sh('rm -f ' + ' '.join(f'{q}/{shlex.quote(k)}' for k in last))   # the folder is not complete until they come back, at the end
            cmd = [self.rsync, '-a', '--delete', '--partial', '-e', ' '.join([self.ssh, *SSH_OPTS])] + [f'--exclude=/{k}' for k in (*keep, *last)] + [f'{src}/', f'{self.target}:{dst}/']
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode: raise DeviceError(f'rsync to {self.target}:{dst} failed: {r.stderr.decode(errors="replace").strip()[-400:]}')
            for k in keep:   # the pack's defaults for files the device does not have yet
                if (src / k).is_file() and self.sh(f'test -e {q}/{shlex.quote(k)}', check=False).returncode: self.put_file(src / k, f'{dst}/{k}')
            for k in last:   # last: a folder with them is a complete one
                if (src / k).is_file(): self.put_file(src / k, f'{dst}/{k}')
            return
        # tar through ssh: the whole folder again, into a fresh copy beside it, swapped in at the end (a broken transfer leaves the old one)
        tmp = shlex.quote(dst + '.partial')
        save = ' '.join(f'[ -e {q}/{shlex.quote(k)} ] && mkdir -p {shlex.quote(str(Path(dst + ".partial", k).parent))} && '
                        f'{{ ln {q}/{shlex.quote(k)} {tmp}/{shlex.quote(k)} || cp -p {q}/{shlex.quote(k)} {tmp}/{shlex.quote(k)}; }};' for k in keep)   # (linked: gigabytes)
        tar = subprocess.Popen(['tar', '-C', str(src), '-cf', '-', '.'], stdout=subprocess.PIPE)
        try:
            # --skip-old-files (GNU tar, SteamOS's): the kept files saved first win over the pack's; any other tar failure stops before the swap
            r = subprocess.run(self._cmd(f'rm -rf {tmp} && mkdir -p {tmp} && {save} tar -C {tmp} --skip-old-files -xf - && rm -rf {q} && mv {tmp} {q}'),
                               stdin=tar.stdout, capture_output=True)
        finally: tar.stdout.close(); tar.wait()
        if r.returncode: raise DeviceError(f'copying to {self.target}:{dst} failed: {r.stderr.decode(errors="replace").strip()[-400:]}')

    def put_file(self, src, dst):
        with open(src, 'rb') as f: data = f.read()
        self.sh(f'mkdir -p {shlex.quote(str(Path(dst).parent))} && cat > {shlex.quote(dst)}', input=data)

    def pull_tree(self, src, dst):
        """Files of the device's folder src that dst lacks (captured shaders), copied into dst; their names"""
        r = self.sh(f'cd {shlex.quote(src)} 2>/dev/null && ls -1', check=False)
        names = [n for n in r.stdout.decode(errors='replace').split('\n') if n and not (Path(dst) / n).exists()]
        Path(dst).mkdir(parents=True, exist_ok=True)
        for n in names:
            data = self.sh(f'cat {shlex.quote(src + "/" + n)}').stdout
            (Path(dst) / n).write_bytes(data)
        return names


def target(device, ssh=None, rsync=None):
    """The device to install on: an SSH target, or this machine when device is empty"""
    return Ssh(device, ssh, rsync) if device else Local()


PROBE = r'''
echo "arch=$(uname -m)"
. /etc/os-release 2>/dev/null; echo "os=${ID:-?} ${VERSION_ID:-} ${VARIANT_ID:-}"
echo "kernel=$(uname -r)"
echo "page=$(getconf PAGESIZE)"
echo "glibc=$(getconf GNU_LIBC_VERSION 2>/dev/null | cut -d' ' -f2)"
for l in libSDL3.so.0 libvulkan.so.1 libavcodec.so; do
  p=$( (ldconfig -p 2>/dev/null || /sbin/ldconfig -p 2>/dev/null) | grep -m1 "$l" | sed 's/.*=> //'); echo "lib.$l=${p:-}"
done
gpu=""; for d in /sys/class/drm/card*/device; do [ -e "$d/of_node/compatible" ] && gpu="$gpu $(tr '\0' ' ' < "$d/of_node/compatible")"; done
[ -z "$gpu" ] && for d in /sys/class/drm/card*/device; do [ -e "$d/vendor" ] && gpu="$gpu pci:$(cat "$d/vendor"):$(cat "$d/device" 2>/dev/null)"; done; echo "gpu=$gpu"
echo "cpu=$(grep -m1 '^flags' /proc/cpuinfo 2>/dev/null | tr ' ' '\n' | grep -x -e avx2 -e fma -e bmi2 | tr '\n' ' ')"
echo "fuse=$(command -v fusermount3 || command -v fusermount || true)"
echo "steam=$( [ -d "$HOME/.local/share/Steam/userdata" ] && echo yes || echo no)"
df -Pk "$HOME" | awk 'NR==2 {print "free_home_kb=" $4}'
'''


def probe(t):
    """What the device is: {'arch', 'os', 'kernel', 'page', 'glibc', 'lib.<name>', 'gpu', 'cpu' (avx2 / fma / bmi2 it has), 'fuse', 'steam', 'free_home_kb'}"""
    out = {}
    for line in t.sh(PROBE, check=False).stdout.decode(errors='replace').splitlines():
        k, eq, v = line.partition('=')
        if eq: out[k] = v.strip()
    return out


def free_bytes(t, path):
    r = t.sh(f'mkdir -p {shlex.quote(path)} && df -Pk {shlex.quote(path)} | awk "NR==2 {{print \\$4}}"', check=False)
    try: return int(r.stdout.decode().strip()) * 1024
    except ValueError: return None
