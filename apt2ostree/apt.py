#!/usr/bin/python

import errno
import glob
import hashlib
import os
import pipes
import sys
if sys.version_info[0] >= 3:
    from urllib.parse import unquote
else:
    from urllib import unquote
from collections import namedtuple

from .ninja import Rule
from .ostree import ostree_addfile, ostree_combine, OstreeRef


DEB_POOL_MIRRORS = []


update_lockfile = Rule("update_lockfile", """\
    set -ex;
    export tmpdir="_build/tmp/update_lockfile/$$(systemd-escape $out)";
    rm -rf $$tmpdir;
    mkdir -p $$tmpdir;
    export HOME=$$tmpdir;
    $create_mirrors;
    aptly lockfile create
        -mirrors "$mirrors"
        -architectures=$architecture
        $keyring_arg -gpg-provider=internal
        $packages >$lockfile~;
    if cmp $lockfile~ $lockfile; then
        rm $lockfile~;
    else
        mv $lockfile~ $lockfile;
    fi;
    rm -rf "$$tmpdir";
""", inputs=['.FORCE'], outputs=['update-lockfile-$lockfile'])

dpkg_base = Rule(
    "dpkg_base", """\
    set -ex;
    tmpdir=_build/tmp/apt/dpkg-base/$architecture;
    rm -rf "$$tmpdir";
    mkdir -p $$tmpdir;
    cd $$tmpdir;
    mkdir -p etc/apt/preferences.d
             etc/apt/sources.list.d
             etc/apt/trusted.gpg.d
             etc/network
             usr/share/info
             var/lib/dpkg/info
             var/cache/apt/archives/partial
             var/lib/apt/lists/auxfiles
             var/lib/apt/lists/partial;
    echo 1 >var/lib/dpkg/info/format;
    echo "$architecture" >var/lib/dpkg/arch;
    touch etc/shells
          var/cache/apt/archives/lock
          var/lib/dpkg/diversions
          var/lib/dpkg/lock
          var/lib/dpkg/lock-frontend
          var/lib/dpkg/statoverride
          var/lib/apt/lists/lock
          usr/share/info/dir;
    chmod 0640 var/cache/apt/archives/lock
               var/lib/apt/lists/lock
               var/lib/dpkg/lock
               var/lib/dpkg/lock-frontend;
    chmod 0700 var/cache/apt/archives/partial
               var/lib/apt/lists/partial;
    cd -;
    ostree --repo=$ostree_repo commit -b "deb/dpkg-base/$architecture"
        --tree=dir=$$tmpdir
        --no-bindings --orphan --timestamp=0 --owner-uid=0 --owner-gid=0;
    rm -rf "$$tmpdir";
    """, restat=True,
    output_type=OstreeRef,
    outputs=["$ostree_repo/refs/heads/deb/dpkg-base/$architecture"],
    order_only=["$ostree_repo/config"])

apt_base = Rule(
    "apt_base", """\
    tmpdir="$$(mktemp -dp $builddir/tmp -t apt_base.XXXXXX)";
    mkdir -p $$tmpdir/etc/apt/sources.list.d;
    printf "deb [arch=%s] %s %s %s\\n" $architecture $archive_url $distribution "$components"
        >$$tmpdir/etc/apt/sources.list.d/$name.list;
    ostree --repo=$ostree_repo commit -b deb/apt_base/$_args_digest
           --tree=dir=$$tmpdir
           --no-bindings --orphan --timestamp=0 --owner-uid=0 --owner-gid=0;
    rm -rf "$$tmpdir";
    """,
    output_type=OstreeRef,
    outputs=["$ostree_repo/refs/heads/deb/apt_base/$_args_digest"],
    order_only=["$ostree_repo/config"], restat=True)

