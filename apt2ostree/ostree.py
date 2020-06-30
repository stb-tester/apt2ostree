from collections import namedtuple

from .ninja import Rule


class OstreeRef(namedtuple("OstreeImage", "filename")):
    @property
    def ref(self):
        return self.filename.split("/refs/heads/")[1]

    @property
    def repo(self):
        return self.filename.split("/refs/heads/")[0]


ostree = Rule("ostree", """\
    mkdir $ostree_repo;
    ostree init --repo=$ostree_repo --mode=bare-user;
    """, outputs=['$ostree_repo/config'], restat=True)


ostree_combine = Rule(
    "ostree_combine", """\
        echo $in
         | sed 's,$ostree_repo/refs/heads/,--tree=ref=,g'
         | xargs -xr ostree --repo=$ostree_repo commit -b $branch --no-bindings
                            --orphan --timestamp=0;
        [ -e $out ]""",
    restat=True,
    output_type=OstreeRef,
    outputs=["$ostree_repo/refs/heads/$branch"],
    order_only=["$ostree_repo/config"],
    description="Ostree Combine for $branch")

ostree_addfile = Rule(
    "file_into_ostree", """\
    set -ex;
    tmpdir=$$(mktemp -dt ostree_adddir.XXXXXX);
    cp $in_file $$tmpdir;
    ostree --repo=$ostree_repo commit --devino-canonical -b $out_branch
           --no-bindings --orphan --timestamp=0
           --tree=ref=$in_branch
           --tree=prefix=$prefix --tree=dir=$$tmpdir
           --owner-uid=0 --owner-gid=0;
    rm -rf $$tmpdir;
    """,
    restat=True,
    inputs=["$ostree_repo/refs/heads/$in_branch", "$in_file"],
    output_type=OstreeRef,
    outputs=["$ostree_repo/refs/heads/$out_branch"],
    description="Add file $in_branch")
