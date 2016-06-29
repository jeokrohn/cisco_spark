#!/usr/bin/python3
'''
Created on 20.01.2016

@author: jkrohn

Iterate through all Spark rooms and copy all attachments to the local file system.

For each room a folder is created under a defined root folder
    * need to make sure that the folder names are compatible with the OS
    * as room names in Saprk can change saved state needs to contain a mapping room ID --> current name
        with that mapping the local folder can be renamed if the Spark room name changes
All attachments are saved in these folders
    * the modifid date of the files in the folders are set to the date/time the file was posted in Spark
    * if a file with the same name is posted multiple times then the older revisions get a file name with a timestamp. 
      Only the latest revision has the original name

Configuration required:
    * client ID and secret
    * user: id, email, password
    * base folder: 
'''
import logging
import configparser
import re
import datetime
import cgi
import os
import shutil
import json
import time

from dump_utilities import set_mask_password, dump_response
from identity_broker import SparkDevIdentityBroker, OAuthToken
import spark_api 

def setup_logging():
    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s %(name)-15s %(levelname)-8s %(message)s',
                        #datefmt='%m-%d %H:%M',
                        filename=os.path.splitext(__file__)[0] + '.log',
                        filemode='w')
    
    # define a Handler which writes INFO messages or higher to the sys.stderr
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    # set a format which is simpler for console use
    formatter = logging.Formatter('%(name)-15s: %(levelname)-8s %(message)s')
    # tell the handler to use this format
    console.setFormatter(formatter)
    # add the handler to the root logger
    logging.getLogger('').addHandler(console)
    
    # set some logging levels
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('dump_utilities').setLevel(logging.DEBUG)
    logging.getLogger('identity_broker').setLevel(logging.INFO)
    logging.getLogger('spark_api').setLevel(logging.DEBUG)
    

def valid_filename(s):
    s = s.strip().replace(' ', '_')
    return re.sub(r'(?u)[^-\w.]', '', s)  
        
def str_to_datetime(s):
    dt = datetime.datetime.strptime(s, '%Y-%m-%dT%H:%M:%S.%fZ')
    return dt

