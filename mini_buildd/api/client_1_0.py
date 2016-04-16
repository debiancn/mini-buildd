# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

import sys
import time
import pickle
import urllib2
import urlparse
import re
import httplib

import debian.debian_support

import mini_buildd.misc
import mini_buildd.api

# mini-buildd API transfers log message via HTTP headers. The default (100) is sometimes too low.
httplib._MAXHEADERS = 500  # pylint: disable=protected-access


class Daemon(object):
    def _log(self, message):
        print("{host}: {m}".format(host=self.host, m=message), file=sys.stderr)

    def _log_daemon_messages(self, headers):
        "Stolen from mini-buildd-tool"
        msgs_header = "x-mini-buildd-message"
        for msg in [v for k, v in headers.items() if msgs_header == k[:len(msgs_header)]]:
            self._log("HTTP Header Message: {m}".format(m=mini_buildd.misc.b642u(msg)))

    def __init__(self, host, port="8066", proto="http",
                 auto_confirm=False,
                 dry_run=False,
                 batch_mode=False,
                 django_mode=True):
        self.host = host
        self.port = port
        self.proto = proto
        self.url = "{proto}://{host}:{port}".format(proto=proto, host=host, port=port)
        self.api_url = "{url}/mini_buildd/api".format(url=self.url)
        self.auto_confirm = auto_confirm
        self.dry_run = dry_run
        self.batch_mode = batch_mode
        if django_mode:
            mini_buildd.api.django_pseudo_configure()

        # Extra: status caching
        self._status = None
        # Extra: dputconf caching (for archive identity workaround)
        self._dputconf = None

    def login(self, user=None):
        "Login. Use the user's mini-buildd keyring for auth, like mini-buildd-tool."
        keyring = mini_buildd.misc.Keyring("mini-buildd")
        mini_buildd.misc.web_login("{host}:{port}".format(host=self.host, port=self.port), user if (user or self.batch_mode) else raw_input("Username: "), keyring, proto=self.proto)
        return self

    def call(self, command, args=None, output="python", raise_on_error=True):
        if args is None:
            args = {}

        if self.auto_confirm:
            args["confirm"] = command
        http_get_args = "&".join("{k}={v}".format(k=k, v=v) for k, v in args.items())
        url = "{api_url}?command={command}&output={output}&{args}".format(api_url=self.api_url, command=command, output=output, args=http_get_args)

        if self.dry_run:
            self._log("Dry Run, skipping API: {}".format(url))
            return None

        self._log("Calling API: {}".format(url))
        try:
            response = urllib2.urlopen(url)
            return pickle.loads(response.read()) if output == "python" else response.read()
        except urllib2.HTTPError as e:
            self._log("API call failed with HTTP Status {status}:".format(status=e.getcode()))
            self._log_daemon_messages(e.headers)
            if not self.batch_mode and e.getcode() == 401:
                action = raw_input("Unauthorized retry: (l)ogin, (c)onfirm this call or (C)onfirm all future calls (anything else to just skip)? ")
                if action and action in "lcC":
                    new_args = args
                    if action == "l":
                        self.login()
                    elif action == "c":
                        new_args["confirm"] = command
                    elif action == "C":
                        self.auto_confirm = True
                    return self.call(command, new_args, output=output, raise_on_error=raise_on_error)
            if raise_on_error:
                raise

    # Extra functionality
    @property
    def identity(self):
        """The Archive's Identity."""
        # Bug 1.0.x: "status" does not give the archive id.
        # Workaround: Parse the archive id from dput.cf
        if self._dputconf is None:
            self._dputconf = self.call("getdputconf")
        # 1st line looks like: "[mini-buildd-my-archive-id]"
        dput_target = self._dputconf._plain_result.split("\n", 1)[0].rpartition("]")[0]  # pylint: disable=protected-access
        return dput_target[len("mini-buildd-") + 1:]

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
                        # Note: Path may also be "/source all/", not "/source/" (on non-source-only uploads?)
                        info["changes_url"] = "{base_url}/log/{repo}/{package}/{version}/source/{package}_{version}_source.changes".format(
                            base_url=base_url,
                            repo=repository,
                            package=src_package,
                            version=vers)
                        result[dist] = info

        return result

    def wait_for_package(self, distribution, src_package, version=None, or_greater=False,
                         max_tries=-1, sleep=60, initial_sleep=0,
                         raise_on_error=True):

        item = "\"{p}_{v}\" in \"{d}\"".format(p=src_package, v=version, d=distribution)

        def _sleep(secs):
            self._log("Waiting for {item}: Idling {s} seconds (Ctrl-C to abort)...".format(item=item, s=secs))
            time.sleep(secs)

        tries = 0
        _sleep(initial_sleep)
        while max_tries < 0 or tries < max_tries:
            pkg_info = self.get_package_versions(src_package, distribution).get(distribution, {})
            actual_version = pkg_info.get("version", None)
            self._log("Actual version for {item}: {v}".format(item=item, v=actual_version))

            if (version is None and actual_version) or \
               (version is not None and (actual_version == version or or_greater and debian.debian_support.Version(actual_version) >= debian.debian_support.Version(version))):
                self._log("Match found: {item}.".format(item=item))
                return pkg_info
            _sleep(sleep)
            tries += 1

        not_found_msg = "Could not find {item} within {s} seconds.".format(item=item, s=initial_sleep + tries * sleep)
        self._log(not_found_msg)
        if raise_on_error:
            raise Exception(not_found_msg)

    def has_package(self, distribution, src_package, version=None, or_greater=False):
        return self.wait_for_package(distribution, src_package, version, or_greater=or_greater,
                                     max_tries=1, sleep=0, initial_sleep=0,
                                     raise_on_error=False)

    def bulk_migrate(self, packages, repositories=None, codenames=None, suites=None):
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
                        self.call("migrate", {"package": package, "distribution": dist}, raise_on_error=False)
