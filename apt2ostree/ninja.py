import errno
import os
import pipes
import re
import sys
import textwrap

import ninja_syntax

NINJA_AUTO_VARS = set(["in", "out"])


class Ninja(ninja_syntax.Writer):
    builddir = "_build"
    ninjafile = "build.ninja"

    def __init__(self, regenerate_command=None, width=78):
        if regenerate_command is None:
            regenerate_command = sys.argv

        output = open(self.ninjafile + '~', 'w')
        super(Ninja, self).__init__(output, width)
        self.global_vars = {}
        self.targets = set()
        self.rules = {}
        self.generator_deps = set()

        self.add_generator_dep(os.path.relpath(__file__))
        self.add_generator_dep(os.path.relpath(ninja_syntax.__file__))
        self.add_generator_dep(os.path.relpath(__file__ + '/../ostree.py'))

        self.regenerate_command = regenerate_command
        self.variable("builddir", self.builddir)
        self.build(".FORCE", "phony")

        # Write a reconfigure script to rememeber arguments passed to configure:
        reconfigure = "%s/reconfigure" % self.builddir
        self.add_target(reconfigure)
        try:
            os.mkdir(self.builddir)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
        with open(reconfigure, 'w') as f:
            f.write("#!/bin/sh\nexec %s\n" % (
                shquote(["./" + os.path.relpath(self.regenerate_command[0])] +
                        self.regenerate_command[1:])))
        os.chmod(reconfigure, 0755)
        self.rule("configure", "$builddir/reconfigure", generator=True)

        self.add_target("%s/.ninja_deps" % self.builddir)
        self.add_target("%s/.ninja_log" % self.builddir)

    def close(self):
        if not self.output.closed:
            self.build(self.ninjafile, "configure", list(self.generator_deps))
            super(Ninja, self).close()
            os.rename(self.ninjafile + '~', self.ninjafile)

    def __enter__(self):
        return self

    def __exit__(self, _1, _2, _3):
        self.close()

    def __del__(self):
        self.close()

    def variable(self, key, value, indent=0):
        if indent == 0:
            if key in self.global_vars:
                if value != self.global_vars[key]:
                    raise RuntimeError(
                        "Setting key to %s, when it was already set to %s" % (
                            key, value))
                return
            self.global_vars[key] = value
        super(Ninja, self).variable(key, value, indent)

    def build(self, outputs, rule, *args, **kwargs):
        for x in ninja_syntax.as_list(outputs):
            self.add_target(x)
        return super(Ninja, self).build(outputs, rule, *args, **kwargs)

    def rule(self, name, *args, **kwargs):
        if name in self.rules:
            assert self.rules[name] == (args, kwargs)
        else:
            self.rules[name] = (args, kwargs)
            return super(Ninja, self).rule(name, *args, **kwargs)

    def open(self, filename, mode='r', *args, **kwargs):
        if 'w' in mode:
            self.add_target(filename)
        if 'r' in mode:
            try:
                out = open(filename, mode, **kwargs)
                self.add_generator_dep(filename)
                return out
            except IOError as e:
                if e.errno == errno.ENOENT:
                    # configure output depends on the existance of this file.
                    # It doesn't exist right now but we'll want to rerun
                    # configure if that changes.  The mtime of the containing
                    # directory will be updated when the file is created so we
                    # add a dependency on that instead:
                    self.add_generator_dep(os.path.dirname(filename) or '.')
                    raise
                else:
                    raise
        else:
            return open(filename, mode, **kwargs)

    def add_generator_dep(self, filename):
        """Cause configure to be rerun if changes are made to filename"""
        self.generator_deps.add(filename.replace('.pyc', '.py'))

    def add_target(self, target):
        if not target:
            raise RuntimeError("Invalid target filename %r" % target)
        self.targets.add(target)

    def write_gitignore(self, filename=None):
        if filename is None:
            filename = "%s/.gitignore" % self.builddir
        self.add_target(filename)
        with open(filename, 'w') as f:
            for x in self.targets:
                f.write("%s\n" % os.path.relpath(x, os.path.dirname(filename)))


def vars_in(items):
    if items is None:
        return set()
    if isinstance(items, (str, unicode)):
        items = [items]
    out = set()
    for text in items:
        for x in text.split('$$'):
            out.update(re.findall(r"\$(\w+)", x))
            out.update(re.findall(r"\${(\w+)}", x))
    return out


class Rule(object):
    def __init__(self, name, command, outputs=None, inputs=None,
                 description=None, order_only=None, implicit=None, **kwargs):
        if order_only is None:
            order_only = []
        if implicit is None:
            implicit = []
        self.name = name
        self.command = textwrap.dedent(command)
        self.outputs = outputs
        self.inputs = inputs
        self.order_only = order_only
        self.implicit = implicit
        self.kwargs = kwargs

        self.vars = vars_in(command).union(vars_in(inputs)).union(vars_in(outputs))

        if description is None:
            description = "%s(%s)" % (self.name, ", ".join(
                "%s=$%s" % (x, x) for x in self.vars))
        self.description = description

    def build(self, ninja, outputs=None, inputs=None, implicit=None,
              order_only=None, implicit_outputs=None, pool=None, **kwargs):
        if outputs is None:
            outputs = []
        if inputs is None:
            inputs = []
        if order_only is None:
            order_only = []
        if implicit is None:
            implicit = []
        ninja.newline()
        ninja.rule(self.name, self.command, description=self.description,
                   **self.kwargs)
        v = set(kwargs.keys())
        missing_args = self.vars - v - set(ninja.global_vars.keys()) - NINJA_AUTO_VARS
        if missing_args:
            raise TypeError("Missing arguments to rule %s: %s" %
                            (self.name, ", ".join(missing_args)))
        if v - self.vars:
            raise TypeError("Rule %s got unexpected arguments: %s" %
                            (self.name, ", ".join(v - self.vars)))
        if self.outputs:
            outputs.extend(ninja_syntax.expand(x, ninja.global_vars, kwargs)
                           for x in self.outputs)
        if self.inputs:
            inputs.extend(ninja_syntax.expand(x, ninja.global_vars, kwargs)
                          for x in self.inputs)
        if self.implicit:
            inputs.extend(ninja_syntax.expand(x, ninja.global_vars, kwargs)
                          for x in self.implicit)

        ninja.newline()
        return ninja.build(
            outputs, self.name, inputs=inputs,
            implicit=implicit, order_only=self.order_only + order_only,
            implicit_outputs=implicit_outputs, pool=pool, variables=kwargs)


def shquote(v):
    if isinstance(v, (unicode, str)):
        return pipes.quote(v)
    else:
        return " ".join(shquote(x) for x in v)
