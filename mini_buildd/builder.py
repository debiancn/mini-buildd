# -*- coding: utf-8 -*-
import os
import time
import datetime
import shutil
import re
import subprocess
import logging

import mini_buildd.setup
import mini_buildd.misc
import mini_buildd.changes

LOG = logging.getLogger(__name__)


class Status(object):
    "... todo:: Some of these methods require locking(?)"

    def __init__(self, max_builds):
        self._building = {}
        self._pending = {}
        self._max_builds = max_builds

    def building(self):
        return self._building

    def load(self):
        return float(len(self._building)) / self._max_builds

    def start(self, key):
        self._building[key] = (time.time(), 0)

    def build(self, key):
        start, _build = self._building[key]
        del self._building[key]
        self._pending[key] = (start, time.time())

    def done(self, key):
        del self._pending[key]

    @property
    def tpl(self):
        return {
            "building": self._building,
            "pending": self._pending,
            "max_builds": self._max_builds,
            "load": self.load()}


def buildlog_to_buildresult(file_name, bres):
    regex = re.compile("^[a-zA-Z0-9-]+: [^ ]+$")
    with open(file_name) as f:
        for l in f:
            if regex.match(l):
                LOG.debug("Build log line detected as build status: {l}".format(l=l.strip()))
                s = l.split(":")
                bres["Sbuild-" + s[0]] = s[1].strip()


def build_clean(breq):

    if "build" in mini_buildd.setup.DEBUG:
        LOG.warn("Build DEBUG mode -- not removing build spool dir {d}".format(d=breq.get_spool_dir()))
    else:
        shutil.rmtree(breq.get_spool_dir())
    breq.remove()


def generate_sbuildrc(path, breq):
    " Generate .sbuildrc for a build request (not all is configurable via switches, unfortunately)."

    with open(os.path.join(path, ".sbuildrc"), 'w') as f:
        # Automatic part part
        f.write("""\
# We update sources.list on the fly via chroot-setup commands;
# this update occurs before, so we don't need it.
$apt_update = 0;

# Allow unauthenticated apt toggle
$apt_allow_unauthenticated = {apt_allow_unauthenticated};

# Builder identity
$pgp_options = ['-us', '-k Mini-Buildd Automatic Signing Key'];
""".format(apt_allow_unauthenticated=breq["Apt-Allow-Unauthenticated"]))
        # Copy the custom snippet
        shutil.copyfileobj(open(os.path.join(path, "sbuildrc_snippet"), 'rb'), f)
        f.write("""
# don't remove this, Perl needs it:
1;
""")


