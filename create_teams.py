'''
Created on Jul 7, 2016

@author: jkrohn

Example code to read a file with user information and create teams based on the information read

Sample file:
Name    E-Mail Address    Department
Adam McKenzie    amckenzie@collabedge-162.dc-01.com    Engineering
Alex Jones    ajones@collabedge-162.dc-01.com    Sales

For each department found in the file a team is created with the coresponding users as mambers

'''
import logging
import os
import csv
import spark_api
from dump_utilities import set_mask_password
import json
import datetime

# this is the OAuth token to be used for the Spark API access
ACCESS_TOKEN = '<insert your token here>'

# define a logger to be used for logging info by this module
log = logging.getLogger(__name__)

def setup_logging():
    ''' set up logging for this module
    '''
    
    # log format for log messages written to a file
    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s %(name)-15s %(levelname)-8s %(message)s',
                        filename=os.path.splitext(__file__)[0] + '.log',
                        filemode='w')
    
    # define a Handler which writes INFO messages or higher to the sys.stderr
    # this implies that DEBUG messages are only written to the log file
    # the log file has all the details including:
    #    * API calls
    #    * HTTPS requests/responses
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    
    # set a format which is simpler for console use
    formatter = logging.Formatter('%(name)-15s: %(levelname)-8s %(message)s')
    # tell the handler to use this format
    console.setFormatter(formatter)
    
    # add the handler to the root logger
    logging.getLogger('').addHandler(console)
    
    # set some logging levels for loggers of imported modules
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('dump_utilities').setLevel(logging.DEBUG)
    logging.getLogger('spark_api').setLevel(logging.DEBUG)

def department_to_team_room(department):
    ''' map a department name to a team name
    
    we want to prefix the Department name with 'Team '
    '''
    return 'Team {}'.format(department)

def create_team(spark, department, users):
    ''' create team room
    
    parameters:
        spark: spark client API
        department: department name
        users: iterable with all users to be added to the team room
    '''
    
    # map department name to team name
    team_name = department_to_team_room(department)
    
    # try to create a team with that name and intercept errors raised by the Spark API
    try: 
        result = spark.create_team(p_name = team_name)
    except spark_api.APIError as e:
        log.error('Failed to create team {}: {}'.format(team_name, e))
        # if creating the team fails then there is no sense in trying to create the memberships
        return
    # if the team is created successfully then we find the ID of the created team in the result
    team_id = result['id']
    
    log.info('Created team {}, id {}'.format(team_name, team_id))
    
    # for information purposes dump the returned JSON
    for l in json.dumps(result, indent=2).splitlines():
        log.debug('Create team "{}" result: {}'.format(team_name, l))
    
    # now add all users to that team
    for user in users:
        # try to add as user and intercept spark API errors
        user_email = user['E-Mail Address']
        try:
            result = spark.create_team_membership(p_teamId = team_id, p_personEmail = user_email)
        except spark_api.APIError as e:
            log.error('Failed to add user {} to team {}: {}'.format(user_email, team_name, e))
        else:
            log.info('  Added user {} to team {}'.format(user_email, team_name))
    log.info('Done creating and adding users to team {}'.format(team_name))
    return
    
def read_csv():
    ''' read CSV with user information
    to keep it simple the file name is hardcoded
    '''
    # open the file as read/only
    with open('users.txt', 'r') as csv_file:
        # an iterator returning a Python dictionary for every row in the file.
        # The attribute names are taken from the 1st line of the
        reader = csv.DictReader(csv_file, delimiter = '\t')
        
        # read all lines and put the dictionaries in a list 
        users = [l for l in reader]

    return users

def setup_spark():
    ''' setup a Spark API instance
    
    returns the Spark API instance
    '''
    # the dump_utilities methods called by the Spark API for detailed logging of HTTPS requests can mask a password
    # it is required to set a password to be masked. As we don't use any passwords in this example we just
    # set some dummy string typically not present
    set_mask_password('.' * 20)
    
    spark = spark_api.SparkAPI(ACCESS_TOKEN)
    return spark
    
def create_teams():
    ''' the actual magic
    read user data from the CSV and create the teams
    '''
    # read lsit of users from CSV
    users = read_csv()
    
    # some Python magic to get a list of unique department names
    departments = (user['Department'] for user in users)
    departments = set(departments)
    departments = list(departments)
    
    # the same could be achieved in a single line:
    # departments = list(set((user['Department'] for user in users)))
    
    # we want the departments in sorted order
    departments.sort()
    
    # print some info
    print('Read {} users in {} departments'.format(len(users), len(departments)))
    
    # print list of departments
    print('Departments:\n  {}'.format('\n  '.join((d for d in departments))))
    
    # get a Spark API instance
    spark = setup_spark()
    
    # let's now create a team room for each department with the appropriate members
    for department in departments:
        create_team(spark, department, (user for user in users if department == user['Department']))
    print('Done')
 
def cleanup_teams():
    ''' delete all teams created by this script
    '''
    # get a Spark API instance
    spark = setup_spark()
    
    # get an iterator with all teams
    teams = spark.list_teams()
    
    # we only want the teams with names starting with the prefix we use to create teams
    teams = [team for team in teams if team['name'].startswith(department_to_team_room(''))]
    
    # also to be on the safe side we only delete teams created today
    today = spark_api.time_to_str(datetime.datetime.utcnow())[:11]
    teams = [team for team in teams if team['created'].startswith(today)]
    
    for team in teams:
        # 1st get list of memberships
        log.info('Deleting memberships for team {}'.format(team['name']))
        memberships = spark.list_team_memberships(p_teamId = team['id'])
        
        # try to delete all memberships and ignore Spark API errors
        for membership in memberships:
            try:
                spark.delete_team_membership(membership_id = membership['id'])
            except spark_api.APIError: pass
        
        # finally try to delete the team and ignore Spark API errors
        log.info('Deleting team {}'.format(team['name']))
        
        try:
            spark.delete_team(team_id = team['id'])
        except spark_api.APIError: pass
    return
if __name__ == '__main__':
    setup_logging()
    
    # we have code to create teams and to clean up "the mess"
    # uncomment whatever you want to do
    create_teams()
    #cleanup_teams()