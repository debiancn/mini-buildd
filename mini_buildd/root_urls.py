# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import django.views.generic.base
import django.contrib.admin
import django.contrib.auth.views
from django.conf.urls import patterns, include, url

django.contrib.admin.autodiscover()

urlpatterns = patterns(
    "",
    # mini_buildd
    (r"^$", django.views.generic.base.RedirectView.as_view(url="/mini_buildd/", permanent=False)),
    (r"^mini_buildd/", include("mini_buildd.urls")),
    # admin
    (r"^admin/doc/", include("django.contrib.admindocs.urls")),
    (r"^admin/", include(django.contrib.admin.site.urls)),
    # registration
    url(r'^accounts/password/reset/confirm/(?P<uidb64>[0-9A-Za-z]+)-(?P<token>.+)/$', django.contrib.auth.views.password_reset_confirm, name='auth_password_reset_confirm'),
    (r'^accounts/', include("registration.backends.default.urls")),
    # registration: This extra line is needed for p-d-registration since some django update...
    (r'^accounts/', include('django.contrib.auth.urls')),
)
