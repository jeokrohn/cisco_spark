'''
Created on 07.12.2015

@author: jkrohn

Simplified Spark APIs
https://developer.ciscospark.com
'''
import requests
import base64
from datetime import datetime
from json.decoder import JSONDecodeError
from functools import wraps
import logging
import time

from dump_utilities import dump_response

log = logging.getLogger(__name__)

def base64_id_to_str(spark_id):
    ''' decode a Spark id (base64 encoded)
    '''
    #spark_id += '=' * ((4 - len(spark_id) % 4) % 4)
    return base64.b64decode(spark_id + '==').decode()        

def base64_id_to_UUID(spark_id):
    return base64_id_to_str(spark_id).split('/')[-1] 

def str_to_time(s):
    ''' converts a date/time string as used commonly in the Spark APIs to a datetime object
    '''
    return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S.%fZ')

def time_to_str(t):
    ''' converts a datetime object to a time string commonly used in the Spark APIs
    '''
    return t.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]+'Z'

class APIError(Exception):
    def __init__(self, *args):
        Exception.__init__(self, *args)
        i = 0
        for a in self.args:
            self.__dict__[['status_code', 'reason', 'info'][i]] = a
            i += 1
            
    def __str__(self):
        return self.__repr__()

def dumpArgs(func):
    '''Decorator to print function call details - parameters names and effective values'''
    @wraps(func)
    def wrapper(*func_args, **func_kwargs):
        global log
        if log.isEnabledFor(logging.DEBUG): 
            arg_names = func.__code__.co_varnames[:func.__code__.co_argcount]
            args = func_args[:len(arg_names)]
            defaults = func.__defaults__ or ()
            args = args + defaults[len(defaults) - (func.__code__.co_argcount - len(args)):]
            params = [list(z) for z in zip(arg_names, args) if z[0] != 'self']
            args = func_args[len(arg_names):]
            if args: params.append(('args', args))
            if func_kwargs:
                for p in params:
                    if p[0] in func_kwargs: 
                        p[1] = func_kwargs[p[0]]
                #if func_kwargs: params.append(('kwargs', func_kwargs))
            log.debug(func.__name__ + ' (' + ', '.join('%s = %r' % (p[0], p[1]) for p in params) + ')')
        return func(*func_args, **func_kwargs)
    return wrapper         
        
def _api_call(f):
    '''Decorator/wrapper for all API calls
    '''
     
    @wraps(f)
    def wrapper (*args, **kwargs):    
        r = f(*args, **kwargs)
        if r.status_code >= 200 and r.status_code <= 299:
            if r.text:
                r = r.json()
                # if result has 'items' then just return that
                if isinstance(r, dict):
                    r = r.get('items', r)
            else:
                r = ''
            return r
        # request failed. Try to get some description (JSON)
        try:
            info = r.json()
        except JSONDecodeError:
            info = '{}'
        raise APIError(r.status_code, r.reason, info)
    return wrapper


def _pagination_iterator(f):
    ''' Decorator/wrapper for iterators
    '''
    
    def pagination(spark, endpoint, params):
        global log
        log.debug('Pagination get 1st: %s' % endpoint)
        while endpoint:
            r = spark.get(endpoint, params=params)
            # params are only needed in the first call, for further calls the link headers have the parameters
            params = {}
            if r.status_code != 200: 
                try:
                    info = r.json()
                except JSONDecodeError:
                    info = '{}'
                raise APIError(r.status_code, r.reason, info)
            items = r.json()['items']
            for item in items:
                yield item
            # let's see if we have a 'next' header
            endpoint = r.links.get('next', None)
            if not endpoint: break
            endpoint = endpoint['url']
            log.debug('Pagination get next %s' % endpoint)
        return
    
    @wraps(f)
    def wrapper(*args, **kwargs):
        (spark, endpoint, params) = f(*args, **kwargs)
        return pagination(spark, endpoint, params)
    
    return wrapper

