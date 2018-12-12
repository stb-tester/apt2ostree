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
from configparser import NoOptionError, SafeConfigParser

sys.path.append(os.path.dirname(__file__) + '/../..')
from apt2ostree import Apt, Ninja, AptSource


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--ostree-repo", default="_build/ostree")
    parser.add_argument(
        "config_file", nargs="+", help="multistrap config files")
    args = parser.parse_args(argv[1:])

    with Ninja(argv) as ninja:
        ninja.add_generator_dep(__file__)

        ninja.variable("ostree_repo", os.path.relpath(args.ostree_repo))

        apt = Apt(ninja)
        for cfg in args.config_file:
            multistrap(cfg, ninja, apt)

        apt.write_phony_rules()

        # We write a gitignore file so we can use git-clean to remove build
        # artifacts that we no-longer produce:
        ninja.write_gitignore()


def multistrap(config_file, ninja, apt):
    p = SafeConfigParser()
    p.read(config_file)
    
    def get(section, field, default=None):
        try:
            return p.get(section, field)
        except NoOptionError:
            return default

    section = p.get("General", "aptsources").split()[0]
    
    apt_source = AptSource(
        architecture=get("General", "arch"),
        distribution=get(section, "suite"),
        archive_url=get(section, "source"),
        components=get(section, "components"))

    image = apt.build_image(
        "%s.lock" % config_file,
        packages=get(section, "packages", "").split(),
        apt_source=apt_source)
    ninja.default(image.filename)
    return image

if __name__ == '__main__':
    sys.exit(main(sys.argv))
