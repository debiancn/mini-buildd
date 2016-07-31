# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import django.views.generic.detail
from django.conf.urls import url

import mini_buildd.views
import mini_buildd.models.repository

urlpatterns = [
    url(r"^$", mini_buildd.views.home),
    url(r"^log/(.+)/(.+)/(.+)/$", mini_buildd.views.log),
    url(r"^repositories/(?P<pk>.+)/$", django.views.generic.detail.DetailView.as_view(model=mini_buildd.models.repository.Repository)),
    url(r"^api$", mini_buildd.views.api),
    url(r"^accounts/profile/$", mini_buildd.views.AccountProfileView.as_view(template_name="mini_buildd/account_profile.html")),
]

django.conf.urls.handler400 = mini_buildd.views.error400_bad_request
django.conf.urls.handler401 = mini_buildd.views.error401_unauthorized
django.conf.urls.handler404 = mini_buildd.views.error404_not_found
django.conf.urls.handler500 = mini_buildd.views.error500_internal