def _method(f):
    ''' Decorator for get, post, put, delete methods
    Adds OAuth authentication and checks for code 429 (too many requests), 500 and 502
    '''
    
    @wraps(f)
    def wrapper(self, endpoint, auto_retry = False, **kwargs):
        self.add_auth_to_headers(kwargs)
        back_off = 1
        retries = 0
        while True:
            try:
                response = f(self, endpoint, **kwargs)
            except requests.exceptions.ConnectionError:
                if retries < 5:
                    log.warning('Connection error encountered. Retry')
                    retries = retries + 1
                    continue
                else:
                    raise
            retries = 0
                
            dump_response(response)
            if response.status_code == 429:
                try:
                    retry_after = min(int(response.headers['retry-after']), 1)
                except Exception:
                    retry_after = 1
                log.warning ('429 encountered. Retry after {} seconds'.format(retry_after))
                time.sleep(retry_after)
                continue
            
            if (response.status_code in [500, 502]) and auto_retry and back_off < 600:
                message = {}
                # if we get a 500 b/c a message can not be decrypted then a retry will not help
                try:
                    message = response.json()
                except JSONDecodeError:
                    message = {}
                if message.get('message', '') in ['Unable to parse encrypted message', 
                                                  'Unable to decrypt content name.',
                                                  'Unable to decrypt message',
                                                  'DefaultActivityEncryptionKeyUrl not found.']: 
                    # retry does not help
                    break
                # if message.get..
                log.warning('\'{}\' encountered. Message {}. Retry, waiting for {} second(s)'.format(response.reason, message, back_off))
                time.sleep(back_off)
                back_off = back_off * 2
                continue
            # if (response.status_code ...
            break
        # while True:
        return response 
        
    return wrapper

class TrivialToken:
    def __init__(self, token):
        self.auth = token
        
    def bearer_auth(self):
        return 'Bearer {}'.format(self.auth)
    
