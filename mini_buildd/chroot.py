# -*- coding: utf-8 -*-
import os
import re
import glob
import tempfile
import getpass
import logging

import django.db

import mini_buildd.globals
import mini_buildd.misc

log = logging.getLogger(__name__)

from mini_buildd.models import Distribution
from mini_buildd.models import Architecture

class Chroot(django.db.models.Model):
    dist = django.db.models.ForeignKey(Distribution)
    arch = django.db.models.ForeignKey(Architecture)
    filesystem = django.db.models.CharField(max_length=30, default="ext2")

    PERSONALITIES = { 'i386': 'linux32' }

    def get_backend(self):
        try:
            return self.filechroot
        except:
            try:
                return self.lvmloopchroot
            except:
                raise Exception("No chroot backend found")

    def get_path(self):
        return os.path.join(mini_buildd.globals.CHROOTS_DIR, self.dist.base_source.codename, self.arch.arch)

    def get_name(self):
        return "mini-buildd-{d}-{a}".format(d=self.dist.base_source.codename, a=self.arch.arch)

    def get_personality(self):
        """
        On 64bit hosts, 32bit schroots must be configured
        with a *linux32* personality to work.

        .. todo:: Chroot personalities

           - This may be needed for other 32-bit archs, too?
           - We currently assume we build under linux only.
        """
        try:
            return self.PERSONALITIES[self.arch.arch]
        except:
            return "linux"

    def debootstrap(self, dir):
        """
        .. todo:: debootstrap

           - mbdAptEnv ??
           - include=sudo is only workaround for sbuild Bug #608840
           - debootstrap include=apt WTF?
        """

        # START SUDOERS WORKAROUND (remove --include=sudo when fixed)
        mini_buildd.misc.run_cmd("sudo debootstrap --variant='buildd' --arch='{a}' --include='apt,sudo' '{d}' '{m}' '{M}'".
                                 format(a=self.arch.arch, d=self.dist.base_source.codename, m=dir, M=self.dist.base_source.get_mirror()))

        # STILL SUDOERS WORKAROUND (remove all when fixed)
        with tempfile.NamedTemporaryFile() as ts:
            ts.write("""
{u} ALL=(ALL) ALL
{u} ALL=NOPASSWD: ALL
""".format(u=getpass.getuser()))
            ts.flush()
            mini_buildd.misc.run_cmd("sudo cp '{ts}' '{m}/etc/sudoers'".format(ts=ts.name, m=dir))
        # END SUDOERS WORKAROUND

    def prepare_schroot_conf(self, backend_conf):
        # There must be schroot configs for each uploadable distribution (does not work with aliases).
        conf_file = os.path.join(self.get_path(), "schroot.conf")
        open(conf_file, 'w').write("""
[{n}]
type=lvm-snapshot
description=Mini-Buildd {n} LVM snapshot chroot
groups=sbuild
users=mini-buildd
root-groups=sbuild
root-users=mini-buildd
source-root-users=mini-buildd
personality={p}
{b}
""".format(n=self.get_name(), p=self.get_personality(), b=backend_conf))

        schroot_conf_file = os.path.join("/etc/schroot/chroot.d", self.get_name() + ".conf")
        mini_buildd.misc.run_cmd("sudo cp '{s}' '{d}'".format(s=conf_file, d=schroot_conf_file))

    def prepare(self):
        self.get_backend().prepare()

    def purge(self):
        self.get_backend().purge()

    def __unicode__(self):
        return "Chroot: {c}:{a}".format(c=self.dist.base_source.codename, a=self.arch.arch)


class FileChroot(Chroot):
    """ File chroot backend. """

    def prepare(self):
        pass

    def purge(self):
        pass


