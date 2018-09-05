import boto3
from vars import B_ACTIVITY_ARN, SG_ID, IPERF_FLAGS, MTR_FLAGS, SUBNETS, REGION, EC2_REGION, GROUP, PARENT_ACCOUNT
import json
from subprocess import Popen, PIPE, STDOUT
import urllib2
import botocore
import datetime
import time

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



# create a new log stream to log results of iperf3 tests between side A and side BaseException
try:
	logs.create_log_stream(
    		logGroupName='Iperf-Crawler',
    		logStreamName=GROUP
	)
except botocore.exceptions.ClientError as e:
	if e.response['Error']['Code'] == 'ResourceAlreadyExistsException':
		pass
		
		
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
						'%s') % (text, WORKER_B_AZ['Attributes'][0]['Value'], SUBNETS[1], SUBNETS[1], SUBNETS[0], WORKER_B_AZ['Attributes'][0]['Value'], WORKER_A_AZ['Attributes'][0]['Value'], command, message)
		
		
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
		activityArn=B_ACTIVITY_ARN

	)

	return response

		
# Wait for ec2 metadata from side 'A', and then create security group entry accordingly below. 
response = get_activity_task()
TASK_TOKEN = response['taskToken']

# Convert the returned input 'string' to json format
response = json.loads(response['input'])

# Get variables for input
SIDE_A_PRIVATE_IP = response['SideAPrivateIp']
SIDE_A_PUBLIC_IP = response['SideAPublicIp']

# Create security group rules for both, side A's private and public IPs
try:
	ec2.authorize_security_group_ingress(
		CidrIp = SIDE_A_PRIVATE_IP + '/32', 
		GroupId = SG_ID, 
		IpProtocol = '-1'
		) 
	ec2.authorize_security_group_ingress(
		CidrIp = SIDE_A_PUBLIC_IP + '/32', 
		GroupId = SG_ID, 
		IpProtocol = '-1'
		) 

	
	# Update the state machine with B's metadata
	stepfunctions.send_task_success(
		taskToken=TASK_TOKEN,
		output="{\"SideBPrivateIp\" : \"%s\", \"SideBPublicIp\" : \"%s\"}" % (LOCAL_PRIVATE_IP, LOCAL_PUBLIC_IP)
	)

except Exception as e:
	stepfunctions.send_task_failure(
		taskToken=TASK_TOKEN,
		error=e,
		cause='There was an issue with creating inbound rules in %s' % SG_ID
	)
		
	

# Now wait for next step
response = get_activity_task()
TASK_TOKEN = response['taskToken']

# Side A's SG should be open to us now. Let's try to see if A's private IP is reachable. If it is not then use its public IP instead for iperf3 test

p = Popen(["ping","-c","3","-W","2",SIDE_A_PRIVATE_IP])
p.wait()
if p.poll():
	TARGET_IP = SIDE_A_PUBLIC_IP
	LOCAL_IP = LOCAL_PUBLIC_IP
else:
	TARGET_IP = SIDE_A_PRIVATE_IP
	LOCAL_IP = LOCAL_PRIVATE_IP

	
# Run the iperf3 client with the user-defined flags (specified in Cloudformation)
try: 
	CMD = '%s %s' % (IPERF_FLAGS, TARGET_IP)	
	p = Popen(CMD, shell=True, stdin=PIPE, stdout=PIPE, stderr=STDOUT, close_fds=True).stdout.read()
	update_results(p,CMD,'IPERF')
	
	# start the iperf3 server on 'B' now since it has finished running client tests
	Popen(["iperf3","-s","-p","5201","&"])

    # finally update the state machine so that side A can run as client
	stepfunctions.send_task_success(
		taskToken=TASK_TOKEN,
		output="{\"TargetIp\" : \"%s\"}" % LOCAL_IP
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
	Popen(["killall","iperf3"])
	CMD = '%s %s' % (MTR_FLAGS, TARGET_IP)
	p = Popen(CMD, shell=True, stdin=PIPE, stdout=PIPE, stderr=STDOUT, close_fds=True).stdout.read()
	update_results(p,CMD,'MTR')
	
	stepfunctions.send_task_success(
		taskToken=TASK_TOKEN,
		output="{}"
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






