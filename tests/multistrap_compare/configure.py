#!/usr/bin/python

"""
Configure script to build images from multistrap configs.  This supports an
ill-defined subset of multistrap configuration files.  Multiple multistrap
configuration files can be provided at one time and an image will be built for
each.

Usage:

    ./multistrap.py --ostree-repo=repo multistrap.conf

    # Create the packages lockfiles:
    ninja update-apt-lockfiles

    # There will now be a file called multistrap.conf.lock which you can check
    # into your source control system

    # Build the image(s)
    ninja

    # Inspect the built image:
    ostree --repo=repo ls -R refs/deb/images/multistrap.conf/configured
"""

import argparse
import os
import sys
from collections import namedtuple
from configparser import NoOptionError, SafeConfigParser

sys.path.append(os.path.dirname(__file__) + '/../..')
from apt2ostree import Apt, AptSource, Ninja, Rule
from apt2ostree.multistrap import multistrap, read_multistrap_config
from apt2ostree.ostree import ostree, OstreeRef
import apt2ostree.apt


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--ostree-repo", default="_build/ostree")
    args = parser.parse_args(argv[1:])

    config_files = ['multistrap.conf']

    with Ninja(argv) as ninja:
        ninja.add_generator_dep(__file__)

        ninja.variable("ostree_repo", os.path.relpath(args.ostree_repo))
        ostree.build(ninja)

        apt = Apt(ninja)
        for cfg in config_files:
            ours = multistrap(cfg, ninja, apt)
            orig = real_multistrap(cfg, ninja, apt)
            aptbs = apt_bootstrap(cfg, ninja)
            diff1 = ostree_diff.build(
                ninja, left=orig.stage_1.ref, right=ours.stage_1.ref)[0]
            diff2 = ostree_diff.build(
                ninja, left=aptbs.ref, right=ours.stage_1.ref)[0]
            bwrap_enter.build(ninja, ref=ours.ref)
            bwrap_enter.build(ninja, ref=orig.ref)
            bwrap_enter.build(ninja, ref=aptbs.ref)
            ninja.build("diff", "phony", inputs=[diff1, diff2])

        ninja.default("diff")

        apt.write_phony_rules()

        # We write a gitignore file so we can use git-clean to remove build
        # artifacts that we no-longer produce:
        ninja.write_gitignore()


# This can be used to compare images built with different systems:
ostree_diff = Rule("ostree_diff", """\
    ostree --repo=$ostree_repo diff $left $right;
    bash -xc 'diff -u <(ostree --repo=$ostree_repo ls -R $left)
                      <(ostree --repo=$ostree_repo ls -R $right)';""",
    outputs=["diff-$left-$right"],
    inputs=["$ostree_repo/refs/heads/$left",
            "$ostree_repo/refs/heads/$right"],
    implicit=['.FORCE'],
    order_only=["$ostree_repo/config"])


# This is useful for interactive exploration of the built images:
bwrap_enter = Rule("bwrap_enter", """\
    set -ex;
    mkdir -p $builddir/tmp/bwrap_enter;
    TARGET=$builddir/tmp/bwrap_enter/$$(echo $ref | sed s,/,-,g);
    sudo rm -rf "$$TARGET";
    sudo ostree --repo=$ostree_repo checkout --force-copy $ref $$TARGET;
    sudo bwrap --bind $$TARGET / --proc /proc --dev /dev --tmpfs /tmp
               --tmpfs /run --setenv LANG C.UTF-8
               --ro-bind /usr/bin/qemu-arm-static /usr/bin/qemu-arm-static
               --ro-bind "$$(readlink -f /etc/resolv.conf)" /etc/resolv.conf
               bash -i;
    """, pool="console",
    inputs=["$ostree_repo/refs/heads/$ref", '.FORCE'],
    outputs="bwrap_enter-$ref")


_real_multistrap = Rule("real_multistrap", """\
    TARGET="$builddir/tmp/multistrap/$name";
    rm -rf $$TARGET;
    set -ex;
    mkdir -p "$$TARGET/etc/apt";
    cp ubuntu-archive-keyring.gpg "$$TARGET/etc/apt/trusted.gpg";
    fakeroot multistrap -d $$TARGET -f $in;
    rm $$TARGET/var/cache/apt/archives/*.deb;
    fakeroot tar -C $$TARGET -c . |
    ostree --repo=$ostree_repo commit --orphan -b multistrap/$name/unpacked
        --no-bindings --tree=tar=/dev/stdin;
    rm -rf $$TARGET;
    """,
    outputs=['$ostree_repo/refs/heads/multistrap/$name/unpacked'],
    implicit=['ubuntu-archive-keyring.gpg'])


def real_multistrap(config_file, ninja, apt):
    cfg = read_multistrap_config(ninja, config_file)

    stage_1 = OstreeRef(_real_multistrap.build(
        ninja, inputs=[config_file], name=config_file.replace('/', '-'))[0])

    stage_2 = OstreeRef(apt.second_stage(
        stage_1, architecture=cfg.apt_source.architecture,
        branch=stage_1.ref.replace('unpacked', 'complete'))[0])
    stage_2.stage_1 = stage_1
    return stage_2


_apt_bootstrap = Rule("apt_bootstrap", """\
    TARGET="$builddir/tmp/apt_bootstrap/$$(systemd-escape $branch)";
    rm -rf $$TARGET;
    set -ex;
    ./apt-bootstrap/apt-bootstrap -a "$architecture" --components "$components"
        --packages "$packages" --keyring "$keyring" --required --important
        --no-recommends "$distribution" "$$TARGET" "$archive_url";
    fakeroot tar -C $$TARGET -c . |
    ostree --repo=$ostree_repo commit --orphan -b $branch
        --no-bindings --tree=tar=/dev/stdin;
    """, outputs="$ostree_repo/refs/heads/$branch")


def apt_bootstrap(config_file, ninja):
    apt_source, packages = read_multistrap_config(ninja, config_file)
    return OstreeRef(_apt_bootstrap.build(
        ninja, archive_url=apt_source.archive_url,
        distribution=apt_source.distribution,
        architecture=apt_source.architecture,
        components=apt_source.components,
        packages=packages, keyring='ubuntu-archive-keyring.gpg',
        branch="apt_bootstrap/%s/complete" % config_file)[0])


if __name__ == '__main__':
    sys.exit(main(sys.argv))