class LVMLoopChroot(Chroot):
    """ This class provides some interesting LVM-(loop-)device stuff. """
    loop_size = django.db.models.IntegerField(default=100,
                                              help_text="Loop device file size in GB.")

    def get_vgname(self):
        return "mini-buildd-loop-{d}-{a}".format(d=self.dist.base_source.codename, a=self.arch.arch)

    def get_backing_file(self):
        return os.path.join(self.get_path(), "lvmloop.image")

    def get_loop_device(self):
        for f in glob.glob("/sys/block/loop[0-9]*/loop/backing_file"):
            if os.path.realpath(open(f).read().strip()) == os.path.realpath(self.get_backing_file()):
                return "/dev/" + f.split("/")[3]

    def get_lvm_device(self):
        for f in glob.glob("/sys/block/loop[0-9]*/loop/backing_file"):
            if open(f).read().strip() == self.get_backing_file():
                return "/dev/" + f.split("/")[3]

    def prepare(self):
        log.info("LVM Loop chroot: Preparing '{d}' builder for '{a}'".format(d=self.dist.base_source.codename, a=self.arch))
        mini_buildd.misc.mkdirs(self.get_path())

        # Check image file
        if not os.path.exists(self.get_backing_file()):
            mini_buildd.misc.run_cmd("dd if=/dev/zero of='{imgfile}' bs='{gigs}M' seek=1024 count=0".format(\
                    imgfile=self.get_backing_file(), gigs=self.loop_size))
            log.debug("LVMLoop: Image file created: '{b}' size {s}G".format(b=self.get_backing_file(), s=self.loop_size))

        # Check loop dev
        if self.get_loop_device() == None:
            mini_buildd.misc.run_cmd("sudo losetup -v -f {img}".format(img=self.get_backing_file()))
            log.debug("LVMLoop {d}@{b}: Loop device attached".format(d=self.get_loop_device(), b=self.get_backing_file()))

        # Check lvm
        try:
            mini_buildd.misc.run_cmd("sudo vgchange --available y {vgname}".format(vgname=self.get_vgname()))
        except:
            log.debug("LVMLoop {d}@{b}: Creating new LVM '{v}'".format(d=self.get_loop_device(), b=self.get_backing_file(), v=self.get_vgname()))
            mini_buildd.misc.run_cmd("sudo pvcreate -v '{dev}'".format(dev=self.get_loop_device()))
            mini_buildd.misc.run_cmd("sudo vgcreate -v '{vgname}' '{dev}'".format(vgname=self.get_vgname(), dev=self.get_loop_device()))

        log.info("LVMLoop prepared: {d}@{b} on {v}".format(d=self.get_loop_device(), b=self.get_backing_file(), v=self.get_vgname()))

        device = "/dev/{v}/{n}".format(v=self.get_vgname(), n=self.get_name())

        try:
            mini_buildd.misc.run_cmd("sudo lvdisplay | grep -q '{c}'".format(c=self.get_name()))
            log.info("LV {c} exists, leaving alone".format(c=self.get_name()))
        except:
            log.info("Setting up LV {c}...".format(c=self.get_name()))

            mount_point = tempfile.mkdtemp()
            try:
                mini_buildd.misc.run_cmd("sudo lvcreate -L 4G -n '{n}' '{v}'".format(n=self.get_name(), v=self.get_vgname()))
                mini_buildd.misc.run_cmd("sudo mkfs.{f} '{d}'".format(f=self.filesystem, d=device))
                mini_buildd.misc.run_cmd("sudo mount -v -t{f} '{d}' '{m}'".format(f=self.filesystem, d=device, m=mount_point))

                self.debootstrap(dir=mount_point)
                mini_buildd.misc.run_cmd("sudo umount -v '{m}'".format(m=mount_point))
                log.info("LV {n} created successfully...".format(n=self.get_name()))
                self.prepare_schroot_conf("""# Backend specific options
device={d}
mount-options=-t {f} -o noatime,user_xattr
lvm-snapshot-options=--size 4G
""".format(d=device, f=self.filesystem))
            except:
                log.error("LV {n} creation FAILED. Rewinding...".format(n=self.get_name()))
                try:
                    mini_buildd.misc.run_cmd("sudo umount -v '{m}'".format(m=mount_point))
                    mini_buildd.misc.run_cmd("sudo lvremove --force '{d}'".format(d=device))
                except:
                    log.error("LV {n} rewinding FAILED.".format(n=self.get_name()))
                raise

    def purge(self):
        try:
            mini_buildd.misc.run_cmd("sudo lvremove --force {v}".format(v=self.get_vgname()))
            mini_buildd.misc.run_cmd("sudo vgremove --force {v}".format(v=self.get_vgname()))
            mini_buildd.misc.run_cmd("sudo pvremove {v}".format(v=self.get_vgname()))
            mini_buildd.misc.run_cmd("sudo losetup -d {d}".format(d=self.get_lvm_device()))
            mini_buildd.misc.run_cmd("rm -f -v '{f}'".format(f=self.get_backing_file()))
        except:
            log.warn("LVM {n}: Some purging steps may have failed".format(n=self.get_vgname()))
