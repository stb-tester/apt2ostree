apt2ostree
==========

Homepage: http://github.com/stb-tester/apt2ostree

apt2ostree is used for building Debian/Ubuntu based ostree images.  It performs
the same task as debootstrap/multistrap but the output is an ostree tree rather
than a rootfs in a directory.

Unlike other similar tools it's fast, reproducible, space-efficient, doesn't
depend on apt/dpkg being installed on the host, is well suited to building
multiple similar but different images and it allows you to manage package
updates as you do with your source-code.

Features
========

* Reproducibility
    * From a list of packages we perform dependency resolution and save the
      complete list of all packages, their versions and their SHAs and commit
      that to git.  Builds from this description are functionally reproducible.
* Speed - apt2ostree is fast becuase:
    * We only download and extracts any given deb once.  If that deb is used in
      multiple images it doesn't need to be extracted again.  This saves disk
      space too because the contents of the debs are committed to ostree so they
      will share disk-space with the built images.
    * Builds happen in parallel - this falls out of using ninja we can be
      downloading one deb at the same time as compiling a second image, or
      performing other build tasks within your build-system.
    * We don't repeat work that we've already done between builds - another
      benefit of using ninja.
    * Combining the contents of the debs is fast because it only touches ostree
      metadata - it doesn't need to read the contents of the files (see also
      [ostreedev/ostree#1643](https://github.com/ostreedev/ostree/pull/1643)).

Lockfiles
=========

We store all our source in git. Reproducibility is an important requirement for
us - when doing a build from the same source we want to end up with an image
with the same packages and versions of packages installed no matter what machine
we run it on or whether we run it sooner rather than later.  This is complicated
with apt because it's more geared to keeping a traditional system up-to-date and
the apt-mirrors don't keep the old packages in their indices.

To fix this we took a leaf from modern programming language package managers.
We use the lockfile concept as used by rust's cargo package manager
([cargo.lock][1]) or nodejs's npm ([package-lock.json][2]).  The idea is that
you have two files, one that is written by hand and lists the packages you want
to install, and a second one generated from the first that lists all the
versions of the packages and all their transitive dependencies.  This second
file is the lockfile.

The key is that you check both files into your git repository.  The lockfile is
a complete description of the packages you want to be installed on the target
system. This determinism has a few advantages:

1. You can go back to a particular revision in the past and build a functionally
   identical image.
2. Updates to the lockfiles are recorded in git so we can diff between source
   revisions to investigate any changes in behavior seen.
3. Security updates are now recorded in your git history and can be managed and
   deployed explicitly.

Updating lockfiles
------------------

We have a CI job that runs every night updating the lockfiles - this is the
equivalent of an apt-get update.  The CI command looks like:

    git checkout -b update-lockfiles
    ninja update-lockfiles
    git commit -a -m "Updated lockfiles as of $(date --iso-8601)"
    git push origin updated-lockfiles

This kicks off builds and in the morning we can see exactly what packages
changed and we have a fresh build with CI passing or failing so we have
confidence that the image still works after applying the security updates.  We
can then choose to roll it out to our devices in the field.

It turns out that the lockfile is a kind of snapshot of the package metadata
from the debian mirrors filtered by the top-level list of packages you want
installed - and we implement it in exactly this way.  The format of the lockfile
is a debian Package index[3] as used by apt.  This has a number of benefits over
a plain list of package names and versions:

1. It contains MD5, SHA1 and SHA256 fields so we can be certain we're using
   exactly the package we want to be. This is nice and secure without having to
   faf around with gpg.
2. It (indirectly) contains the URL of the package so we can implement the
   downloading of the packages external to the chroots where they will be
   installed.

Example
=======

See and example project under `examples/nginx` involving building an image
containing nginx and its dependencies.  The list of packages is defined at the
top of `configure.py`.

Usage:

    cd examples/nginx

    # Create an ostree to build images into:
    mkdir -p _build/ostree
    ostree init --mode=bare-user --repo=_build/ostree

    # Creates build.ninja
    ./configure.py

    # Build image with ref deb/images/Packages.lock/configured
    ninja

Update the lockfile with:

    # Run this to update the lockfile (equivalent to apt update):
    ninja update-lockfiles

Make the rootfs a part of a normal ostree branch with history, etc.:

    ostree commit --tree=ref=deb/images/Packages.lock/configured \
        -b mybranch -s "My message"

Usage
=====

`apt2ostree` is a Python library that helps write ninja build files.  It is
intended to be used by a `configure` script written in Python.  This is a very
flexible albeit unconventional approach.  See the`examples/` directory for
examples.  Currently Python API documentation is a little lacking.

If you don't want to use it as a library you can create a `multistrap` - style
configuration file and use our `multistrap` example under `examples/multistrap`.
See the comments at the top of the file for usage.

Dependencies
============

* [ostree](https://ostree.readthedocs.io/)
* [aptly](https://www.aptly.info/) - You'll need a patched version of this
  adding lockfiles.  Fortunately aptly is quick and easy to build.  See
  https://github.com/stb-tester/aptly/tree/lockfile.  To build run:

        $ mkdir -p $GOPATH/src/github.com/stb-tester/aptly
        $ git clone https://github.com/stb-tester/aptly $GOPATH/src/github.com/stb-tester/aptly
        $ cd $GOPATH/src/github.com/stb-tester/aptly
        $ make install

  and add `$GOPATH/bin` to your `$PATH`
* [ninja](https://ninja-build.org/) build tool.
* [Python](https://www.python.org/) - This project was built against Python 2.
  Patches to make it also work with Python 3 would be greatfully accepted.

Second stage building also requires:

* [bubblewrap](https://github.com/projectatomic/bubblewrap) sandboxing and
  chroot tool.

Second stage building
=====================

Much like `multistrap` there are two stages:

1. The debs are unpacked.  This will be stored in ostree under ref
   `deb/$lockfile_name/unpacked`.
2. The unpacked images is checked out and `dpkg --configure -a` is run within
   a chroot before checking the results back in again.

Building stage 1 is fast and is currently the primary focus of this tool.
`apt2ostree` contains a naive implementation of stage 2 should be reliable, but
has various issues:

* It requires superuser privileges - we use `sudo` to check the files out as
  root. A production implementation might prefer to run this using `fakeroot` or
  user-namespaces.
* It's slow - we check out all the files from ostree by copying rather than
  hard-linking. An optimised implmentation might prefer to use `rofiles-fuse`
  or `overlayfs` to protect the links from modification and `fakeroot` to get
  the permissions/ownership right.
* It's slow - we check all the files back into ostree by piping through tar
  back into ostree. This allows tar to be running as root, while ostree still
  runs as a normal user. If `ostree checkout --require-hardlinks` then we could
  use `ostree commit --link-checkout-speedup` during checking to speed things
  up.  Further speedups might be possible with `overlayfs`.
* I've not tested it building for foreign-architectures with qemu binfmt-misc
  support.  It might work, it might not.

All this is a long-winded way of saying that much like with multistrap you
should implement your own stage 2 where you call `dpkg --configure -a`.

Integration within build-systems
================================

apt2ostree is intended for use within a larger build-system. Typically you'll
want to install additional packages into the rootfs, make custom modifications
or add rules for publishing the built images. There are different ways of doing
this:

* Use `apt2sotree` as a standalone tool embedding the shell script:

        ./configure.py
        ninja

  within your buildsystem.  This is the simplest integration, but will miss out
  on the fine-grained concurrency and notification of whether the images were
  rebuilt or not.

* Use `apt2ostree` to generate a `build.ninja` and then use the ninja
  [`subninja` keyword](https://ninja-build.org/manual.html#ref_scope) to include
  the build rules.  Depending on `ostree/refs/heads/deb/xxx/configured` will
  then cause the various images to be built.

* Extend `configure.py` to add all the other build rules you have. This may be
  simpler if you don't already have a build system you're integrating with.

Comparison to related tools
===========================

debootstrap/multistrap
----------------------

debootstrap and multistrap can both be used to create rootfses that can later
be committed to ostree.  debootstrap is used as part of the official debian
installer, multistrap is more targetted toward creating rootfses for embedded
systems.

Similar to multistrap apt2ostree was designed for building embedded systems
to be booted on a seperate machine to the build host.

For dependency resolution during package selection apt2ostree uses `aptly`
rather than `apt`/`dpkg`.  This makes deployment easier because aptly is a
single statically linked go binary.  The dependency resolution may not be as
robust as `multistrap`.

Unlike multistrap, but like debootstrap apt2ostree doesn't require apt/dpkg to
be installed on the build host.

apt2ostree is faster - particuarly in the case where you're building multiple
variants of images or building an updated image because upstream packages have
been updated.

apt2ostree uses less disk space because it doesn't cache downloaded debs - it
commits them directly to ostree after downloading.  The disk space used will
be shared with the built images.

apt2ostree doesn't currently support generating lockfiles with packages from
multiple repositories.  This means you can't build an image that pulls from both
trusty and trusty-updates.  This is a major missing feature that is a high
priority.

Similar to `multistrap` and to some extent `debootstrap` apt2ostree is generally
used as part of a larger.

Unlike `debootstrap` (and `multistrap`?) apt2ostree is not officially supported
nor is it affiliated with either the Debian or Ubuntu projects.

`apt2ostree` contains a multistrap configuration compatibility script so you
can use your existing multistrap configuration files.  See
`examples/multistrap/multistrap.py` for more information.

`debootstrap` and `multistrap` don't use lockfiles, you get whatever versions
of the packages that are available at the time you ran the tool.  To update you
must rerun the tool and see what the difference is in the committed image, so
you don't have a record of package versions in source control.

endless-ostree-builder (EOB)
----------------------------

See https://github.com/dbnicholson/deb-ostree-builder .

**Disclaimer: I've not used EOB**, so the following are educated guesses based
on available documentation and source.  Please correct any mistakes or
misunderstandings by editing this and opening a pull-request.

Both apt2ostree and EOB were written to create deb-based ostree images.  Unlike
apt2ostree EOB uses `debootstrap` to create a rootfs on disk and then checks
that rootfs into `ostree`.

Both systems were originally built into a company's private build-system, and
has since been separated from that and published publically. These companies are
[Endless](https://endlessos.com/) and [stb-tester.com](https://stb-tester.com).

apt2ostree is narrower in scope than EOB.  It doesn't handle publishing,
device-tree files, among other features.

apt2ostree is likely much faster than EOB with its incremental building.

apt2ostree is intended to be used as a python library within a larger
build-system - more like debootstrap. It seems that EOB is designed to be a
larger system complete with hooks.  It is customisable with hooks and
configuration files.  It may be possible to replace EOB's use of `debootstrap`
with `apt2ostree`.

EOB doesn't use lockfiles.  You get whatever versions of the packages that are
available at the time you ran the tool.  To update you must rerun the tool and
see what the difference is in the committed image, so you don't have a record
of package versions in source control.

History
=======

apt2ostree was started a [stb-tester.com](https://stb-tester.com) as a way of
building images for their stb-tester HDMI product.  Our approach of using ninja
and creating intermediate build images came up in a discussion on the ostree
mailing list which motivated @wmanley to tidy-up and publish what we've built.

ostree mailing list posts threads here:

* https://mail.gnome.org/archives/ostree-list/2018-October/msg00005.html
* https://mail.gnome.org/archives/ostree-list/2018-November/msg00000.html
