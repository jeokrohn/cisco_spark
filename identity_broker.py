'''
Created on 07.12.2015

@author: jkrohn

class SparkToken
    * a spark token has two components:
       * refresh token
       * access token
    * token is cached in a file
    * constructor gets user information so that we can latetr get a refresh token
    * method to get access token
       * exchanges refresh token for access token if necessary
    * options for async and sync methods?
        * if we only have sync methods then a call to get an access token from an async environment blocks every now and then (every two weeks?)

class SparkAccessToken
    * simple class
    * constructor takes access token
    * no caching
    * no refresh token
    
* APIs only call the method to get the current access_token
'''
import requests
import bs4
import urllib.parse
#from urllib.parse import urlparse, urljoin, parse_qs
from datetime import datetime, timedelta
import time
import uuid
import logging
import json
import base64
import xml.dom.minidom

from spark_struct import Struct
from dump_utilities import dump_response

log = logging.getLogger(__name__)

class FlowError(Exception): pass

class IbError(Exception): pass

class CiscoIdentityBroker:
        
    def __init__(self, host='idbroker.webex.com'):
        self.host = host
        self.session = requests.Session()
        
    def endpoint(self, ep):
        return 'https://' + self.host + '/idb/oauth2/v1/' + ep
    
    class URLIntercepted (Exception): pass
    
    def _follow_redirects(self, response, intercept_url=''):
        ''' Follow redirects of response. If intercept_url is given the redirect chain stops at the 1st url which starts with the given value
        Return:
             if intercept_url is given then we return a dictionary of values in the intercepted URL or none if no intercept happened
             if no intercept_url is given then we return the last response.
        '''
        while response.status_code == 302:
            dump_response(response)
            # determine redirection target
            location = response.headers['location'].strip()
            loc_url = urllib.parse.urlparse(location)
            if loc_url.query:
                query = urllib.parse.parse_qs(loc_url.query)
                if 'error' in query:
                    raise FlowError('OAuth error(11): {}, {}'.format(query['error'][0], query['error_description'][0]))
                if intercept_url and location.startswith(intercept_url):
                    return query
            # follow redirection
            location = urllib.parse.urljoin(response.request.url, location) 
            response = self.session.get(location, allow_redirects=False)
        return None if intercept_url else response
    
    def _cisco_sso_user_auth(self, response, user_id, user_password):
        ''' execute the full web browser flow for a cisco.com SSO enabled user
        return is the response after the authentication flow. The response typically will be the form asking for authorization for the client
        '''
        # Form based authentication for cisco.com SSO enabled user    
        
        # this gets us a hidden form which we need to submit
        soup = bs4.BeautifulSoup(response.text, 'lxml')
        form = soup.find('form')
        if not form: raise FlowError('No form found(2)')
        
        # the action tag has a URL to be used for the form action. The full URL uses the same base as the request URL
        form_action = urllib.parse.urljoin(response.request.url, form.get('action', urllib.parse.urlparse(response.request.url).path)) 
        
        # There should be a few input fields carrying RelayState and SAMLRequest
        inputs = form.find_all('input')
        if not inputs: raise FlowError('No input fields found(3)')
        
        # compile the form data
        form_data = {inp['name'] : inp['value'] for inp in inputs if inp['type'] != 'submit'}
        
        # Try to post the form
        log.debug('auth code grant flow (Cisco SSO, {}): submit hidden form with SAMLRequest to {}'.format(user_id, form_action))
        response = self.session.post(form_action, data = form_data)
        dump_response(response)
        if response.status_code !=200: raise FlowError('Unexpected status code on POST(4): {} {}'.format(response.status_code, response.reason)) 
        
        # this get's us to a page where the CEC credentials need to be entered
        # Now we should be at the point where we use form based authentication
        soup = bs4.BeautifulSoup(response.text, 'lxml')
        form = soup.find('form')
        if not form: raise FlowError('No form found(5)')
        
        # the action tag has a URL to be used for the form action. The full URL uses the same base as the request URL
        form_action = urllib.parse.urljoin(response.request.url, form.get('action', urllib.parse.urlparse(response.url).path)) 
        
        inputs = form.find_all('input')
        if not inputs: raise FlowError('No input fields found(6)')
        
        # compile the form data
        # we assume that the 1st two fields are user and password
        form_data = {inp['name'] : inp['value'] for inp in inputs[2:] if inp['type'] != 'submit'}
        form_data[inputs[0]['name']] = user_id
        form_data[inputs[1]['name']] = user_password
        
        # Try to post the form, w/o redirects; location has trailing spaces which the requests modulde does not strip
        log.debug('auth code grant flow (Cisco SSO, {}): Posting credentials to {}'.format(user_id, form_action))
        for _ in range(5):
            try:
                response = self.session.post(form_action, data = form_data, allow_redirects=False)
            except requests.exceptions.ConnectionError:
                time.sleep(1)
                continue
            break
        response = self._follow_redirects(response)
        dump_response(response)
        
        if response.status_code !=200: raise FlowError('Unexpected status code on POST(7): {} {}'.format(response.status_code, response.reason))
        
        # let's check for an error message
        soup = bs4.BeautifulSoup(response.text, 'lxml')
        warn_msg = soup.find(id='warning-msg')
        if warn_msg:
            raise FlowError('Authentication problem: \n{}'.format(warn_msg.text.strip()))
            
        # this gets us to a page with some JavaScript code which resumes somewhere
        q = urllib.parse.parse_qs(urllib.parse.urlparse(response.url).query, keep_blank_values=True)
        if not 'resumePath' in q:
            raise FlowError ('Could not find resume path in query string: {}'.format(response.url))
        resume_url = 'https://cloudsso.cisco.com' + q['resumePath'][0]
        
        log.debug('auth code grant flow (Cisco SSO, {}): Resume flow. Get on {}'.format(user_id, resume_url))
        retries = 0
        while True:
            try:
                response = self.session.get(resume_url)
            except requests.exceptions.ConnectionError:
                retries += 1
                if retries >= 5: raise
                time.sleep(1)
                continue
            break
        dump_response(response)
        if response.status_code !=200: raise FlowError('Unexpected status code on GET(8): {} {}'.format(response.status_code, response.reason)) 
        
        # this returns a page with <body onload="javascript:document.forms[0].submit()">
        # So we again need to look at the embedded form
        soup = bs4.BeautifulSoup(response.text, 'lxml')
        form = soup.find('form')
        if not form: raise FlowError('No form found(9)')
        
        # the action tag has a URL to be used for the form action. The full URL uses the same base as the request URL
        form_action = urllib.parse.urljoin(response.url, form.get('action', urllib.parse.urlparse(response.url).path)) 
        
        # There should be a few input fields carrying RelayState and SAMLResponse
        inputs = form.find_all('input')
        if not inputs: raise FlowError('No input fields found(10)')
        
        # compile the form data
        form_data = {inp['name'] : inp['value'] for inp in inputs if inp['type'] != 'submit'}
        
        if log.isEnabledFor(logging.DEBUG):
            # take a look at the SAMLResponse
            saml_response = form_data['SAMLResponse']
            for l in (s for s in xml.dom.minidom.parseString(base64.b64decode(saml_response)).toprettyxml().splitlines() if s.strip()):
                log.debug('SAML Response: {}'.format(l))
                
            #print('SAML Response:\n  ')
            #print('\n  '.join((s for s in xml.dom.minidom.parseString(base64.b64decode(saml_response)).toprettyxml().splitlines() if s.strip())))
                        
        # post the form, but w/o automatic redirects, b/c we want to be able to intercept errors
        log.debug('auth code grant flow (Cisco SSO, {}): Submit hidden form to {}'.format(user_id, form_action))
        
        response = self.session.post(form_action, data = form_data, allow_redirects=False)
        response = self._follow_redirects(response)
        if response.status_code !=200: raise FlowError('Unexpected status code on GET(12): {} {}'.format(response.status_code, response.reason)) 
        return response
        
    def auth_code_grant_flow(self, user_info, client_info, scope = 'webexsquare:admin'):
        ''' Executes an OAuth Authorization Code Grant Flow
        Returns an Authorizatioon code
        '''
        assert user_info['email']
        assert user_info['id']
        assert user_info['password']
        assert client_info['id']
        assert client_info['redirect_uri']
        assert client_info['secret']
        
        # we try to use the Authorization Code Grant Flow
        endpoint = self.endpoint('authorize')
        
        # random state
        flow_state = str(uuid.uuid4())
        data = Struct() 
        data.response_type = 'code'
        data.state = flow_state
        data.client_id = client_info['id']
        data.redirect_uri = client_info['redirect_uri']
        data.scope = scope
        log.debug('auth code grant flow: access endpoint {}'.format(endpoint))
        
        response = self.session.get(endpoint, params=data.get_dict())
        dump_response(response)
        if response.status_code !=200: raise FlowError('Unexpected status code on GET(1): {} {}'.format(response.status_code, response.reason)) 
      
        # after a number of redirects this gets us to a page on which we need to enter an email address
        # The title is "Sign In - Cisco WebEx"
        # if we still have a valid session cookie we might actually get to the OAuth2 authorization page directly
        soup = bs4.BeautifulSoup(response.text, 'lxml')
        title = soup.find('title')
        
        if not(title and title.text.strip() in ['Sign In - Cisco WebEx', 'OAuth2 Authorization - Cisco WebEx']):
            raise FlowError('Didn\'t find expected title')
        
        if title and title.text.strip() == 'Sign In - Cisco WebEx':
            # Need to sign in.
           
            log.debug('auth code grant flow: found expected \'Sign In - Cisco WebEx\'')
            '''
            This form is part of the reply:
                <form name="GlobalEmailLookup" id="GlobalEmailLookupForm" method="post" action="/idb/globalLogin">
                    <input type="hidden" id="email" name="email" value=""></input>
                    <input type="hidden" id="isCookie" name="isCookie" value="false"></input>
                    <input type="hidden" name="gotoUrl" value="aHR0cHM6Ly9pZGJyb2tlci53ZWJleC5jb20vaWRiL29hdXRoMi92MS9hdXRob3JpemU/c2NvcGU9c3BhcmslM0FwZW9wbGVfcmVhZCtzcGFyayUzQXJvb21zX3JlYWQrc3BhcmslM0FtZW1iZXJzaGlwc19yZWFkK3NwYXJrJTNBbWVzc2FnZXNfcmVhZCZjbGllbnRfaWQ9Q2U2N2Y5NzE0YTEzN2U2ODg0OGJhNjQ1YzQ4NjBmYThhZWUyYzUwMzFlZTA1YmMyMjE2MzNkMGNlZWRlOWExYjkmcmVkaXJlY3RfdXJpPWh0dHBzJTNBJTJGJTJGb2F1dGgua3JvaG5zLmRlJTJGb2F1dGgyJnN0YXRlPXNvbWVSYW5kb21TdHJpbmcmcmVzcG9uc2VfdHlwZT1jb2Rl" />
                    <input type="hidden" id="encodedParamsString" name="encodedParamsString" value="dHlwZT1sb2dpbg==" />
                </form>
            A POST with the email address to that form is the next step
            '''
            soup = bs4.BeautifulSoup(response.text, 'lxml')
            form = soup.find(id = 'GlobalEmailLookupForm')
            if not form: raise FlowError('Couldn\'t find form \'GlobalEmailLookupForm\' to post user\'s email address')
            
            inputs = form.find_all('input')
            # 1st input is the email address
            inputs[0]['value'] = user_info['email']
            form_data = {i['name'] : i['value'] for i in inputs}
            form_action = urllib.parse.urljoin(response.request.url, form.get('action', urllib.parse.urlparse(response.request.url).path)) 
            log.debug('auth code grant flow: Posting email address {} to form {}'.format(user_info['email'], form_action))
            response = self.session.post(form_action, data = form_data)
            dump_response(response)    
            
            # For CIS users this redirects us to a page with title "Sign In - Cisco WebEx"
            log.debug('auth code grant flow: Checking for title \'Sign In - Cisco WebEx\'')
            soup = bs4.BeautifulSoup(response.text, 'lxml')
            title = soup.find('title')
            if title and title.text.strip() == 'Sign In - Cisco WebEx':
                # Identified the form to directly enter credentials
                dump_response(response)
                # search for the form with name 'Login'
                form = soup.find(lambda tag : tag.name == 'form' and tag.get('name', '') == 'Login')
                inputs = form.find_all('input')
                form_data = {i['name'] : i['value'] for i in inputs}
                form_data['IDToken0'] = ''
                form_data['IDToken1'] = user_info['email']
                form_data['IDToken2'] = user_info['password']
                form_data['IDButton'] = 'Sign In'
                form_action = urllib.parse.urljoin(response.request.url, form.get('action', urllib.parse.urlparse(response.request.url).path)) 
                log.debug('auth code grant flow: Found title \'Sign In - Cisco WebEx\'. Posting credentials to {}'.format(form_action))
                response = self.session.post(form_action, data = form_data)
                dump_response(response)
            else:
                # authentication of a cisco.com SSO enabled user requires multiple steps (SAML 2.0 REDIRECT/POST flow with some javascript ...
                response = self._cisco_sso_user_auth(response, user_info['id'], user_info['password'])
            # if title and title.text.strip() == if title and title.text.strip() == 'Sign In - Cisco WebEx': .. else ..
        # if title and title.text.strip() == 'Sign In - Cisco WebEx':
                    
        # this now is a form where we are requested to grant the requested access
        soup = bs4.BeautifulSoup(response.text, 'lxml')
        form = soup.find('form')
        if not form: raise FlowError('No form found(13)')
        
        # the action tag has a URL to be used for the form action. The full URL uses the same base as the request URL
        form_action = urllib.parse.urljoin(response.url, form.get('action', urllib.parse.urlparse(response.url).path)) 
        
        inputs = form.find_all('input')
        if not inputs: raise FlowError('No input fields found(14)')
        
        # compile the form data
        # the form basically has few hidden fields and the "decision" field needs to be set to "accept"
        form_data = {inp['name'] : inp['value'] for inp in inputs if inp['type'] == 'hidden'}
        form_data['decision'] = 'accept'
        
        # Again post, but no automatic redirects
        log.debug('auth code grant flow: Granting access to client by posting \'accept\' decision')
        response = self.session.post(form_action, data = form_data, allow_redirects=False)
        
        # follow redirects, but stop at client redirect URI; this allows to use non-existing redirect URIs
        response = self._follow_redirects(response, client_info['redirect_uri'])
        if not response: raise FlowError('Failed to get OAuth authorization code')
        if response['state'][0] != flow_state: raise FlowError('State has been tampered with?!. Got ({}), expected ({})'.format(response['state'][0], flow_state))
        return response['code'][0]
        
    def auth_code_to_token(self, client_info, code):
        
        endpoint = self.endpoint('access_token')
        data = Struct() 
        data.grant_type = 'authorization_code'
        data.redirect_uri = client_info['redirect_uri']
        data.code = code
        data.client_id = client_info['id']
        data.client_secret = client_info['secret']
        
        log.debug('Exchanging code for access token. POST to {}'.format(endpoint))
        response = self.session.post(endpoint, data=data.get_dict())
        dump_response(response)
        if response.status_code != 200: raise IbError('Unexpected status code on POST(12): {} {}'.format(response.status_code, response.reason))
        
        oauth_token = Struct(response.json())
        return oauth_token
    
    def refresh_token_to_access_token(self, refresh_token, client_info):
        endpoint = self.endpoint('access_token')
        data = Struct()
        data.grant_type = 'refresh_token'
        data.refresh_token = refresh_token.token
        data.client_id = client_info['id']
        data.client_secret = client_info['secret']
        
        # Sometime we get a 401 and retrying helps to fix that temporary glitch
        for _ in range(5):
            response = self.session.post(endpoint, data=data.get_dict())
            dump_response(response)
            if response.status_code != 401: break
            log.warning('Got 401 on token refresh. Retrying...')
        if response.status_code != 200: raise IbError('Unexpected status code on GET(12): {} {}'.format(response.status_code, response.reason), response.status_code, response.reason, response.text)
        result = Struct(response.json())
        return result