def build(breq, gnupg, jobs, status):
    """
    .. todo:: Builder

       - Upload "internal error" result on exception to requesting mini-buildd.
       - DEB_BUILD_OPTIONS
       - [.sbuildrc] gpg setup
       - schroot bug: chroot-setup-command: uses sudo workaround
       - sbuild bug: long option '--jobs=N' does not work though advertised in man page (using '-jN')
    """
    mini_buildd.misc.sbuild_keys_workaround()

    pkg_info = "{s}-{v}:{a}".format(s=breq["Source"], v=breq["Version"], a=breq["Architecture"])
    status.start(pkg_info)

    build_dir = breq.get_spool_dir()

    bres = mini_buildd.changes.Changes(
        os.path.join(build_dir,
                     "{s}_{v}_mini-buildd-buildresult_{a}.changes".
                     format(s=breq["Source"], v=breq["Version"], a=breq["Architecture"])))

    if bres.is_new():
        try:
            breq.untar(path=build_dir)

            generate_sbuildrc(build_dir, breq)

            sbuild_cmd = ["sbuild",
                          "-j{0}".format(jobs),
                          "--dist={0}".format(breq["Distribution"]),
                          "--arch={0}".format(breq["Architecture"]),
                          "--chroot=mini-buildd-{d}-{a}".format(d=breq["Base-Distribution"], a=breq["Architecture"]),
                          "--chroot-setup-command=sudo cp {p}/apt_sources.list /etc/apt/sources.list".format(p=build_dir),
                          "--chroot-setup-command=sudo cp {p}/apt_preferences /etc/apt/preferences".format(p=build_dir),
                          "--chroot-setup-command=sudo apt-key add {p}/apt_keys".format(p=build_dir),
                          "--chroot-setup-command=sudo apt-get update",
                          "--chroot-setup-command=sudo {p}/chroot_setup_script".format(p=build_dir),
                          "--build-dep-resolver={r}".format(r=breq["Build-Dep-Resolver"]),
                          "--nolog", "--log-external-command-output", "--log-external-command-error"]

            if "Arch-All" in breq:
                sbuild_cmd.append("--arch-all")
                sbuild_cmd.append("--source")
                sbuild_cmd.append("--debbuildopt=-sa")

            if "Run-Lintian" in breq:
                sbuild_cmd.append("--run-lintian")
                sbuild_cmd.append("--lintian-opts=--suppress-tags=bad-distribution-in-changes-file")
                sbuild_cmd.append("--lintian-opts={o}".format(o=breq["Run-Lintian"]))

            if "sbuild" in mini_buildd.setup.DEBUG:
                sbuild_cmd.append("--verbose")
                sbuild_cmd.append("--debug")

            sbuild_cmd.append("{s}_{v}.dsc".format(s=breq["Source"], v=breq["Version"]))

            buildlog = os.path.join(build_dir, "{s}_{v}_{a}.buildlog".format(s=breq["Source"], v=breq["Version"], a=breq["Architecture"]))
            LOG.info("{p}: Running sbuild: {c}".format(p=pkg_info, c=sbuild_cmd))
            with open(buildlog, "w") as l:
                retval = subprocess.call(sbuild_cmd,
                                         cwd=build_dir,
                                         env=mini_buildd.misc.taint_env({"HOME": build_dir}),
                                         stdout=l, stderr=subprocess.STDOUT)

            for v in ["Distribution", "Source", "Version", "Architecture"]:
                bres[v] = breq[v]

            # Add build results to build request object
            bres["Sbuildretval"] = str(retval)
            buildlog_to_buildresult(buildlog, bres)

            LOG.info("{p}: Sbuild finished: Sbuildretval={r}, Status={s}".format(p=pkg_info, r=retval, s=bres["Sbuild-Status"]))
            bres.add_file(buildlog)
            build_changes_file = os.path.join(build_dir,
                                              "{s}_{v}_{a}.changes".
                                              format(s=breq["Source"], v=breq["Version"], a=breq["Architecture"]))
            if os.path.exists(build_changes_file):
                build_changes = mini_buildd.changes.Changes(build_changes_file)
                build_changes.tar(tar_path=bres.file_path + ".tar")
                bres.add_file(bres.file_path + ".tar")

            bres.save(gnupg)
        except Exception as e:
            LOG.exception("Build internal error: {e}".format(e=str(e)))
            build_clean(breq)
            # todo: internal_error.upload(...)
            return
    else:
        LOG.info("Re-using existing buildresult: {b}".format(b=breq.file_name))

    status.build(pkg_info)

    # Finally, try to upload to requesting mini-buildd; if the
    # upload fails, we keep all data and try later.
    try:
        bres.upload(mini_buildd.misc.HoPo(breq["Upload-Result-To"]))
        build_clean(breq)
        status.done(pkg_info)
    except Exception as e:
        LOG.error("Upload failed (trying later): {e}".format(e=str(e)))


def run(queue, gnupg, status, build_queue_size, sbuild_jobs):
    threads = []

    def threads_cleanup():
        rm = []
        for t in threads:
            if not t.is_alive():
                t.join()
                rm.append(t)
        for t in rm:
            threads.remove(t)

    while True:
        event = queue.get()
        if event == "SHUTDOWN":
            break

        try:
            LOG.info("Builder status: {0} active builds, {0} waiting in queue.".format(0, queue.qsize()))

            while len(status.building()) >= build_queue_size:
                LOG.info("Max ({b}) builds running, waiting for a free builder slot...".format(b=len(status.building())))
                time.sleep(15)

            threads.append(mini_buildd.misc.run_as_thread(build, breq=mini_buildd.changes.Changes(event), gnupg=gnupg, jobs=sbuild_jobs, status=status))
            threads_cleanup()

        except Exception as e:
            LOG.error("Unexpected error in builder loop: {e}".format(e=str(e)))
            if mini_buildd.setup.DEBUG is not None and "main" in mini_buildd.setup.DEBUG:
                LOG.exception("DEBUG: Builder loop exception")
        finally:
            queue.task_done()

    for t in threads:
        t.join()