class SparkAPI:
    def __init__(self, token):
        ''' 
        parameters:
            token:  OAuth token. Can be a string or an object. If an object is passed then the object has to have
                    a bearer_auth method returning a Bearer authentication header for the token
        '''
        if isinstance(token, str):
            self.token = TrivialToken(token)
        else:
            self.token = token
        self.session = requests.Session()
        
    def bearer_auth(self):
        return self.token.bearer_auth()
        
    def add_auth_to_headers(self, kwargs):
        headers = kwargs.get('headers', {})
        headers['Authorization'] = self.bearer_auth()
        kwargs['headers'] = headers
        return kwargs['headers']
    
    def endpoint(self, api = None, para = None):
        ep = 'https://api.ciscospark.com/v1'
        if api: ep += '/' + api
        if para: ep += '/' + para
        return ep
    
    ############################ basic HTTP methods
    @_method
    def get(self, endpoint, **kwargs):
        return self.session.get(endpoint, **kwargs)
    
    @_method
    def head(self, endpoint, **kwargs):
        return self.session.head(endpoint, **kwargs)
        
    @_method
    def post(self, endpoint, **kwargs):
        return self.session.post(endpoint, **kwargs)
        
    @_method
    def put(self, endpoint, **kwargs):
        return self.session.put(endpoint, **kwargs)
        
    @_method
    def delete(self, endpoint, **kwargs):
        return self.session.delete(endpoint, **kwargs)
        
    ############################# people
    @_pagination_iterator
    @dumpArgs
    def list_people(self, p_email=None, p_displayName=None, p_max=None):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v}
        endpoint = self.endpoint('people')
        return (self, endpoint, params)
    
    @_api_call
    @dumpArgs
    def get_person_details(self, personId):
        ''' Get person details
        personId can be 'me'
        '''
        endpoint = self.endpoint('people', personId)
        return self.get(endpoint)
        
    ############################# rooms
            
    @_pagination_iterator
    @dumpArgs
    def list_rooms(self, p_showSipAddress = None, p_teamId = None, p_max = None, p_type = None):
        assert p_type == None or (isinstance(p_type, str) and p_type in ['direct', 'group']), "type needs to be 'direct' or 'group'"
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v}
        endpoint = self.endpoint('rooms')
        return (self, endpoint, params)
            
    @_api_call
    @dumpArgs
    def create_room(self, p_title, p_teamId = None):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v}
        endpoint = self.endpoint('rooms')
        return self.post(endpoint, json = params)
        
    @_api_call
    @dumpArgs
    def get_room_details(self, roomId, p_showSipAddress = None):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v}
        endpoint = self.endpoint('rooms', roomId)
        return self.get(endpoint, params=params)
    
    @_api_call
    @dumpArgs
    def update_room(self, roomId, p_title = None):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v}
        endpoint = self.endpoint('rooms', roomId)
        return self.put(endpoint, json=params)
    
    @_api_call
    @dumpArgs
    def delete_room(self, roomId):
        endpoint = self.endpoint('rooms', roomId)
        return self.delete(endpoint)
    
    @dumpArgs
    def find_room(self, title):
        for r in self.list_rooms():
            if r['title'] == title:
                return r
        return None
        
    ############################# memberships
    
    @_pagination_iterator
    @dumpArgs
    def list_memberships(self, p_roomId = None, p_personId = None, p_personEmail = None, p_max = None):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('memberships')
        return (self, endpoint, params)
    
    @_api_call
    @dumpArgs
    def create_membership(self, p_roomId, p_personId = None, p_personEmail = None, p_isModerator = None):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('memberships')
        return self.post(endpoint, json=params)
    
    @_api_call
    @dumpArgs
    def get_membership_details(self, membership_id):
        endpoint = self.endpoint('memberships', membership_id)
        return self.get(endpoint)
    
    @_api_call
    @dumpArgs
    def update_membership(self, membership_id, p_isModerator):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('memberships', membership_id)
        return self.put(endpoint, json=params)
    
    @_api_call
    @dumpArgs
    def delete_membership(self, membership_id):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('memberships', membership_id)
        return self.delete(endpoint, json=params)
    
    ############################# messages
    
    @_pagination_iterator
    @dumpArgs
    def list_messages(self, p_roomId, p_before=None, p_beforeMessage=None, p_max=None):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('messages')
        return (self, endpoint, params)
    
    @_api_call
    @dumpArgs
    def create_message(self, p_roomId, p_text=None, p_files=None, p_file=None, p_toPersonId=None, p_toPersonEmail=None):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('messages')
        return self.post(endpoint, json=params)
    
    @_api_call
    @dumpArgs
    def get_message_details(self, message_id):
        endpoint = self.endpoint('messages', message_id)
        return self.get(endpoint)
    
    @_api_call
    @dumpArgs
    def delete_message(self, message_id):
        endpoint = self.endpoint('messages', message_id)
        return self.delete(endpoint)
    
    ############################# teams
    
    @_pagination_iterator
    @dumpArgs
    def list_teams(self, p_max = None):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('teams')
        return (self, endpoint, params)
    
    @_api_call
    @dumpArgs
    def create_team(self, p_name = None):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('teams')
        return self.post(endpoint, json=params)
    
    @_api_call
    @dumpArgs
    def get_team_details(self, team_id):
        endpoint = self.endpoint('teams', team_id)
        return self.get(endpoint)
    
    @_api_call
    @dumpArgs
    def update_team(self, team_id, p_name):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('teams', team_id)
        return self.put(endpoint, json=params)
    
    @_api_call
    @dumpArgs
    def delete_team(self, team_id):
        endpoint = self.endpoint('teams', team_id)
        return self.delete(endpoint)
    
    ############################# team memberships
    
    @_pagination_iterator
    @dumpArgs
    def list_team_memberships(self, p_teamId = None, p_max = None):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('team/memberships')
        return (self, endpoint, params)
    
    @_api_call
    @dumpArgs
    def create_team_membership(self, p_teamId, p_personId = None, p_personEmail = None, p_isModerator = None):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('team/memberships')
        return self.post(endpoint, json=params)
    
    @_api_call
    @dumpArgs
    def get_team_membership_details(self, membership_id):
        endpoint = self.endpoint('team/memberships', membership_id)
        return self.get(endpoint)
    
    @_api_call
    @dumpArgs
    def update_team_membership(self, membership_id, p_isModerator):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('team/memberships', membership_id)
        return self.put(endpoint, json=params)
    
    @_api_call
    @dumpArgs
    def delete_team_membership(self, membership_id):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('team/memberships', membership_id)
        return self.delete(endpoint, json=params)
    
    ############################# webhooks
    
    @_pagination_iterator
    @dumpArgs
    def list_webhooks(self, p_max=None):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('webhooks')
        return (self, endpoint, params)
    
    @_api_call
    @dumpArgs
    def create_webhook(self, p_name, p_targetUrl=None, p_resource=None, p_event=None, p_filter=None):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('webhooks')
        return self.post(endpoint, json=params)
    
    @_api_call
    @dumpArgs
    def get_webhook_details(self, webhook_id):
        endpoint = self.endpoint('webhooks', webhook_id)
        return self.get(endpoint)
    
    @_api_call
    @dumpArgs
    def update_webhook(self, webhook_id, p_name=None, p_targetUrl=None):
        params = {k[2:]:v for k,v in locals().items() if k[:2] == 'p_' and v != None}
        endpoint = self.endpoint('webhooks', webhook_id)
        return self.put(endpoint, json=params)
    
    @_api_call
    @dumpArgs
    def delete_webhook(self, webhook_id):
        endpoint = self.endpoint('webhooks', webhook_id)
        return self.delete(endpoint)