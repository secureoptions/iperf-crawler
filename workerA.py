import boto3
from vars import STATE_MACHINE_ARN, REGION, EC2_REGION, A_ACTIVITY_ARN, SG_ID, IPERF_FLAGS, SUBNETS, MTR_FLAGS, GROUP, PARENT_ACCOUNT
from subprocess import Popen, PIPE, STDOUT
import urllib2
import json
import datetime
import time
import re

# set up clients with appropriate credentials to make calls across accounts
sts = boto3.client('sts')

assume_role = sts.assume_role(
	RoleArn='arn:aws:iam::%s:role/IperfCrawlerEc2' % PARENT_ACCOUNT,
	RoleSessionName='iperf_worker'
	)

stepfunctions = boto3.client('stepfunctions',
				aws_access_key_id=assume_role['Credentials']['AccessKeyId'],
				aws_secret_access_key=assume_role['Credentials']['SecretAccessKey'],
				aws_session_token=assume_role['Credentials']['SessionToken'],	
				region_name = REGION
				)
logs = boto3.client('logs',
				aws_access_key_id=assume_role['Credentials']['AccessKeyId'],
				aws_secret_access_key=assume_role['Credentials']['SecretAccessKey'],
				aws_session_token=assume_role['Credentials']['SessionToken'],	
				region_name = REGION
				)
sdb = boto3.client('sdb',
				aws_access_key_id=assume_role['Credentials']['AccessKeyId'],
				aws_secret_access_key=assume_role['Credentials']['SecretAccessKey'],
				aws_session_token=assume_role['Credentials']['SessionToken'],	
				region_name = REGION
				)			

SECONDARY_ACCOUNT = sts.get_caller_identity()
SECONDARY_ACCOUNT = SECONDARY_ACCOUNT['Account']

if PARENT_ACCOUNT != SECONDARY_ACCOUNT:
	assume_role = sts.assume_role(
	RoleArn='arn:aws:iam::%s:role/IperfCrawlerEc2' % SECONDARY_ACCOUNT,
	RoleSessionName='iperf_worker2'
	)
				
ec2 = boto3.client('ec2', 
				aws_access_key_id=assume_role['Credentials']['AccessKeyId'],
				aws_secret_access_key=assume_role['Credentials']['SecretAccessKey'],
				aws_session_token=assume_role['Credentials']['SessionToken'],
				region_name = EC2_REGION
				)

LOCAL_PUBLIC_IP = urllib2.urlopen('http://169.254.169.254/latest/meta-data/public-ipv4').read()
LOCAL_PRIVATE_IP = urllib2.urlopen('http://169.254.169.254/latest/meta-data/local-ipv4').read()
SUBNETS = SUBNETS.split(',')

		
# function to put test results and messages in Cloudwatch
def update_results(message,command,text):
		epoch = datetime.datetime.utcfromtimestamp(0)
		
		def unix_time_millis(dt):
			return (dt - epoch).total_seconds() * 1000.0
			
		WORKER_A_AZ = sdb.get_attributes(
			DomainName='iperf-crawler',
			ItemName=SUBNETS[0],
			AttributeNames=['AvailabilityZone']
				)

		
		WORKER_B_AZ = sdb.get_attributes(
				DomainName='iperf-crawler',
				ItemName=SUBNETS[1],
				AttributeNames=['AvailabilityZone']
					)
					
		
		result_to_send= ('######## %s CLIENT RESULTS FROM %s | %s #####\n'
						'# Traffic Direction: %s ---> %s\n' 
						'# Availability Zones: %s ---> %s\n'
						'# Command Executed: %s\n'
						'############################################################################\n'
						'\n'
						'%s') % (text, WORKER_A_AZ['Attributes'][0]['Value'], SUBNETS[0], SUBNETS[0], SUBNETS[1], WORKER_A_AZ['Attributes'][0]['Value'], WORKER_B_AZ['Attributes'][0]['Value'], command, message)
			
		try:
			SEQ_TOKEN = logs.describe_log_streams(
				logGroupName='Iperf-Crawler',
				logStreamNamePrefix=GROUP
			)
			
		
			SEQ_TOKEN = SEQ_TOKEN['logStreams'][0]['uploadSequenceToken']
			logs.put_log_events(
				logGroupName='Iperf-Crawler',
				logStreamName=GROUP,
				logEvents=[
					{
						'timestamp': int(unix_time_millis(datetime.datetime.now())),
						'message': result_to_send
					},
				],
				sequenceToken=SEQ_TOKEN
			)
		except:
			logs.put_log_events(
				logGroupName='Iperf-Crawler',
				logStreamName=GROUP,
				logEvents=[
					{
						'timestamp': int(unix_time_millis(datetime.datetime.now())),
						'message': result_to_send
					},
				],
			)


