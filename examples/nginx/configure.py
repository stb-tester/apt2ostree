#!/usr/bin/python

import argparse
import os
import sys

# Needed so we don't need to add apt2ostree to PYTHONPATH for this example to
# work:
sys.path.append(os.path.dirname(__file__) + '/../..')
from apt2ostree import Apt, Ninja, ubuntu_apt_sources


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--ostree-repo", default="_build/ostree")
    args = parser.parse_args(argv[1:])

    # Ninja() will write a build.ninja file:
    with Ninja(argv) as ninja:
        # Rebuild build.ninja if this file changes:
        ninja.add_generator_dep(__file__)

        # Ostree repo where the images will be written:
        ninja.variable("ostree_repo", args.ostree_repo)

        apt = Apt(ninja)

        # Build an image containing the package nginx-core and dependencies.
        # The ref will be deb/images/Packages.lock/configured.  A lockfile will
        # be written to `Packages.lock`.  This can be updated with
        # `ninja update-lockfile-Packages.lock`.
        image = apt.build_image("Packages.lock", ['nginx-core'],
                                ubuntu_apt_sources("xenial"))

        # If run `ninja` without specifying a target our image will be built:
        ninja.default(image.filename)

        # Write a rule `update-apt-lockfiles` to update all the lockfiles (as it
        # is we only have the one).
        apt.write_phony_rules()

        # We write a gitignore file so we can use git-clean to remove build
        # artifacts that we no-longer produce:
        ninja.write_gitignore()


if __name__ == '__main__':
    sys.exit(main(sys.argv))
