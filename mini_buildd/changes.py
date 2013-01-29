# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import stat
import tempfile
import shutil
import glob
import logging
import tarfile
import socket
import ftplib
import re
import contextlib

import debian.deb822

import mini_buildd.setup
import mini_buildd.misc
import mini_buildd.gnupg

import mini_buildd.models.repository
import mini_buildd.models.gnupg

LOG = logging.getLogger(__name__)


class Changes(debian.deb822.Changes):
    # Extra mini-buildd changes file types we invent
    TYPE_DEFAULT = 0
    TYPE_BREQ = 1
    TYPE_BRES = 2
    TYPES = {TYPE_DEFAULT: "",
             TYPE_BREQ: "_mini-buildd-buildrequest",
             TYPE_BRES: "_mini-buildd-buildresult"}

    BUILDREQUEST_RE = re.compile("^.+" + TYPES[TYPE_BREQ] + "_[^_]+.changes$")
    BUILDRESULT_RE = re.compile("^.+" + TYPES[TYPE_BRES] + "_[^_]+.changes$")

    def __init__(self, file_path):
        self._file_path = file_path
        self._file_name = os.path.basename(file_path)
        self._new = not os.path.exists(file_path)
        self._sha1 = None if self._new else mini_buildd.misc.sha1_of_file(file_path)

        if self.BUILDREQUEST_RE.match(self._file_name):
            self._type = self.TYPE_BREQ
        elif self.BUILDRESULT_RE.match(self._file_name):
            self._type = self.TYPE_BRES
        else:
            self._type = self.TYPE_DEFAULT

        super(Changes, self).__init__([] if self._new else file(file_path))
        # Be sure base dir is always available
        mini_buildd.misc.mkdirs(os.path.dirname(file_path))

        # This is just for stat/display purposes
        self.remote_http_url = None

    def __unicode__(self):
        if self.type == self.TYPE_BREQ:
            return "Buildrequest from '{h}': {i}".format(h=self.get("Upload-Result-To"), i=self.get_pkg_id(with_arch=True))
        elif self.type == self.TYPE_BRES:
            return "Buildresult from '{h}': {i}".format(h=self.get("Built-By"), i=self.get_pkg_id(with_arch=True))
        else:
            return "User upload: {i}".format(i=self.get_pkg_id())

    @property
    def type(self):
        return self._type

    @classmethod
    def strip_epoch(cls, version):
        return version.rpartition(":")[2]

    @classmethod
    def gen_changes_file_name(cls, package, version, arch, mbd_type=TYPE_DEFAULT):
        """
        Gen any changes file name.

        Always strip epoch from version, and handle special
        mini-buildd types.

        >>> Changes.gen_changes_file_name("mypkg", "1.2.3-1", "mips")
        u'mypkg_1.2.3-1_mips.changes'
        >>> Changes.gen_changes_file_name("mypkg", "7:1.2.3-1", "mips")
        u'mypkg_1.2.3-1_mips.changes'
        >>> Changes.gen_changes_file_name("mypkg", "7:1.2.3-1", "mips", mbd_type=Changes.TYPE_BREQ)
        u'mypkg_1.2.3-1_mini-buildd-buildrequest_mips.changes'
        >>> Changes.gen_changes_file_name("mypkg", "7:1.2.3-1", "mips", mbd_type=Changes.TYPE_BRES)
        u'mypkg_1.2.3-1_mini-buildd-buildresult_mips.changes'
        """
        return "{p}_{v}{x}_{a}.changes".format(p=package,
                                               v=cls.strip_epoch(version),
                                               a=arch,
                                               x=cls.TYPES[mbd_type])

    def gen_file_name(self, arch, mbd_type):
        return self.gen_changes_file_name(self["Source"], self["Version"], arch, mbd_type)

    @property
    def dsc_name(self):
        return "{s}_{v}.dsc".format(s=self["Source"],
                                    v=self.strip_epoch(self["Version"]))

    @property
    def bres_stat(self):
        return "Build={b}, Lintian={l}".format(b=self.get("Sbuild-Status"), l=self.get("Sbuild-Lintian"))

    @property
    def file_name(self):
        return self._file_name

    @property
    def file_path(self):
        return self._file_path

    @property
    def buildlog_name(self):
        return "{s}_{v}_{a}.buildlog".format(s=self["Source"], v=self["Version"], a=self["Architecture"])

    def get_archive_dir(self, installed):
        " Archive path for this changes file: REPOID/[_failed]/PACKAGE/VERSION/ARCH "
        return os.path.join(mini_buildd.misc.Distribution(self["Distribution"]).repository,
                            "" if installed else "_failed",
                            self["Source"],
                            self["Version"],
                            self["Architecture"])

    def is_new(self):
        return self._new

    @property
    def magic_auto_backports(self):
        mres = re.search(r"\*\s*MINI_BUILDD:\s*AUTO_BACKPORTS:\s*([^*.\[\]]+)", self.get("Changes", ""))
        return (re.sub(r'\s+', '', mres.group(1))).split(",") if mres else []

    @property
    def magic_backport_mode(self):
        return bool(re.search(r"\*\s*MINI_BUILDD:\s*BACKPORT_MODE", self.get("Changes", "")))

    def get_spool_dir(self):
        return os.path.join(mini_buildd.setup.SPOOL_DIR, self._sha1)

    def get_pkg_id(self, with_arch=False, arch_separator=":"):
        pkg_id = "{s}_{v}".format(s=self["Source"], v=self["Version"])
        if with_arch:
            pkg_id += "{s}{a}".format(s=arch_separator, a=self["Architecture"])
        return pkg_id

    def get_files(self, key=None):
        return [f[key] if key else f for f in self.get("Files", [])]

    def add_file(self, file_name):
        self.setdefault("Files", [])
        self["Files"].append({"md5sum": mini_buildd.misc.md5_of_file(file_name),
                              "size": os.path.getsize(file_name),
                              "section": "mini-buildd",
                              "priority": "extra",
                              "name": os.path.basename(file_name)})

    def save(self, gnupg):
        try:
            LOG.info("Saving changes: {f}".format(f=self._file_path))
            self.dump(fd=open(self._file_path, "w+"))
            LOG.info("Signing changes: {f}".format(f=self._file_path))
            gnupg.sign(self._file_path)
            self._sha1 = mini_buildd.misc.sha1_of_file(self._file_path)
        except:
            # Existence of the file name is used as flag
            if os.path.exists(self._file_path):
                os.remove(self._file_path)
            raise

    def upload(self, hopo):
        upload = os.path.splitext(self._file_path)[0] + ".upload"
        if os.path.exists(upload):
            LOG.info("FTP: '{f}' already uploaded to '{h}'...".format(f=self._file_name, h=open(upload).read()))
        else:
            ftp = ftplib.FTP()
            ftp.connect(hopo.host, hopo.port)
            ftp.login()
            ftp.cwd("/incoming")
            for fd in self.get_files() + [{"name": self._file_name}]:
                f = fd["name"]
                LOG.debug("FTP: Uploading file: '{f}'".format(f=f))
                ftp.storbinary("STOR {f}".format(f=f), open(os.path.join(os.path.dirname(self._file_path), f)))
            open(upload, "w").write("{h}:{p}".format(h=hopo.host, p=hopo.port))
            LOG.info("FTP: '{f}' uploaded to '{h}'...".format(f=self._file_name, h=hopo.host))

    def upload_buildrequest(self, local_hopo):
        arch = self["Architecture"]
        codename = self["Base-Distribution"]

        remotes = {}

        def check_remote(remote):
            try:
                status = remote.mbd_get_status(update=True)
                if status.running and status.has_chroot(codename, arch):
                    remotes[status.load] = status
            except Exception as e:
                mini_buildd.setup.log_exception(LOG, "Builder check failed", e, logging.WARNING)

        # Always check our own instance as pseudo remote first
        check_remote(mini_buildd.models.gnupg.Remote(http=local_hopo.string))

        # Check all active remotes
        for r in mini_buildd.models.gnupg.Remote.mbd_get_active():
            check_remote(r)

        if not remotes:
            raise Exception("No builder found for {c}/{a}".format(c=codename, a=arch))

        for _load, remote in sorted(remotes.items()):
            try:
                self.upload(mini_buildd.misc.HoPo(remote.ftp))
                self.remote_http_url = "http://{r}".format(r=remote.http)
                return
            except Exception as e:
                mini_buildd.setup.log_exception(LOG, "Uploading to '{h}' failed".format(h=remote.ftp), e, logging.WARNING)

        raise Exception("Buildrequest upload failed for {a}/{c}".format(a=arch, c=codename))

    def tar(self, tar_path, add_files=None):
        with contextlib.closing(tarfile.open(tar_path, "w")) as tar:
            tar_add = lambda f: tar.add(f, arcname=os.path.basename(f))
            tar_add(self._file_path)
            for f in self.get_files():
                tar_add(os.path.join(os.path.dirname(self._file_path), f["name"]))
            if add_files:
                for f in add_files:
                    tar_add(f)

    def untar(self, path):
        tar_file = self._file_path + ".tar"
        if os.path.exists(tar_file):
            with contextlib.closing(tarfile.open(tar_file, "r")) as tar:
                tar.extractall(path=path)
        else:
            LOG.info("No tar file (skipping): {f}".format(f=tar_file))

    def archive(self, installed):
        logdir = os.path.join(mini_buildd.setup.LOG_DIR, self.get_archive_dir(installed))

        if not os.path.exists(logdir):
            os.makedirs(logdir)
        LOG.info("Moving changes to log: '{f}'->'{l}'".format(f=self._file_path, l=logdir))
        for fd in [{"name": self._file_name}] + self.get_files():
            f = os.path.join(os.path.dirname(self._file_path), fd["name"])
            LOG.debug("Moving: '{f}' to '{d}'". format(f=fd["name"], d=logdir))
            os.rename(f, os.path.join(logdir, fd["name"]))

    def remove(self):
        LOG.info("Removing changes: '{f}'".format(f=self._file_path))
        for fd in [{"name": self._file_name}] + self.get_files():
            f = os.path.join(os.path.dirname(self._file_path), fd["name"])
            LOG.debug("Removing: '{f}'".format(f=fd["name"]))
            os.remove(f)

    def gen_buildrequests(self, daemon, repository, dist, suite_option):
        """
        Build buildrequest files for all architectures.
        """

        # Extra check on all files from the dsc: Check md5 against possible pool files, add missing from pool (orig.tar.gz).
        files_from_pool = []
        dsc_file = os.path.join(os.path.dirname(self._file_path), self.dsc_name)
        dsc = debian.deb822.Dsc(file(dsc_file))
        for f in dsc["Files"]:
            for p in glob.glob(os.path.join(repository.mbd_get_path(), "pool", "*", "*", self["Source"], f["name"])):
                if f["md5sum"] == mini_buildd.misc.md5_of_file(p):
                    if not f["name"] in self.get_files(key="name"):
                        files_from_pool.append(p)
                        LOG.info("Buildrequest: File added from pool: {f}".format(f=p))
                else:
                    raise Exception("MD5 mismatch in uploaded dsc vs. pool: {f}".format(f=f["name"]))

        breq_dict = {}
        for ao in dist.architectureoption_set.all():
            path = os.path.join(self.get_spool_dir(), ao.architecture.name)

            breq = Changes(os.path.join(path,
                                        self.gen_file_name(ao.architecture.name, self.TYPE_BREQ)))

            if breq.is_new():
                for v in ["Distribution", "Source", "Version"]:
                    breq[v] = self[v]

                # Generate sources.list et.al. to be used
                open(os.path.join(path, "apt_sources.list"), 'w').write(dist.mbd_get_apt_sources_list(repository, suite_option))
                open(os.path.join(path, "apt_preferences"), 'w').write(dist.mbd_get_apt_preferences(repository, suite_option))
                open(os.path.join(path, "apt_keys"), 'w').write(repository.mbd_get_apt_keys(dist))
                chroot_setup_script = os.path.join(path, "chroot_setup_script")
                # Note: For some reason (python, django sqlite, browser?) the text field may be in DOS mode.
                open(chroot_setup_script, 'w').write(mini_buildd.misc.fromdos(dist.chroot_setup_script))

                os.chmod(chroot_setup_script, stat.S_IRWXU)
                open(os.path.join(path, "sbuildrc_snippet"), 'w').write(dist.mbd_get_sbuildrc_snippet(ao.architecture.name))

                # Generate tar from original changes
                self.tar(tar_path=breq.file_path + ".tar",
                         add_files=[os.path.join(path, "apt_sources.list"),
                                    os.path.join(path, "apt_preferences"),
                                    os.path.join(path, "apt_keys"),
                                    chroot_setup_script,
                                    os.path.join(path, "sbuildrc_snippet")] + files_from_pool)
                breq.add_file(breq.file_path + ".tar")

                breq["Upload-Result-To"] = daemon.mbd_get_ftp_hopo().string
                breq["Base-Distribution"] = dist.base_source.codename
                breq["Architecture"] = ao.architecture.name
                if ao.build_architecture_all:
                    breq["Arch-All"] = "Yes"
                breq["Build-Dep-Resolver"] = dist.get_build_dep_resolver_display()
                breq["Apt-Allow-Unauthenticated"] = "1" if dist.apt_allow_unauthenticated else "0"
                if dist.lintian_mode != dist.LINTIAN_DISABLED:
                    # Generate lintian options
                    modeargs = {
                        dist.LINTIAN_DISABLED: "",
                        dist.LINTIAN_RUN_ONLY: "",
                        dist.LINTIAN_FAIL_ON_ERROR: "",
                        dist.LINTIAN_FAIL_ON_WARNING: "--fail-on-warning"}
                    breq["Run-Lintian"] = modeargs[dist.lintian_mode] + " " + dist.lintian_extra_options

                breq.save(daemon.mbd_gnupg)
            else:
                LOG.info("Re-using existing buildrequest: {b}".format(b=breq.file_name))
            breq_dict[ao.architecture.name] = breq

        return breq_dict

    def gen_buildresult(self, path=None):
        assert(self.type == self.TYPE_BREQ)
        if not path:
            path = self.get_spool_dir()

        bres = mini_buildd.changes.Changes(os.path.join(path,
                                                        self.gen_file_name(self["Architecture"], self.TYPE_BRES)))

        for v in ["Distribution", "Source", "Version", "Architecture"]:
            bres[v] = self[v]

        return bres

    def upload_failed_buildresult(self, gnupg, hopo, retval, status, exception):
        t = tempfile.mkdtemp()
        bres = self.gen_buildresult(path=t)

        bres["Sbuildretval"] = unicode(retval)
        bres["Sbuild-Status"] = status
        buildlog = os.path.join(t, self.buildlog_name)
        with open(buildlog, "w+") as l:
            l.write("""
Host: {h}
Build request failed: {r} ({s}): {e}
""".format(h=socket.getfqdn(), r=retval, s=status, e=exception))
        bres.add_file(buildlog)
        bres.save(gnupg)
        bres.upload(hopo)

        shutil.rmtree(t)


if __name__ == "__main__":
    mini_buildd.misc.setup_console_logging()
    import doctest
    doctest.testmod()
