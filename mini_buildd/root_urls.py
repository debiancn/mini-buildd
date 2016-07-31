# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import django.views.generic.base
import django.contrib.admin
import django.contrib.auth.views
from django.conf.urls import include, url

django.contrib.admin.autodiscover()

urlpatterns = [
    # mini_buildd
    url(r"^$", django.views.generic.base.RedirectView.as_view(url="/mini_buildd/", permanent=False)),
    url(r"^mini_buildd/", include("mini_buildd.urls")),
    # admin
    url(r"^admin/doc/", include("django.contrib.admindocs.urls")),
    url(r"^admin/", include(django.contrib.admin.site.urls)),
    # registration
    url(r"^accounts/password/reset/confirm/(?P<uidb64>[0-9A-Za-z]+)-(?P<token>.+)/$", django.contrib.auth.views.password_reset_confirm, name="auth_password_reset_confirm"),
    url(r"^accounts/", include("registration.backends.model_activation.urls")),
    # registration: This extra line is needed for p-d-registration since some django update...
    url(r"^accounts/", include("django.contrib.auth.urls")),
]