def get_activity_task():
	response = stepfunctions.get_activity_task(
		activityArn=A_ACTIVITY_ARN
	)

	return response


# This machine is identified as side 'A'. It will be responsible for initiating state execution, and providing its metadata as initial input to the state
execution = stepfunctions.start_execution(
	stateMachineArn=STATE_MACHINE_ARN,
	# Here we need to update state with local metadata. This will be used by side 'B' to perform its tasks
	input="{\"SideAPrivateIp\" : \"%s\", \"SideAPublicIp\" : \"%s\"}" % (LOCAL_PRIVATE_IP, LOCAL_PUBLIC_IP)
)

execution_arn = execution['executionArn']


for subnet in SUBNETS:
	sdb.put_attributes(
		DomainName='iperf-crawler',
		ItemName=subnet,
		Attributes=[
			{
				'Name': 'ExecutionArn',
				'Value': execution_arn
			}
		]
		)

# Retrieve Side B's metadata from the state machine input, and use it to create security group rules
response = get_activity_task()
TASK_TOKEN = response['taskToken']

# Convert the returned input 'string' to json format
response = json.loads(response['input'])

# Get variables for input
SIDE_B_PRIVATE_IP = response['SideBPrivateIp']
SIDE_B_PUBLIC_IP = response['SideBPublicIp']

# Create security group rules for both, side A's private and public IPs

try:
	ec2.authorize_security_group_ingress(
		CidrIp = SIDE_B_PRIVATE_IP + '/32', 
		GroupId = SG_ID, 
		IpProtocol = '-1'
		) 
	ec2.authorize_security_group_ingress(
		CidrIp = SIDE_B_PUBLIC_IP + '/32', 
		GroupId = SG_ID, 
		IpProtocol = '-1'
		)
except Exception as e:
	stepfunctions.send_task_failure(
		taskToken=TASK_TOKEN,
		error=e,
		cause='There was an issue with creating inbound rules in %s' % SG_ID
	)
		

# Now start the iperf3 server on this machine, and update state so that side B knows it can start the iperf3 client
Popen(["iperf3","-s","-p","5201","&"])

stepfunctions.send_task_success(
    taskToken=TASK_TOKEN,
    output="{}"
)

# B side client has finished running iperf3. Run iperf3 client from side A now
response = get_activity_task()
TASK_TOKEN = response['taskToken']

# Convert the returned input 'string' to json format
response = json.loads(response['input'])
TARGET_IP = response['TargetIp']

try: 	
	Popen(['killall','iperf3'])
	CMD = '%s %s' % (IPERF_FLAGS, TARGET_IP)

	p = Popen(CMD, shell=True, stdin=PIPE, stdout=PIPE, stderr=STDOUT, close_fds=True).stdout.read()
	update_results(p,CMD,'IPERF')
	
	# finally update the state machine so that side A can run as client
	stepfunctions.send_task_success(
		taskToken=TASK_TOKEN,
		output="{}"
		)
except Exception as e:
		stepfunctions.send_task_failure(
			taskToken=TASK_TOKEN,
			error=e,
			cause='It appears that there was an issue with running iperf3 client or server on %s, or the results were unable to be pushed to Cloudwatch.' % LOCAL_PRIVATE_IP
			)

# Finally run an MTR report to the target ip. This does not require sync between the EC2s
response = get_activity_task()
TASK_TOKEN = response['taskToken']
try:
	CMD = '%s %s' % (MTR_FLAGS, TARGET_IP)
	p = Popen(CMD, shell=True, stdin=PIPE, stdout=PIPE, stderr=STDOUT, close_fds=True).stdout.read()
	update_results(p,CMD,'MTR')
	
	stepfunctions.send_task_success(
		taskToken=TASK_TOKEN,
		output="{}"
		)
		
	sdb.put_attributes(
		DomainName='iperf-crawler',
		ItemName=SUBNETS[0],
		Attributes=[
			{
				'Name': 'FinishStatus',
				'Value': 'Completed',
				'Replace': True
			}
		]
		)
except Exception as e:
		stepfunctions.send_task_failure(
			taskToken=TASK_TOKEN,
			error=e,
			cause='Side A was unable to run MTR'
			)
		sdb.put_attributes(
			DomainName='iperf-crawler',
			ItemName=SUBNETS[1],
			Attributes=[
				{
					'Name': 'FinishStatus',
					'Value': 'Completed',
					'Replace': True
				}
			]
			)
