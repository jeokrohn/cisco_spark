#de.jkrohn.python.spark

##This is my 'playground' for playing with the Cisco Spark APIs using Python 3

* api_test.py: testing the public APIs
* dump_utilities.py: helper to dump HTTPS requests and responses to log files
* identity_broker.py: Handle OAuth authentication flows to obtain OAuth tokens. Currently i'm using the auth_code grant flow using end user credentials
* spark_api.py: class offering access to the public Spark APIs as documented at https://developer.ciscospark.com
* spark_errors.py: common exception classes
* spark_struct.py: helper class to map dictionaries to classes
* get_attachments.py: application using above classes. The purpose of this script is to browse through all rooms and download all attachments from these rooms. The downloaded attachments are stored in a local directory structure with one folder for each room.
* create_teams.py: example script creating teams and team memberships based on information read from a CSV
* users.txt: example file for create_teams.py

Right now the focus is on the 'public' APIs implemented in spark\_api.py. Some test code in that files is called from api\_test.py.
