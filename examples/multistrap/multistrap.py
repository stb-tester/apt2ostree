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

sys.path.append(os.path.dirname(__file__) + '/../..')
from apt2ostree import Apt, Ninja
from apt2ostree.multistrap import multistrap


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--ostree-repo", default="_build/ostree")
    parser.add_argument("-o", "--output", default="build.ninja")
    parser.add_argument(
        "config_file", nargs="+", help="multistrap config files")
    args = parser.parse_args(argv[1:])

    with Ninja(argv, ninjafile=args.output) as ninja:
        ninja.add_generator_dep(__file__)

        ninja.variable("ostree_repo", os.path.relpath(args.ostree_repo))

        apt = Apt(ninja)
        for cfg in args.config_file:
            image = multistrap(cfg, ninja, apt)
            ninja.default(image.filename)

        apt.write_phony_rules()

        # We write a gitignore file so we can use git-clean to remove build
        # artifacts that we no-longer produce:
        ninja.write_gitignore()


if __name__ == '__main__':
    sys.exit(main(sys.argv))