# Ninja will rebuild the target if the contents of the rule changes.  We don't
# want to redownload a deb just because the list of mirrors has changed, so
# instead we write _build/deb_pool_mirrors and explicitly **don't** declare a
# dependency on it.
download_deb = Rule(
    "download_deb", """\
        download() {
            curl -L --fail -o $$tmpdir/deb $$1;
            actual_sha256="$$(sha256sum $$tmpdir/deb | cut -f1 -d ' ')";
            if [ "$$actual_sha256" != "$sha256sum" ]; then
                printf "FAIL: SHA256sum %s from %s doesn't match %s" \\
                    "$$actual_sha256" "$$1" "$sha256sum";
                return 1;
            else
                return 0;
            fi;
        };

        set -ex;
        tmpdir=$builddir/tmp/download-deb/$aptly_pool_filename;
        mkdir -p "$$tmpdir";
        while read mirror; do
            download file://$$PWD/$builddir/apt/mirror/${filename} && break;
            download $$mirror/${filename} && break;
            download $$mirror/$aptly_pool_filename && break;
        done <$builddir/deb_pool_mirrors;
        if ! [ -e $$tmpdir/deb ]; then
            echo Failed to download ${filename};
            exit 1;
        fi;
        cd $$tmpdir;
        ar x deb;
        cd -;
        control=$$(find $$tmpdir -name 'control.tar.*');
        data=$$(find $$tmpdir -name 'data.tar.*');
        ostree --repo=$ostree_repo commit -b $ref_base/data
               --tree=tar=$$data --no-bindings --orphan --timestamp=0
               -s $aptly_pool_filename" data";
        ostree --repo=$ostree_repo commit -b $ref_base/control
               --tree=tar=$$control --no-bindings --orphan --timestamp=0
               -s $aptly_pool_filename" control";
        if [ "$apt_should_mirror" = "True" ]; then
            mkdir -p "$builddir/apt/mirror/$$(dirname $filename)";
            mv $$tmpdir/deb "$builddir/apt/mirror/$filename";
        fi;
        rm -rf $$tmpdir;
    """,
    restat=True,
    output_type=(OstreeRef, OstreeRef),
    outputs=['$ostree_repo/refs/heads/$ref_base/data',
             '$ostree_repo/refs/heads/$ref_base/control'],
    order_only=["$ostree_repo/config"],
    description="Download $aptly_pool_filename")

make_dpkg_info = Rule(
    "make_dpkg_info", """\
        overwrite_if_changed () {
            if ! cmp $$1 $$2; then
                mv $$1 $$2;
            fi;
        };
        set -ex;
        tmpdir=$builddir/tmp/make_dpkg_info/$sha256sum;
        rm -rf "$$tmpdir";
        mkdir -p $$tmpdir/out/var/lib/dpkg/info;
        ostree --repo=$ostree_repo checkout --repo=$ostree_repo -UH "$ref_base/control" "$$tmpdir/control";
        multi_arch=$$(awk '/^Multi-Arch:/ {print $$2}' $$tmpdir/control/control);
        if [ "$$multi_arch" = "same" ]; then
            architecture=$$(awk '/^Architecture:/ {print $$2}' $$tmpdir/control/control);
            suffix=":$$architecture";
        fi;
        ostree --repo=$ostree_repo ls -R $ref_base/data --nul-filenames-only
        | tr '\\0' '\\n' 
        | sed 's,^/$$,/.,' >$$tmpdir/out/var/lib/dpkg/info/$pkgname$$suffix.list;
        cd "$$tmpdir";
        for x in conffiles
                 config
                 md5sums
                 postinst
                 postrm
                 preinst
                 prerm
                 shlibs
                 symbols
                 templates
                 triggers; do
            if [ -e "control/$$x" ]; then
                mv "control/$$x" "out/var/lib/dpkg/info/$pkgname$$suffix.$$x";
            fi;
        done;
        ( cat control/control; echo Status: install ok unpacked; echo ) >status;
        ( cat control/control; echo ) >available;
        cd -;
        ostree --repo=$ostree_repo commit -b "$ref_base/info" --tree=dir=$$tmpdir/out
            --no-bindings --orphan --timestamp=0 --owner-uid=0 --owner-gid=0
            --no-xattrs;
        overwrite_if_changed $$tmpdir/status $builddir/$ref_base/status;
        overwrite_if_changed $$tmpdir/available $builddir/$ref_base/available;
        rm -rf "$$tmpdir";
    """,
    restat=True,
    output_type=(str, str, OstreeRef),
    outputs=[
        '$builddir/$ref_base/status',
        '$builddir/$ref_base/available',
        '$ostree_repo/refs/heads/$ref_base/info'],
    order_only=["$ostree_repo/config"],
    inputs=["$ostree_repo/refs/heads/$ref_base/control",
            "$ostree_repo/refs/heads/$ref_base/data"])

