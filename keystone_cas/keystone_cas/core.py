# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2013 Spanish National Research Council
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import uuid
import keystone.middleware

from urllib import urlencode, urlopen
from urlparse import urljoin

from xml.etree import ElementTree

from keystone.common import logging
from keystone.common import wsgi
from keystone import exception
from keystone import identity
# from keystone import assignment
import keystone.middleware

from oslo.config import cfg

LOG = logging.getLogger(__name__)

CONF = cfg.CONF
opts = [
    cfg.BoolOpt("autocreate_users",
                default=False,
                help="If enabled, users will be created automatically "
                     "in the local Identity backend (default False)."),
    cfg.StrOpt("default_tenant",
                default="demo",
                help="If specified users will be automatically "
                     "added to this tenant."),
    cfg.StrOpt("cas_server_url",
                default="http://localhost:8000/cas",
                help="URL of the cas server"),
    cfg.BoolOpt("add_roles",
                default=False,
                help="If enabled, users will get the roles defined in "
                "'user_roles' when created."),
    cfg.ListOpt("user_roles",
                default=["_member_"],
                help="List of roles to add to new users."),
]
CONF.register_opts(opts, group="cas")

PARAMS_ENV = keystone.middleware.PARAMS_ENV

class CASAuthMiddleware(wsgi.Middleware):
    def __init__(self, *args, **kwargs):
        self.identity_api = identity.Manager()
        self.domain = CONF.identity.default_domain_id or "default"
        super(CASAuthMiddleware, self).__init__(*args, **kwargs)

    def _validate_cas_ticket(self, ticket, service):
        params = {'ticket': ticket, 'service': service}
        url = (urljoin(CONF.cas.cas_server_url, 'proxyValidate') + '?' +
               urlencode(params))
        page = urlopen(url)
        try:
            response = page.read()
            tree = ElementTree.fromstring(response)
            if tree[0].tag.endswith('authenticationSuccess'):
                return {'name': tree[0][0].text}
            else:
                return None
        finally:
            page.close()

    def _do_create_user(self, user_ref):
        user_name = user_ref["name"]
        user_id = uuid.uuid4().hex
        LOG.info(_("Autocreating REMOTE_USER %s with id %s") %
                  (user_name, user_id))
        user = {
            "id": user_id,
            "name": user_name,
            "enabled": True,
            "domain_id": self.domain,
            "email": user_ref.get("email", "noemail"),
        }
        self.identity_api.create_user(self.identity_api,
                                      user_id,
                                      user)
        if CONF.cas.default_tenant:
            try: 
                tenant_ref = self.identity_api.get_project_by_name(
                    self.identity_api, CONF.cas.default_tenant,
                    self.domain)
            except exception.ProjectNotFound:
                raise
            user_tenants = self.identity_api.get_projects_for_user(
                self.identity_api, user_id)
            if tenant_ref["id"] not in user_tenants:
                LOG.info(_("Automatically adding user %s to tenant %s") %
                        (user_name, tenant_ref["name"]))
                self.identity_api.add_user_to_project(
                    self.identity_api,
                    tenant_ref["id"],
                    user_id)

    def is_applicable(self, request):
        """Check if the request is applicable for this handler or not"""
        params = request.environ.get(PARAMS_ENV, {})
        auth = params.get("auth", {})
        if "casCredentials" in auth:
            return True
        return False

    def _get_login_url(self, service):
        params = {'service': service}
        #if settings.CAS_EXTRA_LOGIN_PARAMS:
        #    params.update(settings.CAS_EXTRA_LOGIN_PARAMS)
        return (urljoin(CONF.cas.cas_server_url, 'login')
                + '?' + urlencode(params))

    def process_request(self, request):
        if request.environ.get('REMOTE_USER', None) is not None:
            # authenticated upstream
            return self.application

        if not self.is_applicable(request):
            return self.application

        params = request.environ.get(PARAMS_ENV)
        casCredentials = params["auth"]["casCredentials"]
         
        ticket = casCredentials.get("ticket", None)
        service = casCredentials.get("service", None)

        if not ticket:
            # this is asking for the server_url
            return wsgi.render_response({"cas_login_url":
                                         self._get_login_url(service)}) 

        user_ref = self._validate_cas_ticket(ticket, service)
        if not user_ref:
            # Wrong authentication? 
            return self.application

        user_name = user_ref["name"]
        try:
            self.identity_api.get_user_by_name(
                self.identity_api,
                user_name,
                self.domain)
        except exception.UserNotFound:
            if CONF.cas.autocreate_users:
                self._do_create_user(user_ref)

        request.environ['REMOTE_USER'] = user_name
