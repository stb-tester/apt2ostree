#!/usr/bin/python3

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager

import requests
from bs4 import BeautifulSoup


def main(argv):
    parser = argparse.ArgumentParser()
    parser.parse_args(argv[1:])

    for x in ["xenial", "bionic-updates", "eoan", "focal", "jammy"]:
        print(x)
        get_keyring_package(
            "ubuntu", x,
            "https://packages.ubuntu.com/%s/all/ubuntu-keyring/download" % x)

    for x in ["buster", "bullseye", "sid"]:
        print(x)
        get_keyring_package(
            "debian", x,
            "https://packages.debian.org/%s/all/debian-archive-keyring/download"
            % x)


def get_keyring_package(distro, release, package_download_url):
    print(package_download_url)
    r = requests.get(package_download_url)
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        sys.stderr.write("Unavailable: %s\n" % (e))
        return

    soup = BeautifulSoup(r.text, 'html.parser')

    sha256 = find_sha(soup)
    dl = find_dl_link(soup)

    r = requests.get(dl)
    r.raise_for_status()

    if hashlib.sha256(r.content).hexdigest() != sha256:
        raise Exception("SHAs don't match!")

    with named_temporary_directory(prefix="apt2ostree-update_keyrings-") as tmp:
        with open("%s/keyring.deb" % tmp, "wb") as f:
            f.write(r.content)
        subprocess.check_call(
            ["dpkg-deb", '-x', 'keyring.deb', '.'], cwd=tmp)
        sys.stderr.write("%s\n" % tmp)
        os.makedirs(_find_file(distro), exist_ok=True)
        try:
            os.rename("%s/etc/apt/trusted.gpg.d" % tmp,
                      _find_file("%s/%s" % (distro, release)))
        except IOError:
            sys.stderr.write(
                "Couldn't find keyrings for %s/%s\n" % (distro, release))


def find_sha(soup):
    for h in soup.find_all("th"):
        if h.string == "SHA256 checksum":
            sha256 = next(iter(h.find_next_siblings("td"))).string
            assert re.match("^[0-9a-f]{64}$", sha256), \
                "%r is not a SHA" % (sha256,)
            return sha256
    raise Exception("No SHA256 in %s" % soup)


def find_dl_link(soup):
    for a in soup.find_all("a"):
        if a["href"].startswith("http://dk.archive.ubuntu.com/ubuntu/pool") or \
                a["href"].startswith("http://ftp.de.debian.org"):
            return a['href']
    raise Exception("No download link")


def _find_file(filename, this_dir=os.path.dirname(os.path.abspath(__file__))):
    return os.path.join(this_dir, filename)


@contextmanager
def named_temporary_directory(
        suffix='', prefix='tmp', dir=None):  # pylint: disable=W0622
    dirname = tempfile.mkdtemp(suffix, prefix, dir)
    try:
        yield dirname
    finally:
        shutil.rmtree(dirname, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