do_usrmove = Rule(
    "do_usrmove", """\
    set -ex;
    if ! ostree --repo=$ostree_repo ls "$in_branch" | grep -e /bin -e /lib -e /sbin; then
        ostree --repo=$ostree_repo commit -b $out_branch
               --no-bindings --orphan --timestamp=0 --tree=ref=$in_branch;
        exit 0;
    fi;
    mkdir -p $builddir/tmp/do_usrmove;
    tmpdir=$builddir/tmp/do_usrmove/$$(systemd-escape $in_branch);
    rm -rf "$$tmpdir";
    ostree --repo=$ostree_repo checkout -UH $in_branch "$$tmpdir";
    ostree --repo=$ostree_repo checkout -UH :$in_branch:bin "$$tmpdir/usr/bin" --union && rm -rf $$tmpdir/bin && ln -s usr/bin $$tmpdir/bin || true;
    ostree --repo=$ostree_repo checkout -UH :$in_branch:sbin "$$tmpdir/usr/sbin" --union && rm -rf $$tmpdir/sbin && ln -s usr/sbin $$tmpdir/sbin || true;
    ostree --repo=$ostree_repo checkout -UH :$in_branch:lib "$$tmpdir/usr/lib" --union  && rm -rf $$tmpdir/lib && ln -s usr/lib $$tmpdir/lib || true;

    ostree --repo=$ostree_repo commit --devino-canonical -b $out_branch
           --no-bindings --orphan --timestamp=0 --tree=dir=$$tmpdir
           --owner-uid=0 --owner-gid=0;
    """,
    inputs=["$ostree_repo/refs/heads/$in_branch"],
    output_type=OstreeRef,
    outputs=["$ostree_repo/refs/heads/$out_branch"],
    description="usrmove $in_branch")

deb_combine_meta = Rule(
    "deb_combine_meta", """\
    set -e;
    tmpdir=$builddir/tmp/deb_combine_$meta/$pkgs_digest;
    rm -rf "$$tmpdir";
    mkdir -p "$$tmpdir/var/lib/dpkg";
    cat $in >$$tmpdir/var/lib/dpkg/$meta;
    ostree --repo=$ostree_repo commit -b "deb/images/$pkgs_digest/$meta"
        --tree=dir=$$tmpdir --no-bindings --orphan --timestamp=0
        --owner-uid=0 --owner-gid=0 --no-xattrs;
    rm -rf "$$tmpdir";
    """,
    output_type=OstreeRef,
    outputs=["$ostree_repo/refs/heads/deb/images/$pkgs_digest/$meta"],
    order_only=["$ostree_repo/config"],
    description="var/lib/dpkg/$meta for $pkgs_digest")


# This is a really naive implementation calling `dpkg --configure -a` in a
# container using `bwrap` and `sudo`.  A proper implementation will be
# container-system dependent and should not require root.
dpkg_configure = Rule(
    "dpkg_configure", """\
        set -ex;
        tmpdir=$builddir/tmp/dpkg_configure/$out_branch;
        sudo rm -rf "$$tmpdir";
        mkdir -p $$tmpdir;
        TARGET=$$tmpdir/co;
        sudo ostree --repo=$ostree_repo checkout --force-copy $in_branch $$TARGET;
        sudo cp $$TARGET/usr/share/base-passwd/passwd.master $$TARGET/etc/passwd;
        sudo cp $$TARGET/usr/share/base-passwd/group.master $$TARGET/etc/group;

        BWRAP="sudo bwrap --bind $$TARGET / --proc /proc --dev /dev
            --tmpfs /tmp --tmpfs /run --setenv LANG C.UTF-8
            --setenv DEBIAN_FRONTEND noninteractive
            $binfmt_misc_support";
        if [ -x $$TARGET/var/lib/dpkg/info/dash.preinst ]; then
            $$BWRAP /var/lib/dpkg/info/dash.preinst install;
        fi;
        printf '#!/bin/sh\\nexit 101'
        | sudo tee $$tmpdir/co/usr/sbin/policy-rc.d;
        sudo chmod a+x $$tmpdir/co/usr/sbin/policy-rc.d;

        if [ -f $$TARGET/usr/lib/insserv/insserv ]; then
            $$BWRAP dpkg-divert --local --rename --add /usr/lib/insserv/insserv;
            sudo ln -s ../../../bin/true $$TARGET/usr/lib/insserv/insserv;
            sudo ln -s ../bin/true $$TARGET/sbin/insserv;
        fi;

        if [ -f $$TARGET/usr/bin/mawk ]; then
            sudo ln -sf mawk $$TARGET/usr/bin/awk;
        fi;

        $$BWRAP dpkg --configure -a;

        sudo rm -f $$TARGET/etc/machine-id;

        sudo tar -C $$tmpdir/co -c .
        | ostree --repo=$ostree_repo commit --branch $out_branch --no-bindings
                 --orphan --timestamp=0 --tree=tar=/dev/stdin;
        sudo rm -rf $$tmpdir;
    """,
    output_type=OstreeRef,
    outputs=["$ostree_repo/refs/heads/$out_branch"],
    inputs=["$ostree_repo/refs/heads/$in_branch"],
    order_only=["$ostree_repo/config"],
    # pool console is used because the above involves sudo which might need
    # to ask for a password
    pool="console")