class SparkDevIdentityBroker(CiscoIdentityBroker):
    ''' Identity broker API specific to the the api.ciscospark.com flows:
    https://dev-preview.ciscospark.com/authentication.html
    '''
    def __init__(self, host='api.ciscospark.com'):
        CiscoIdentityBroker.__init__(self, host)
        
    def endpoint(self, ep):
        return 'https://' + self.host + '/v1/' + ep
    
    def auth_code_grant_flow(self, user_info, client_info, scope = 'spark:people_read spark:rooms_read spark:memberships_read spark:messages_read spark:rooms_write spark:memberships_write spark:messages_write spark:teams_read spark:teams_write spark:team_memberships_read spark:team_memberships_write'):
        ''' scope is a space separated list of requested scopes:
            spark:people_read Read your users’ company directory
            spark:rooms_read List the titles of rooms that your user’s are in
            spark:rooms_write Manage rooms on your users’ behalf
            spark:memberships_read List people in the rooms your user’s are in
            spark:memberships_write Invite people to rooms on your users’ behalf
            spark:messages_read Read the content of rooms that your user’s are in
            spark:messages_write Post and delete messages on your users’ behalf
            spark:teams_read List the teams your users are in
            spark:teams_write Create teams on your users’ behalf
            spark:team_memberships_read List the people in the teams your user’s belong to
            spark:team_memberships_write Add people to teams on your users’ behalf
            '''
        return CiscoIdentityBroker.auth_code_grant_flow(self, user_info, client_info, scope)
 
