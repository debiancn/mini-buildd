# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import django.views.generic.detail

try:
    # django >= 1.6
    from django.conf.urls import patterns
except ImportError:
    # django 1.5
    from django.conf.urls.defaults import patterns  # pylint: disable=import-error,no-name-in-module

import mini_buildd.views
import mini_buildd.models.repository

urlpatterns = patterns(
    '',
    (r"^$", mini_buildd.views.home),
    (r"^log/(.+)/(.+)/(.+)/$", mini_buildd.views.log),
    (r"^repositories/(?P<pk>.+)/$", django.views.generic.detail.DetailView.as_view(model=mini_buildd.models.repository.Repository)),
    (r"^api$", mini_buildd.views.api),
    (r"^accounts/profile/$", mini_buildd.views.AccountProfileView.as_view(template_name="mini_buildd/account_profile.html")),)

django.conf.urls.handler400 = mini_buildd.views.error400_bad_request
django.conf.urls.handler401 = mini_buildd.views.error401_unauthorized
django.conf.urls.handler404 = mini_buildd.views.error404_not_found
django.conf.urls.handler500 = mini_buildd.views.error500_internal
