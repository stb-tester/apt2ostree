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
    """, outputs=['$ostree_repo/config'])


ostree_combine = Rule(
    "ostree_combine", """\
        echo $in
         | sed 's,$ostree_repo/refs/heads/,--tree=ref=,g'
         | xargs ostree --repo=$ostree_repo commit -b $branch --no-bindings
                        --orphan --timestamp=0;""",
    outputs=["$ostree_repo/refs/heads/$branch"],
    order_only=["$ostree_repo/config"],
    description="Ostree Combine for $branch")
