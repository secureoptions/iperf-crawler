import boto3
from vars import B_ACTIVITY_ARN, SG_ID, IPERF_FLAGS, MTR_FLAGS, SUBNETS, REGION, GROUP
import json
from subprocess import Popen, PIPE, STDOUT
import botocore
from ec2_metadata import ec2_metadata
import datetime
import time

# set needed boto3 clients
ec2 = boto3.client('ec2', region_name = ec2_metadata.region)
stepfunctions = boto3.client('stepfunctions', region_name = REGION)
logs = boto3.client('logs', region_name = REGION)
sdb = boto3.client('sdb', region_name = REGION)
SUBNETS = SUBNETS.split(',')


# set workerB's ReadyCheck attribute to True so workerA knows workerB is ready to begin stepfunctions
sdb.put_attributes(
    DomainName='iperf-crawler',
    ItemName=SUBNETS[1],
    Attributes=[
        {
            'Name': 'ReadyCheck',
            'Value': 'True',
			'Replace':True
        }
    ]
)

# now wait and check SimpleDB every 5 seconds for workerA to signal its ready as well
while True:
	response = sdb.get_attributes(
					DomainName='iperf-crawler',
					ItemName=SUBNETS[0],
					AttributeNames=['ReadyCheck'],
					)
	if response['Attributes'][0]['Value'] == 'True':
		# workerA is ready! Let's go
		break
	else:
		time.sleep(5)


# create a new log stream to log results of iperf3 tests between side A and side BaseException
try:
	logs.create_log_stream(
    		logGroupName='Iperf-Crawler',
    		logStreamName='%s:%s <--> %s' % (SUBNETS[0],SUBNETS[1])
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
				logStreamNamePrefix='%s:%s <--> %s' % (SUBNETS[0],SUBNETS[1])
			)
			
		
			SEQ_TOKEN = SEQ_TOKEN['logStreams'][0]['uploadSequenceToken']
			logs.put_log_events(
				logGroupName='Iperf-Crawler',
				logStreamName='%s:%s <--> %s' % (SUBNETS[0],SUBNETS[1]),
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
				logStreamName='%s:%s <--> %s' % (SUBNETS[0],SUBNETS[1]),
				logEvents=[
					{
						'timestamp': int(unix_time_millis(datetime.datetime.now())),
						'message': result_to_send
					},
				],
			)


def get_activity_task(name):
	response = stepfunctions.get_activity_task(
		activityArn=B_ACTIVITY_ARN,
		workerName=name
	)

	return response

		
# Wait for ec2 metadata from side 'A', and then create security group entry accordingly below. 
response = get_activity_task('Side B get Side A\'s EC2 Metadata')
TASK_TOKEN = response['taskToken']

# Convert the returned input 'string' to json format
response = json.loads(response['input'])

# Get variables for input
SIDE_A_PRIVATE_IP = response['SideAPrivateIp']
SIDE_A_PUBLIC_IP = response['SideAPublicIp']

# Create security group rules for both, side A's private and public IPs
try:
	ec2.authorize_security_group_ingress(CidrIp = SIDE_A_PRIVATE_IP + '/32', FromPort = 5201, GroupId = SG_ID, IpProtocol = 'tcp', ToPort = 5201) 
	ec2.authorize_security_group_ingress(CidrIp = SIDE_A_PUBLIC_IP + '/32', FromPort = 5201, GroupId = SG_ID, IpProtocol = 'tcp', ToPort = 5201) 

	ec2.authorize_security_group_ingress(CidrIp = SIDE_A_PRIVATE_IP + '/32', FromPort =-1, GroupId = SG_ID, IpProtocol = 'icmp', ToPort = -1 )
	ec2.authorize_security_group_ingress(CidrIp = SIDE_A_PUBLIC_IP + '/32', FromPort =-1, GroupId = SG_ID, IpProtocol = 'icmp', ToPort = -1 )
	
	# Update the state machine with B's metadata
	stepfunctions.send_task_success(
		taskToken=TASK_TOKEN,
		output="{\"SideBPrivateIp\" : \"%s\", \"SideBPublicIp\" : \"%s\"}" % (ec2_metadata.private_ipv4, ec2_metadata.public_ipv4)
	)

except Exception as e:
	stepfunctions.send_task_failure(
		taskToken=TASK_TOKEN,
		error=e,
		cause='There was an issue with creating inbound rules in %s' % SG_ID
	)
		
	

# Now wait for next step
response = get_activity_task('Side B notified that server A is ready for iperf3 test. Executing')
TASK_TOKEN = response['taskToken']

# Side A's SG should be open to us now. Let's try to see if A's private IP is reachable. If it is not then use its public IP instead for iperf3 test

p = Popen(["ping","-c","3","-W","2",SIDE_A_PRIVATE_IP])
p.wait()
if p.poll():
	TARGET_IP = SIDE_A_PUBLIC_IP
	LOCAL_IP = ec2_metadata.public_ipv4
else:
	TARGET_IP = SIDE_A_PRIVATE_IP
	LOCAL_IP = ec2_metadata.private_ipv4

	
# Run the iperf3 client with the user-defined flags (specified in Cloudformation)
try: 
	CMD = 'iperf3 -c %s %s' % (IPERF_FLAGS, TARGET_IP)	
	p = Popen(CMD, shell=True, stdin=PIPE, stdout=PIPE, stderr=STDOUT, close_fds=True).stdout.read()
	update_results(p,CMD,'IPERF')
	
	# start the iperf3 server on 'B' now since it has finished running client tests
	Popen(["iperf3","-s","&"])

    # finally update the state machine so that side A can run as client
	stepfunctions.send_task_success(
		taskToken=TASK_TOKEN,
		output="{\"TargetIp\" : \"%s\"}" % LOCAL_IP
		)
except Exception as e:
		stepfunctions.send_task_failure(
		taskToken=TASK_TOKEN,
		error=e,
		cause='It appears that there was an issue with running iperf3 client or server on %s, or the results were unable to be pushed to Cloudwatch.' % ec2_metadata.private_ipv4
		)


# Finally run an MTR report to the target ip. This does not require sync between the EC2s
response = get_activity_task('Side B running MTR')
TASK_TOKEN = response['taskToken']
try:
	Popen(["killall","iperf3"])
	CMD = 'mtr %s %s' % (MTR_FLAGS, TARGET_IP)
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






