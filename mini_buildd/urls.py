# -*- coding: utf-8 -*-
import django.views.static
import django.views.generic
import django.views.generic.list_detail
import django.conf.urls.defaults

import mini_buildd.views
import mini_buildd.models.repository

urlpatterns = django.conf.urls.defaults.patterns(
    '',
    (r"^$", mini_buildd.views.show_index),
    (r"^download/archive.key$", mini_buildd.views.get_archive_key),
    (r"^download/dput.cf$", mini_buildd.views.get_dput_conf),
    (r"^download/builder_state$", mini_buildd.views.get_builder_state),
    (r"^repositories/$", mini_buildd.views.get_repository_results),
    (r"^repositories/(?P<object_id>.+)/$",
     django.views.generic.list_detail.object_detail,
     {'queryset': mini_buildd.models.repository.Repository.objects.all()}),)

django.conf.urls.handler403 = "mini_buildd.views.http_status_403"
django.conf.urls.handler404 = "mini_buildd.views.http_status_404"
django.conf.urls.handler500 = "mini_buildd.views.http_status_500"