class SparkCCMIndentityBroker(CiscoIdentityBroker):
    pass

class AuthToken(Struct):
    def __init__(self, token, expires_in, token_type, expires_at = None):
        Struct.__init__(self, {k:v for k,v in locals().items() if k != 'self'})
        if expires_at == None:
            issued = datetime.utcnow()
            self.expires_at = issued + timedelta(seconds=self.expires_in)
    
    def has_expired(self):
        return datetime.utcnow() > self.expires_at
    
    def time_remaining(self):
        return self.expires_at - datetime.utcnow()
    
    def ratio_remaining(self):
        return self.time_remaining().total_seconds() / self.expires_in
    
    def about_to_expire(self):
        delta = self.expires_at - datetime.utcnow()
        # we want to refresh the token if the remaining seconds until expiration are
        # less than 10% of the initial expiration time
        margin = self.expires_in * 0.1
        log.debug('{} token lifetime is {} seconds, expires at {}, lifetime remaining: {} seconds, {:.0%}'.
                     format(self.token_type, self.expires_in, self.expires_at, int(delta.total_seconds()), self.ratio_remaining()))
        
        result = delta.total_seconds() < margin
        if result:
            log.info('{} token about to expire. lifetime is {} seconds, expires at {}, lifetime remaining: {} seconds, {:.0%}'.
                     format(self.token_type, self.expires_in, self.expires_at, int(delta.total_seconds()), self.ratio_remaining()))
        return result
    
    def bearer_auth(self):
        return 'Bearer ' + self.token
        
