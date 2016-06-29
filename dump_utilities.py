'''
Created on 07.12.2015

@author: jkrohn
'''
from urllib.parse import urlparse, parse_qsl
import xml.dom.minidom
import logging
import json

log = logging.getLogger(__name__)

pwd = None
def set_mask_password(password):
    global pwd
    pwd = password

def print_pwd(text):
    global pwd
    if not pwd: raise Exception('Before using dump_utilities a password to be masked in the output needs to be set by calling "set_mask_password"')
    try:
        text = text.replace(pwd, '*' * len(pwd))
        text = text.encode('ascii', 'replace')
        text = text.decode('ascii')
    except Exception: pass
    log.debug(text)
    
def dump_req_body(request):
    if not request.body: return
    log.debug('Request body:')
    for l in (l for l in request.body.splitlines() if l): print_pwd('  %s' % l)
    if request.headers['content-type'] == 'application/x-www-form-urlencoded':
        print_pwd('  Form data:')
        query = parse_qsl(request.body, keep_blank_values=True)
        for k,v in query:
            print_pwd('    %s=%s' % (k,v))

def print_header(header, value):
    if header == 'Authorization':
        value = value[:20] + '...'
    print_pwd('  >%s: %s<' % (header, value))
    if header == 'location':
        parsed_url = urlparse(value)
        print_pwd('    netloc: %s' % parsed_url.netloc)
        print_pwd('    path: %s' % parsed_url.path)
        print_pwd('    params: %s' % parsed_url.params)
        print_pwd('    query: %s' % parsed_url.query)
        if parsed_url.query:
            for k,v in parse_qsl(parsed_url.query, keep_blank_values=True):
                print_pwd('      %s=%s' % (k, v))
    
def dump_request(request):
    log.debug('\n' + '=' * 10 + 'Request start')
    log.debug('Method: %s' % request.method)
    log.debug('Request URL: %s' % request.url)
    url = urlparse(request.url)
    if url.query:
        log.debug('  Query:')
        for k, v in parse_qsl(url.query, keep_blank_values=True):
            log.debug('    %s=%s' % (k, v))
    log.debug('Request headers:')
    for h in request.headers: print_header(h, request.headers[h])
    dump_req_body(request)
    log.debug('=' * 10 + ' Request end')

def dump_resp_body(response):
    if not response.content: return
    
    log.debug('Response content:')
    if response.headers['content-type'].startswith('text/html'):
        try:
            xml_p = xml.dom.minidom.parseString(response.text)
        except Exception:
            for l in (l for l in response.text.splitlines() if l.strip()): print_pwd('    %s' % l)
        else:
            for l in (l for l in xml_p.toprettyxml().splitlines() if l.strip()): print_pwd('    %s' % l)
        return
    if response.headers['content-type'].startswith('application/json'):
        #print('    %s' % response.json())
        s = json.dumps(response.json(), indent=2)
        class DumpStop(Exception): pass
        line = 1
        try:
            for l in (l for l in s.splitlines() if l.strip()):
                line = line + 1
                if line == 10: raise DumpStop
                log.debug('    {}'.format(l))
        except DumpStop:
            log.debug('    ...')
        #log.debug('    %s' % response.text.strip()[:100])
        return
    print_pwd(response.content)
   
def dump_response(response, dump_history=True, force = False, dump_body=True):
    ''' Dump all 'relevant' information from a requests response
    '''
    if not log.isEnabledFor(logging.DEBUG): return
    if dump_history:
        for r in response.history: dump_response(r, dump_history=False, force=force)
    dump_request(response.request)
    log.debug('=' * 10 + 'Response start')
    log.debug('Status Code: %s %s' % (response.status_code, response.reason))
    log.debug('Response headers:')
    for h in response.headers: print_header(h, response.headers[h])
    if dump_body: dump_resp_body(response)
    log.debug('=' * 10 + ' Response end') 