AptSource = namedtuple(
    "AptSource", "architecture distribution archive_url components keyrings")


_UBUNTU_RELEASES = {
    "14.04": "trusty",
    "16.04": "xenial",
    "18.04": "bionic",
}


def ubuntu_apt_sources(release="bionic", architecture="amd64"):
    if release in _UBUNTU_RELEASES:
        release = _UBUNTU_RELEASES[release]
    if architecture in ["amd64", "i386"]:
        archive_url = "http://archive.ubuntu.com/ubuntu"
    else:
        archive_url = "http://ports.ubuntu.com/ubuntu-ports"
    return [
        AptSource(architecture, release, archive_url,
                  "main restricted universe multiverse",
                  keyrings_for("ubuntu", release)),
        AptSource(architecture, "%s-updates" % release, archive_url,
                  "main restricted universe multiverse",
                  keyrings_for("ubuntu", "%s-updates" % release)),
        AptSource(architecture, "%s-security" % release, archive_url,
                  "main restricted universe multiverse",
                  keyrings_for("ubuntu", "%s-security" % release)),
    ]


def keyrings_for(distro, release):
    if os.path.exists(_find_file("keyrings/%s/%s" % (distro, release))):
        d = "keyrings/%s/%s" % (distro, release)
    elif os.path.exists(
            _find_file("keyrings/%s/%s" % (distro, release.split("-")[0]))):
        d = "keyrings/%s/%s" % (distro, release.split("-")[0])
    else:
        raise Exception("No known key for %s/%s" % (distro, release))

    out = []
    for x in glob.glob(_find_file("%s/*.gpg" % d)):
        out.append("$apt2ostreedir/%s" % os.path.relpath(x, _find_file(".")))

    sys.stderr.write("%s\n" % out)
    return out