class OAuthToken(AuthToken):
    
    def __init__(self, ib, user_info, client_info, cache_token = True, **kwargs):
        ''' can take an optional scope argument which is passed to the brokers auth_code_grant_flow.
        If scope is not given then the default of the broker is used
        scope is a space separated list of requested scopes:
            spark:people_read          Read your company directory
            spark:rooms_read           List the titles of rooms that you're in
            spark:rooms_write          Manage rooms on your behalf
            spark:memberships_read     List the people in rooms that you're in
            spark:memberships_write    Invite people to rooms on your behalf
            spark:messages_read        Read the content of rooms that you're in
            spark:messages_write       Post and delete messages on your behalf
        '''
        self._ib = ib
        self._user_info = user_info
        self._client_info = client_info
        self.cache_token = cache_token
        
        refresh_token = None
        if cache_token:
            '''
            check if a cached refresh token exists
            These are saved as <userid>-token.json.
            '''
            cache_file = '{}-refresh.json'.format(user_info['id'])
            try:
                f = open(cache_file, 'r')
            except IOError:
                pass
            else:
                log.info('Reading cached refresh token from file {}'.format(cache_file))
                refresh_token = Struct(json.load(f))
                f.close()
                refresh_token.expires_at = datetime.strptime(refresh_token.expires_at, '%Y-%m-%d %H:%M:%S')
                refresh_token = AuthToken(token = refresh_token.token, 
                                          expires_in = refresh_token.expires_in, 
                                          token_type = refresh_token.token_type, 
                                          expires_at = refresh_token.expires_at)
                log.info('Refresh token lifetime is {} seconds, expires at {}, lifetime remaining: {:.0%}'.
                         format(refresh_token.expires_in, refresh_token.expires_at, refresh_token.ratio_remaining()))
        # if cache_token:
        
        # this is the handler to get a new refresh token
        self.get_new_refresh_token = lambda: self.code_grant_flow(**kwargs)
        
        if refresh_token:
            # exchange refresh token for access token
            self._refresh = refresh_token
            self.refresh_access_token()
        else:
            self.get_new_refresh_token()
            
    def code_grant_flow(self, **kwargs):
        log.info('Initiating auth code grant flow for user {}'.format(self._user_info['id']))
        
        code = self._ib.auth_code_grant_flow(self._user_info, self._client_info, **kwargs)
        log.debug('Auth code grant flow for user {}, got code {}'.format(self._user_info['id'], code))
        
        # exchange code against OAuth token
        token = self._ib.auth_code_to_token(self._client_info, code)
        self._access = AuthToken(token = token.access_token, 
                                expires_in = token.expires_in, 
                                token_type = 'Access')
        log.debug('Auth code grant flow for user {}. Access token valid for {} seconds until {}'.format(self._user_info['id'], self._access.expires_in, self._access.expires_at))
        self._refresh = AuthToken(token=token.refresh_token, 
                                 expires_in = token.refresh_token_expires_in, 
                                 token_type = 'Refresh')
        log.debug('Auth code grant flow for user {}. Refresh token valid for {} seconds until {}'.format(self._user_info['id'], self._refresh.expires_in, self._refresh.expires_at))
        
        if self.cache_token:
            # make sure that the refresh token is cached
            refresh_token = self._refresh.get_dict()
            refresh_token['expires_at'] = refresh_token['expires_at'].strftime('%Y-%m-%d %H:%M:%S')
            cache_file = '{}-refresh.json'.format(self._user_info['id'])
        
            log.info('Caching refresh token in file {}'.format(cache_file))
            f = open(cache_file, 'w')
            json.dump(refresh_token, f)
            f.close()
            
    def get_access_token(self):
        return self._access
    
    def refresh_access_token(self):
        if self._refresh.about_to_expire():
            log.info('Refresh token about to expire. Getting new refresh token...')
            self.get_new_refresh_token()
        else:
            token = self._ib.refresh_token_to_access_token(self._refresh, self._client_info)
            self._access = AuthToken(token = token.access_token,
                                     expires_in = token.expires_in,
                                     token_type = 'Access')
            log.info('Refreshed access token using refresh token')
            log.info('Access token token lifetime is {} seconds, expires at {}, lifetime remaining: {:.0%}'.
                     format(self._access.expires_in, self._access.expires_at, self._access.ratio_remaining()))
        
    def check_refresh(self):
        if self._access.about_to_expire():
            self.refresh_access_token()
            
    def bearer_auth(self):
        self.check_refresh()
        return self._access.bearer_auth()