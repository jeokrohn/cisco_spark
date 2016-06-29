'''
Created on 07.12.2015

@author: jkrohn

Tests for simple Spark APIs
'''
import logging
import configparser
import json

from dump_utilities import set_mask_password, dump_response
from identity_broker import SparkDevIdentityBroker, OAuthToken
from spark_api import SparkAPI

def test():
    # set up logging
    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
                        #datefmt='%m-%d %H:%M',
                        filename='./api_test.log',
                        filemode='w')
    # define a Handler which writes INFO messages or higher to the sys.stderr
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    # set a format which is simpler for console use
    formatter = logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s')
    # tell the handler to use this format
    console.setFormatter(formatter)
    # add the handler to the root logger
    logging.getLogger('').addHandler(console)
    
    # disable some logging
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('dump_utilities').setLevel(logging.DEBUG)
    logging.getLogger('identity_broker').setLevel(logging.DEBUG)
    
    config = configparser.ConfigParser()
    config.read('spark.ini')
    set_mask_password(config['user']['password'])
    ib = SparkDevIdentityBroker()
    oauth_token = OAuthToken(ib, config['user'], config['client'], scope = 'spark:people_read spark:rooms_read spark:rooms_write spark:memberships_read spark:memberships_write spark:messages_read spark:messages_write')
    
    spark = SparkAPI(oauth_token)
    #spark.test_room_api()
    #spark.test_people_api()
    #spark.test_message_api()
    #spark.test_membership_api()
    room = spark.find_room('Ask Spark Features')
    print(json.dumps(room, indent = 2))
    detail = spark.get_room_details(room['id'], p_showSipAddress = True)
    print(json.dumps(detail, indent = 2))
    return
      
if __name__ == '__main__':
    test()