class Apt(object):
    def __init__(self, ninja, deb_pool_mirrors=None, apt_should_mirror=False):
        if deb_pool_mirrors is None:
            deb_pool_mirrors = DEB_POOL_MIRRORS

        self.ninja = ninja
        self.archive_urls = set()
        self.deb_pool_mirrors = deb_pool_mirrors
        self._update_lockfile_rules = set()

        ninja.variable("apt_should_mirror", str(bool(apt_should_mirror)))

        self.ninja.add_generator_dep(__file__)

        # Get these files added to .gitignore:
        ninja.add_target("%s/config" % ninja.global_vars['ostree_repo'])
        ninja.add_target("%s/objects" % ninja.global_vars['ostree_repo'])

    def write_phony_rules(self):
        self.ninja.build("update-apt-lockfiles", "phony",
                         inputs=list(self._update_lockfile_rules))

    def build_image(self, lockfile, packages, apt_sources, unpack_only=False,
                    usrmove=False):
        self.generate_lockfile(lockfile, packages, apt_sources)
        stage_1 = self.image_from_lockfile(
            lockfile, apt_sources[0].architecture, usrmove)
        sources_lists = []
        for n, apt_source in enumerate(apt_sources):
            sources_lists.append(apt_base.build(
                self.ninja, archive_url=apt_source.archive_url,
                components=apt_source.components,
                architecture=apt_source.architecture,
                distribution=apt_source.distribution,
                name="apt2ostree-%i" % n))
        if unpack_only:
            out = stage_1
        else:
            stage_2 = self.second_stage(stage_1, apt_sources[0].architecture)
            assert "unpacked" in stage_1.ref
            complete = ostree_combine.build(
                self.ninja,
                inputs=[stage_2.filename] + [x.filename for x in sources_lists],
                branch=stage_1.ref.replace("unpacked", "complete"))
            self.ninja.build(
                "image-for-%s" % lockfile, "phony", inputs=complete.filename)
            out = complete
        out.stage_1 = stage_1
        out.sources_lists = sources_lists
        return out

    def second_stage(self, unpacked, architecture, branch=None):
        if branch is None:
            assert "unpacked" in unpacked.ref
            branch = unpacked.ref.replace("unpacked", "configured")
        order_only = []
        if architecture == "armhf":
            binfmt_misc_support = \
                "--ro-bind /usr/bin/qemu-arm-static /usr/bin/qemu-arm-static"
            order_only.append('/usr/bin/qemu-arm-static')
        elif architecture in ["amd64", "i686"]:
            binfmt_misc_support = ""
        else:
            assert False, ("binfmt_misc support for architecture %r not "
                           "implemented in apt2ostree.  Modify lines above to "
                           "add support if possible.")
        configured_ref = dpkg_configure.build(
            self.ninja,
            in_branch=unpacked.ref,
            out_branch=branch,
            order_only=order_only,
            binfmt_misc_support=binfmt_misc_support)
        return configured_ref

    def generate_lockfile(self, lockfile, packages, apt_sources):
        packages = sorted(packages)

        this_dir_rel = os.path.relpath(
            os.path.dirname(os.path.abspath(__file__)))

        # We need to call aptly mirror create for each of our apt_sources. It's
        # difficult to pass that kind of structured data through to a rule via
        # a variable - so instead we write out a shell script which makes the
        # calls for us.
        mirrors = []
        gen_mirror_cmds = []
        all_keyring_args = set()
        s = hashlib.sha256()
        for n, src in enumerate(apt_sources):
            mirrors.append("mirror-%i" % n)
            self.archive_urls.add(src.archive_url)
            s.update(repr(apt_sources).encode('utf-8'))
            keyring_arg = [
                "-keyring=" + x.replace("$apt2ostreedir", this_dir_rel)
                for x in src.keyrings]
            all_keyring_args = all_keyring_args.union(keyring_arg)
            cmd = [
                "aptly", "mirror", "create",
                "-architectures=" + src.architecture] + keyring_arg + [
                "-gpg-provider=internal",
                "mirror-%i" % n, src.archive_url,
                src.distribution] + src.components.split()
            gen_mirror_cmds.append(" ".join(pipes.quote(x) for x in cmd))

        create_mirrors = "_build/apt/lockfile/create_mirrors-%s" % (
            s.hexdigest()[:7])
        mkdir_p(os.path.dirname(create_mirrors))
        with self.ninja.open(create_mirrors, "w") as f:
            f.write("#!/bin/sh -ex\n")
            for x in gen_mirror_cmds:
                f.write(x + "\n")
            os.fchmod(f.fileno(), 0o755)

        keyring_arg = " ".join("-keyring=")

        out = update_lockfile.build(
            self.ninja,
            lockfile=lockfile,
            packages=packages,
            create_mirrors=create_mirrors,
            mirrors=",".join(mirrors),
            architecture=apt_sources[0].architecture,
            keyring_arg=" ".join(all_keyring_args))
        self._update_lockfile_rules.update(out)
        return lockfile

    def image_from_lockfile(self, lockfile, architecture=None, usrmove=False):
        if architecture is None:
            architecture = "amd64"
        base = dpkg_base.build(self.ninja, architecture=architecture)

        all_data = []
        all_info = []
        all_status = []
        all_available = []

        with self.ninja.open('_build/deb_pool_mirrors', 'w') as f:
            for x in self.deb_pool_mirrors:
                f.write(x + "\n")
            for x in self.archive_urls:
                f.write(x + "\n")

        try:
            with self.ninja.open(lockfile) as f:
                for pkg in parse_packages(f):
                    filename = unquote(pkg['Filename'])
                    aptly_pool_filename = "%s/%s/%s_%s" % (
                        pkg['SHA256'][:2], pkg['SHA256'][2:4],
                        pkg['SHA256'][4:], os.path.basename(filename))
                    ref_base = ("deb/pool/" + aptly_pool_filename
                                .replace('+', '_').replace('~', '_'))
                    data, _ = download_deb.build(
                        self.ninja, sha256sum=pkg['SHA256'], filename=filename,
                        aptly_pool_filename=aptly_pool_filename,
                        ref_base=ref_base)
                    if usrmove:
                        data = do_usrmove.build(
                            self.ninja,
                            in_branch=data.ref,
                            out_branch=data.ref + '-usrmove')
                    data = self.fix_package(
                        pkg['Package'], pkg['Version'], data)
                    all_data.append(data.filename)
                    status, available, info = make_dpkg_info.build(
                        self.ninja, sha256sum=pkg['SHA256'],
                        pkgname=pkg['Package'], ref_base=ref_base)
                    all_status.append(status)
                    all_available.append(available)
                    all_info.append(info.filename)
        except IOError as e:
            # lockfile hasn't been created yet.  Presumably it will be created
            # by running `ninja update-apt-lockfiles` soon so this isn't a fatal
            # error.
            if e.errno != errno.ENOENT:
                raise

        digest = lockfile.replace('/', '_')

        rootfs = ostree_combine.build(
            self.ninja, inputs=all_data,
            branch="deb/images/%s/data_combined" % digest)
        dpkg_infos = ostree_combine.build(
            self.ninja, inputs=all_info,
            branch="deb/images/%s/info_combined" % digest)

        dpkg_status = deb_combine_meta.build(
            self.ninja, inputs=all_status,
            pkgs_digest=digest, meta="status")

        dpkg_available = deb_combine_meta.build(
            self.ninja, inputs=all_available,
            pkgs_digest=digest, meta="available")

        image = ostree_combine.build(
            self.ninja,
            inputs=[base.filename, dpkg_infos.filename, dpkg_status.filename,
                    dpkg_available.filename, rootfs.filename],
            implicit=lockfile,
            branch="deb/images/%s/unpacked" % digest)
        self.ninja.build("unpacked-image-for-%s" % lockfile,
                         "phony", inputs=image.filename)
        return image

    def fix_package(self, pkgname, version, data):
        """
        Here we can apply quirks as required to get particular packages to
        install.
        """
        if pkgname == 'pylint' and version < "2.1.1-2":
            # This is a backport of :
            #
            # > "Use "byte compile exception patterns" feature to exclude tests
            # > from byte-compiling, instead of shipping a manual postinst file.
            #
            # This fixes installing python2.7-minimal on Ubuntu bionic.  It
            # attempts to compile the contents of dist-packages/pylint/tests
            # much of which isn't valid Python files.
            #
            # See also https://salsa.debian.org/python-team/applications/pylint/commit/28d9e9231f58ef9a1debeb4ae34f4d7441c36a67
            return ostree_addfile.build(
                self.ninja, in_branch=data.ref,
                prefix="/usr/share/python/bcep",
                in_file=_find_file("quirks/pylint/pylint.bcep"),
                out_branch=data.ref + "-fixed")
        else:
            return data


def parse_packages(stream):
    """Parses an apt Packages file"""
    pkg = {}
    label = None
    for line in stream:
        if line.strip() == '':
            if pkg:
                yield pkg
            pkg = {}
            label = None
            continue
        elif line == ' .':
            pkg[label] += '\n\n'
        elif line.startswith(" "):
            pkg[label] += '\n' + line[1:].strip()
        else:
            label, data = line.split(': ', 1)
            pkg[label] = data.strip()


def mkdir_p(d):
    """Python 3.2 has an optional argument to os.makedirs called exist_ok.  To
    support older versions of python we can't use this and need to catch
    exceptions"""
    try:
        os.makedirs(d)
    except OSError as e:
        if e.errno == errno.EEXIST and os.path.isdir(d) \
                and os.access(d, os.R_OK | os.W_OK):
            return
        else:
            raise


def _find_file(filename, this_dir=os.path.dirname(os.path.abspath(__file__))):
    return os.path.join(this_dir, filename)
