# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

import sys
import pickle
import urllib2
import urlparse
import re

import mini_buildd.misc


class Daemon(object):
    def _log(self, message):
        print("{host}: {m}".format(host=self.host, m=message), file=sys.stderr)

    def _log_daemon_messages(self, headers):
        "Stolen from mini-buildd-tool"
        msgs_header = "x-mini-buildd-message"
        for msg in [v for k, v in headers.items() if msgs_header == k[:len(msgs_header)]]:
            self._log("HTTP Header Message: {m}".format(host=self.host, m=mini_buildd.misc.b642u(msg)))

    def __init__(self, host, port="8066", proto="http"):
        self.host = host
        self.port = port
        self.proto = proto
        self.url = "{proto}://{host}:{port}".format(proto=proto, host=host, port=port)
        self.api_url = "{url}/mini_buildd/api".format(url=self.url)
        self.auto_confirm = True

        # Extra: status caching
        self._status = None
        # Extra: dputconf caching (for archive identity workaround)
        self._dputconf = None

    def login(self, user):
        "Login. Use the user's mini-buildd keyring for auth, like mini-buildd-tool."
        keyring = mini_buildd.misc.Keyring("mini-buildd")
        mini_buildd.misc.web_login("{host}:{port}".format(host=self.host, port=self.port), user, keyring)

    def set_auto_confirm(self, confirm=True):
        self.auto_confirm = confirm

    def call(self, command, args={}, output="python"):
        if self.auto_confirm:
            args["confirm"] = command
        http_get_args = "&".join("{k}={v}".format(k=k, v=v) for k, v in args.items())
        url = "{api_url}?command={command}&output={output}&{args}".format(api_url=self.api_url, command=command, output=output, args=http_get_args)
        self._log("API call URL: {}".format(url))

        try:
            response = urllib2.urlopen(url)
            self._log("HTTP Status: {status}".format(status=response.getcode()))
            if output == "python":
                return pickle.loads(response.read())
            else:
                return response.read()
        except urllib2.HTTPError as e:
            self._log_daemon_messages(e.headers)

    # Extra functionality
    @property
    def identity(self):
        """The Archive's Identity."""
        # Bug 1.0.x: "status" does not give the archive id.
        # Workaround: Parse the archive id from dput.cf
        if self._dputconf is None:
            self._dputconf = self.call("getdputconf")
        return self._dputconf._plain_result.split("\n", 1)[0].rpartition("]")[0].rpartition("-")[2]

    @property
    def status(self):
        if self._status is None:
            self._status = self.call("status")
        return self._status

    @property
    def repositories(self):
        return self.status.repositories.keys()

    def get_codenames(self, repo):
        return self.status.repositories[repo]

    def get_package_versions(self, src_package, dist_regex=".*"):
        """Helper: Produce a dict with all (except rollback) available versions of this package (key=distribution, value=info dict: version, dsc_url, log_url, changes_url*)."""
        def _base_url(url):
            url_parse = urlparse.urlparse(url)
            return "{scheme}://{hostname}:{port}".format(scheme=url_parse.scheme, hostname=url_parse.hostname, port=url_parse.port)

        show = self.call("show", {"package": src_package})
        result = {}
        for repository in show.repositories:
            for versions in repository[1]:
                for version in versions[1]:
                    dist = version["distribution"]
                    vers = version["sourceversion"]
                    # Note: 'vers' may be empty when only rollbacks exist
                    if vers and re.match(dist_regex, dist):
                        info = {}

                        repository = mini_buildd.misc.Distribution(dist).repository

                        info["version"] = vers
                        info["dsc_url"] = version["dsc_url"]
                        base_url = _base_url(info["dsc_url"])
                        info["log_url"] = "{base_url}/mini_buildd/log/{repo}/{package}/{version}/".format(
                            base_url=base_url,
                            repo=repository,
                            package=src_package,
                            version=vers)
                        # TODO/BUG: Path may also be "/source all/", not "/source/" (on non-source-only uploads?)
                        info["changes_url"] = "{base_url}/log/{repo}/{package}/{version}/source/{package}_{version}_source.changes".format(
                            base_url=base_url,
                            repo=repository,
                            package=src_package,
                            version=vers)
                        result[dist] = info

        return result

    def _bulk_migrate(self, packages, repositories=None, codenames=None, suites=None):
        status = self.call("status")

        if repositories is None:
            repositories = list(status.repositories.keys())
        if suites is None:
            suites = ["unstable", "testing"]

        for package in packages:
            for repository in repositories:
                iter_codenames = codenames
                if iter_codenames is None:
                    iter_codenames = list(status.repositories[repository])
                for codename in iter_codenames:
                    for suite in suites:
                        dist = "{c}-{r}-{s}".format(c=codename, r=repository, s=suite)
                        self.call("migrate", {"package": package, "distribution": dist})

    def django_pseudo_configure(self):
        import mini_buildd.django_settings
        import django.core.management
        import mini_buildd.models

        mini_buildd.django_settings.pseudo_configure()
        mini_buildd.models.import_all()
        django.core.management.call_command("migrate", interactive=False, run_syncdb=True, verbosity=0)