def get_attachments():
    
    def assert_folder(p_state, base_path, room_id, room_folder):
        ''' make sure that the folder is created for the room
        '''
        if not os.path.lexists(base_path):
            # base directory needs to be created
            logging.debug('Base directory %s does not exist' % base_path)
            os.mkdir(base_path)
        
        full_path = os.path.join(base_path, room_folder)
        
        if room_id not in p_state:
            p_state[room_id] = {}
        room_state = p_state[room_id]
        
        if 'folder' not in room_state:
            logging.debug('No previous folder for room %s' % room_folder)
            # the folder for this room hasn't been created before
            i = 0
            base_folder = room_folder
            while True:
                full_path = os.path.join(base_path, room_folder)
                try:
                    os.mkdir(full_path)
                    logging.debug('Created folder %s' % full_path)
                except FileExistsError:
                    # Folder exists, but not for this room?
                    # Try to find the room the folder has been created for
                    logging.debug('Folder {} already exists'.format(full_path))
                    r = next((r for r in p_state.values() if r.get('folder') == room_folder), None)
                    if r:
                        i = i + 1
                        room_folder = base_folder + str(i)
                        logging.debug('Room folder {} belongs to identified. Creating alternate name {} for new folder'.format(full_path, room_folder))
                        # we need to come up with a different folder name for the new folder
                        continue
                    else:
                        # this folder seems to be stale?
                        logging.debug('Folder {} seems to belong to no room. Renaming to {}'.format(full_path, full_path + '.stale'))
                        os.rename(full_path, full_path + '.stale')
                        os.mkdir(full_path)
                        logging.debug('Created folder %s' % full_path)
                break     
            # while
            
            # remember the folder name for this room
            room_state['folder'] =  room_folder
        else:
            # has the folder name been changed?
            if room_folder != room_state['folder']:
                logging.debug('Room name (folder) for room %s changed from %s to %s' % (room_id, room_state['folder'], room_folder))
                old_full_path = os.path.join(base_path, room_state['folder'])
                logging.debug('Renaming %s to %s' % (old_full_path, full_path))
                try:
                    os.rename(old_full_path, full_path)
                except FileNotFoundError:
                    logging.warning('Tried to rename folder {} but the folder did not exist'.format(old_full_path))
                    if os.path.lexists(full_path):
                        logging.warning('New folder {} exists. Assuming this is the correct folder'.format(full_path))
                    else:
                        logging.warning('New folder also does not exist. Potentially lost state!?')
                room_state['folder'] = room_folder
            # if room_folder != ...
            
            if not os.path.lexists(full_path):
                logging.debug('Folder %s does not exist and will be created' % full_path)
                os.mkdir(full_path)
        # we might have changed the folder name. So we return the potentially updated value 
        return room_folder
    
    def copy_attachment(p_state, base_path, room_id, room_folder, message, attachment_index, file_name, response):
        ''' read the attachment to a file
        '''
        message_id = message['id']
        message_created = message['created']
        
        # remove whitespaces from file_name
        (base, ext) = os.path.splitext(file_name)
        base = base.strip()
        ext = ext.strip()
        file_name = base + ext
        
        file_name = file_name.strip()
        full_path = os.path.join(base_path, room_folder)
        full_name = os.path.join(full_path, file_name)
        room_state = p_state[room_id]
        
        if 'messages' not in room_state:
            logging.debug('Initialize message state in room state')
            room_state['messages'] = {}
        messages_state = room_state['messages']
        
        if message_id not in messages_state:
            logging.debug('Initialize message state for message %s from %s' % (message_id, str_to_datetime(message_created).isoformat()))
            messages_state[message_id] = {'created' : message_created}
        message_state = messages_state[message_id]
        
        attachment_index = str(attachment_index).strip()
        if attachment_index not in message_state:
            logging.debug('New attachment. Message %s from %s, index %s, file \'%s\'' % (message_id, message_created, attachment_index, file_name))
            # record the file name for this attachment
            if os.path.exists(full_name):
                logging.debug('File \'%s\' already exists' % file_name)
                # Find the message and index which currently uses this name
                # The Mac OS X file system in case preserving but case insensitive so "attachment.png" and "Attachment.png" are the 'same'
                # we have to consider that when searching for the message which references to a given file name: the check needs to be case insensitive 
                class UpdateDone(Exception): pass
                try:
                    for _, ms in messages_state.items():
                        for idx in ms:
                            if ms[idx].lower() == file_name.lower():
                                # this is the existing entry
                                logging.debug('Existing file \'%s\' belongs to message from %s' % (ms[idx], str_to_datetime(ms['created']).isoformat()))
                                # the older file needs to be renamed
                                if message_created > ms['created']:
                                    logging.debug('This attachment seems to be newer. This: %s, existing: %s' % 
                                                  (str_to_datetime(message_created).isoformat(), str_to_datetime(ms['created']).isoformat()))
                                    logging.debug('Existing file needs to be renamed')
                                    (base, ext) = os.path.splitext(ms[idx])
                                    new_name = base + '_' + str_to_datetime(ms['created']).strftime('%Y%m%d%H%M%S') + '-' + str(attachment_index).strip() + ext
                                    logging.debug('File will be renamed to \'%s\'' % new_name)
                                    os.rename(os.path.join(full_path, ms[idx]), os.path.join(full_path, new_name))
                                    ms[idx] = new_name
                                else:
                                    logging.debug('This attachment seems to be older. This: %s, existing: %s' % 
                                                  (str_to_datetime(message_created).isoformat(), str_to_datetime(ms['created']).isoformat()))
                                    logging.debug('This attachment needs to be saved under a different name')
                                    (base, ext) = os.path.splitext(file_name)
                                    file_name = base + '_' + str_to_datetime(message_created).strftime('%Y%m%d%H%M%S') + '-' + str(attachment_index).strip() + ext
                                    full_name = os.path.join(base_path, room_folder, file_name)
                                    logging.debug('Attachment will be saved as %s instead' % file_name)
                                raise UpdateDone
                            # if ms[idx] ..
                        # for idx in ms:
                    # for _, ms in messages_state.items():
                    logging.warning('File \'%s\' exists, but message this attachment belongs to could not be found' % full_name)
                    logging.warning('.. renaming to {}'.format(full_name + '.stale'))
                    os.rename(full_name, full_name + '.stale')
                except UpdateDone: pass
            else:
                # the file does not exist. For sanity reasons remove all references to attachments with the same name from the message state
                # reason: user might have "cleaned up" the attachment repository on the file system and deleted a file
                for _, ms in messages_state.items():
                    for idx in list(ms.keys()):
                        if ms[idx].lower() == file_name.lower():
                            logging.debug('Found stale message state for file %s from %s. Removing state..' % (ms[idx], str_to_datetime(ms['created']).isoformat()))
                            del ms[idx]
            # now finally copy the file
            logging.info('      Downloading attachment to \'%s\'' % full_name)
            with open(full_name, 'wb') as f:
                response.raw.decode_content = True
                shutil.copyfileobj(response.raw, f)
            # set access and last modified date
            f_time = str_to_datetime(message_created).timestamp()
            os.utime(full_name, (f_time, f_time))
            
            message_state[attachment_index] = file_name
        else:
            logging.debug('Attachment already downloaded. Message %s from %s, index %s, file \'%s\' as \'%s\'' % 
                          (message_id, message_created, attachment_index, file_name, message_state[attachment_index]))
            logging.info('      Already downloaded. Skipping file...')  
        return
    
    def check_new_activity(p_state, room):
        ''' check whether there is new activity in the room
        returns:
            None - no new activity
            '' - all messsages in the room are new
            <datestring> - date/time of last activity. Only newer activities need to be considered
        '''
        room_id = room['id']
        last_activity = room['lastActivity']
        
        if room_id in p_state:
            last_seen = p_state[room_id].get('lastActivity', '')
            if last_activity != p_state[room_id].get('lastActivity', ''):
                logging.debug('New activity in room: last seen %s, now %s' % (last_seen, last_activity))
                # p_state[room_id]['lastActivity'] = last_activity
                return last_seen
            else:
                logging.debug('No new activity in room: last seen %s' % last_seen)
                return None
        else:
            # p_state[room_id] = {'lastActivity' : last_activity}
            logging.debug('New activity in room. Room never tested before')
            return ''
    
    def set_last_activity(p_state, room, activity):
        ''' sets 'lastActivity' for the given rooom in p_state
        '''
        room_id = room['id']
        logging.debug('Setting last activity for room to: {}'.format(activity))
        if room_id in p_state:
            p_state[room_id]['lastActivity'] = activity
        else:
            p_state[room_id] = {'lastActivity': activity}
        return
    
    setup_logging()
    
    spark_config = configparser.ConfigParser()
    spark_config.read('spark.ini')
    
    set_mask_password(spark_config['user']['password'])
    ib = SparkDevIdentityBroker()
    oauth_token = OAuthToken(ib, spark_config['user'], spark_config['client'])
    
    spark = spark_api.SparkAPI(oauth_token)
    
    att_config = configparser.ConfigParser()
    att_config.read(os.path.splitext(__file__)[0] + '.ini')
    
    base_path = os.path.abspath(os.path.expanduser(att_config['path']['base']))
    
    state_file = os.path.splitext(__file__)[0] + '.json'
    try:
        f = open(state_file, 'r')
    except IOError:
        logging.debug('Did not find saved state in file %s' % state_file)
        p_state = {}
    else:
        logging.debug('Reading saved state from file %s' % state_file)
        p_state = json.load(f)
        f.close()
    
    logging.info('Getting list of rooms...')
    try:
        rooms = list(spark.list_rooms())
    except spark_api.APIError as e:
        try:
            logging.error('Error getting rooms: %s' % e.args[2]['message'])
        except Exception:
            logging.error('Error getting rooms: %s' % e.args[2])
        rooms = []
    logging.info('Found {} rooms'.format(len(rooms)))
    
    try:
        for room in rooms:
            room_id = room['id']
            # in case the room doesn't have a title we use the room ID as fallback
            room_folder = valid_filename(room.get('title', room_id))
            
            logging.info('Checking room \'%s\'' % room_folder)
            logging.debug('ID: %s, %s' % (room_id, spark_api.base64_id_to_str(room_id)))
            last_activity = check_new_activity(p_state, room)
            if last_activity == None:
                logging.info('No new activity. Skipping room')
                continue
            
            # iterate through all messages with attachments
            def get_messages_with_attachments(room_id, last_activity):
                ''' get all messages with attachment of given room newer than last_activity
                '''
                # if we never read the room try to read messages in bigger chunks
                max_messages = 200 if not last_activity else 50
                for m in spark.list_messages(room_id, p_max=max_messages):
                    if m['created'] <= last_activity:
                        logging.debug('Got last message after last checked activity. Last activity %s, this message %s' % (str_to_datetime(last_activity).isoformat(), str_to_datetime(m['created']).isoformat()))
                        break
                    if 'files' in m:
                        # only collect messages with attachments
                        yield m
                return
                
            try:
                messages = get_messages_with_attachments(room_id, last_activity)
            
                '''if not messages:
                    logging.info('  No new messages with attachments in room')
                '''
                for message in messages:
                    message_created = str_to_datetime(message['created'])
                    logging.info('  %s: Message with %s attachments.' % (message_created.isoformat(), len(message['files'])))
                    
                    for attachment_index in range(len(message['files'])):
                        attachment = message['files'][attachment_index]
                        
                        class DownloadError(Exception): pass
                        
                        try:
                            back_off = 1
                            while True:
                                logging.debug('  Getting attachment {} from {}'.format(attachment_index, attachment))
                                
                                # we set the dump_utilities log level to INFO to avoid hick-ups from trying to log the content
                                # the current log level will be set back to the original value after
                                level = logging.getLogger('dump_utilities').getEffectiveLevel()
                                logging.getLogger('dump_utilities').setLevel(logging.INFO)
                                
                                response = spark.get(attachment, stream=True)
                                
                                logging.getLogger('dump_utilities').setLevel(level)
                                dump_response(response, dump_body=False)
                                
                                # sometimes we don't get the attachment and instead a JSON error message is returned
                                cd_header = response.headers.get('content-disposition', None)
                                if cd_header == None:
                                    try:
                                        js = response.json()
                                        logging.error('Error downloading from room {}, time {}, error message: {}'.format(room_folder, message_created.isoformat(), js.get('message', 'Unknown problem: %s' % js)))
                                    except Exception:
                                        logging.error('Error downloading from room {}, time {}. No content-disposition header and no JSON found. Headers: {}'.format(room_folder, message_created.isoformat(), response.headers))
                                        raise DownloadError
                                    response.close()
                                    if back_off > 32: raise DownloadError
                                    logging.info('  Waiting for {} seconds before retrying...'.format(back_off))
                                    time.sleep(back_off)
                                    back_off = back_off * 2
                                    continue
                                break
                        except DownloadError:
                            break
                        
                        _, params = cgi.parse_header(cd_header)
                        file_name = params['filename']
                    
                        size = response.headers.get('content-length', None)
                        size = 'n/a' if size == None else int(size)
                        logging.info('    File \'%s\', length: %s' % (file_name, size))
                        
                        # copy the file to the appropriate folder
                        room_folder = assert_folder(p_state, base_path, room_id, room_folder)
                        copy_attachment(p_state, base_path, room_id, room_folder, message, attachment_index, file_name, response)
                        response.close()
                    # for attachment in message['files']:
                    
                    # when done with a message set the last activity state for the current room
                    set_last_activity(p_state, room, message['created'])
                # for message in messages:
                
                # when done with all message in the room set the last activity state for the current_room
                set_last_activity(p_state, room, room['lastActivity'])
            except spark_api.APIError as e:
                try:
                    logging.error('Error getting messages from room %s: %s' % (room_folder, e.info.get('message', 'unknown error')))
                except Exception:
                    logging.error('Error getting messages from room %s: %s' % (room_folder, e.info))
                messages = []
            
            logging.debug('Saving state to file %s' % state_file)
            f = open(state_file, 'w')
            json.dump(p_state, f, indent = 4)
            f.close()
                
    except Exception:
        logging.debug('Saving state to file %s' % state_file)
        f = open(state_file, 'w')
        json.dump(p_state, f, indent = 4)
        f.close()
        raise
    # Setting the last modified date of the folders in line with the latest attachment in the room is a nice idea
    for room_state in (r for r in p_state.values() if 'folder' in r):
        folder = os.path.join(base_path, room_state['folder'])
        dates = [m['created'] for m in room_state.get('messages', {}).values()]
        dates.sort()
        latest = dates[-1]
        f_time = str_to_datetime(latest).timestamp()
        try:
            os.utime(folder, (f_time, f_time))
        except Exception as e:
            logging.error('Error setting timestamp of folder {}:{}'.format(folder, e))
    return

if __name__ == '__main__':
    get_attachments()