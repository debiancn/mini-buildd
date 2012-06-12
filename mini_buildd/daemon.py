# -*- coding: utf-8 -*-
import os, contextlib, logging

import django.db, django.core.exceptions

from mini_buildd import misc, changes, gnupg, builder

from mini_buildd.models import Repository

log = logging.getLogger(__name__)

class Daemon(django.db.models.Model):
    max_parallel_builds = django.db.models.IntegerField(
        default=4,
        help_text="Maximum number of parallel builds.")

    sbuild_parallel = django.db.models.IntegerField(
        default=1,
        help_text="Degree of parallelism per build.")

    max_parallel_packages = django.db.models.IntegerField(
        default=10,
        help_text="Maximum number of parallel packages to process.")

    gnupg_template = django.db.models.TextField(default="""
Key-Type: DSA
Key-Length: 1024
Expire-Date: 0""")

    def __init__(self, *args, **kwargs):
        ".. todo:: GPG: to be replaced in template; Only as long as we dont know better"
        super(Daemon, self).__init__(*args, **kwargs)
        self.gnupg = gnupg.GnuPG(self.gnupg_template)

    def __unicode__(self):
        res = "Daemon for: "
        for c in Repository.objects.all():
            res += c.__unicode__() + ", "
        return res

    def clean(self):
        super(Daemon, self).clean()
        if Daemon.objects.count() > 0 and self.id != Daemon.objects.get().id:
            raise django.core.exceptions.ValidationError("You can only create one Daemon instance!")

django.contrib.admin.site.register(Daemon)

def run(incoming_queue):
    daemon, created = Daemon.objects.get_or_create(id=1)

    # todo: Own GnuPG model
    log.info("Preparing {d}".format(d=daemon))
    daemon.gnupg.prepare()

    join_threads = []

    log.info("Starting {d}".format(d=daemon))
    while True:
        event = incoming_queue.get()
        if event == "SHUTDOWN":
            break

        c = changes.Changes(event)
        r = c.get_repository()
        if c.is_buildrequest():
            log.info("{p}: Got build request for {r}".format(p=c.get_pkg_id(), r=r.id))
            join_threads.append(misc.run_as_thread(builder.run, br=c))
        elif c.is_buildresult():
            log.info("{p}: Got build result for {r}".format(p=c.get_pkg_id(), r=r.id))
            c.untar(path=r.mbd_get_incoming_path())
            r._reprepro.processincoming()
        else:
            log.info("{p}: Got user upload for {r}".format(p=c.get_pkg_id(), r=r.id))
            for br in c.gen_buildrequests():
                br.upload()

        incoming_queue.task_done()

    for t in join_threads:
        log.debug("Waiting for {i}".format(i=t))
        t.join()

    log.info("Stopped {d}".format(d=daemon))