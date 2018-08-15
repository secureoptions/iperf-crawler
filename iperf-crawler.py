import boto3
import re
import json
import time
import os
import sys
from botocore.vendored import requests
import datetime


def lambda_handler(event, context):
	REGION = os.environ['REGION']
	URLS =['https://raw.githubusercontent.com/secureoptions/iperf-crawler/master/workerA.py','https://raw.githubusercontent.com/secureoptions/iperf-crawler/master/workerB.py']
	IPERF_FLAGS = os.environ['IPERF_FLAGS']
	MTR_FLAGS = os.environ['MTR_FLAGS']
	INSTANCE_PROFILE = os.environ['INSTANCE_PROFILE']
	STATES_EXECUTION_ROLE = os.environ['STATES_EXECUTION_ROLE']
	TYPE = os.environ['TYPE']
	
	events = boto3.client('events')
	sdb = boto3.client('sdb')
	ec2 = boto3.client('ec2')
	stepfunctions = boto3.client('stepfunctions')
	logs = boto3.client('logs')

	# Base Amazon Linux AMIs per region which will be used to launch the worker nodes
	ami = {'us-east-1': 'ami-97785bed', 'us-west-1': 'ami-824c4ee2', 'ap-northeast-3': 'ami-83444afe', 'ap-northeast-2': 'ami-863090e8', 'ap-northeast-1': 'ami-ceafcba8', 'sa-east-1': 'ami-84175ae8', 'ap-southeast-1': 'ami-68097514', 'ca-central-1': 'ami-a954d1cd', 'ap-southeast-2': 'ami-942dd1f6', 'us-west-2': 'ami-f2d3638a', 'us-east-2': 'ami-f63b1193', 'ap-south-1': 'ami-531a4c3c', 'eu-central-1': 'ami-5652ce39', 'eu-west-1': 'ami-d834aba1', 'eu-west-2': 'ami-403e2524', 'eu-west-3': 'ami-8ee056f3'}	
	

	# if SDB domain doesn't exist. Create it
	try:
	    sdb.get_attributes(
	        DomainName='iperf-crawler'
	        )
	except:
		sdb.create_domain(
			DomainName='iperf-crawler'
				)
						
						
	# Check if there is already an active crawler running
	try:
		response = sdb.get_attributes(
	        DomainName='iperf-crawler',
			ItemName='ActiveCrawler',
			AttributeNames=[
				'Status'
				]	
	        )
		if response['Attributes'][0]['Value'] == 'Running':
			sys.exit()
	except:
		pass
			
		
	
	# Setting status to 'Running' so that this script does not execute again while a current script is running
	sdb.put_attributes(
		DomainName='iperf-crawler',
		ItemName='ActiveCrawler',
		Attributes=[
			{
				'Name':'Status',
				'Value':'Running',
				'Replace':True
			}
			]
		)
	
	try:
		# See if any subnets in the DB have removed iperf:groupN tag. If so remove subnet item from DB
		get_group_subs = sdb.select(
						SelectExpression='select * from `iperf-crawler`'
						)
											
		for item in get_group_subs['Items']:
			subnet_id = item['Name']
			for attribute in item['Attributes']:
				if attribute['Name'] == 'Group':
					group_id = attribute['Value']
			
					group_status = ec2.describe_subnets(
							Filters=[{
											'Name': 'tag:iperf',
											'Values': [group_id]
									}],
							SubnetIds=[subnet_id]
												)
					
					if group_status['Subnets'] == []:
						# Remove their entry from SDB
						sdb.delete_attributes(
									DomainName='iperf-crawler',
									ItemName=subnet_id
								)
	except:
		pass

	# Check for tagged subnets per region
	response = ec2.describe_regions()
	for region in response['Regions']:
		ec2 = boto3.client('ec2', region_name = region['RegionName'])
		subnets = ec2.describe_subnets(
				Filters=[{
								'Name': 'tag-key',
								'Values': ['iperf']
						}]
									)
		# Create a dictionary of subnet:groupId 
		key_regex = re.compile('^iperf$')
		value_regex = re.compile('^group[0-9]+$')
		
		for subnet in subnets['Subnets']:
			# keep track of number of iperf:group pairs on subnet, and choose the last one
			for tag in subnet['Tags']:
				if re.match(key_regex, tag['Key']) and re.match(value_regex, tag['Value']):
				
					#See if tagged subnets match items in SimpleDB. If not add them now a, if they do exist in SDB what are their status? Determine if the subnet needs a worker launched in it or not.
					
					try:
						response = sdb.get_attributes(
							DomainName='iperf-crawler',
							ItemName=subnet['SubnetId']
						)
						response['Attributes'][0]['Name']
						sdb.put_attributes(
							DomainName='iperf-crawler',
							ItemName=subnet['SubnetId'],
							Attributes=[
								{
									'Name':'Group',
									'Value':tag['Value'],
									'Replace':True
								}
								]
							)
					except KeyError:
						sdb.put_attributes(
							DomainName='iperf-crawler',
							ItemName=subnet['SubnetId'],
							Attributes=[
								{
									'Name':'Group',
									'Value':tag['Value']
								},
								{
									'Name':'WorkerLaunched',
									'Value':'False'
								},
								{
									'Name':'Region',
									'Value': region['RegionName']
								}
								]
							)
							
							
					# Make sure that there is 2 subnets tagged for a group before workers are deployed for that group
					try:
						get_group_subs = sdb.select(
											SelectExpression='select * from `iperf-crawler` where `Group` = "%s" AND `WorkerLaunched` = "%s"' % (tag['Value'],'False')
										)
										
						if len(get_group_subs['Items']) == 2:				
							# Create a list of the two grouped subnets. Will export this list to iperf workers as a variable
							subnets=[]
							for item in get_group_subs['Items']:
								subnets.append(item['Name'])
										
							# If two tagged subnets in the same group do not have workers. Create activities for each worker in a group, and a state machine for each group.

							
							workerA = stepfunctions.create_activity(
										name='%s-worker' % subnets[0]
										)
										
							workerB = stepfunctions.create_activity(
										name='%s-worker' % subnets[1]
										)
										
							activity_ARNs = []			
							workerA_ARN = workerA['activityArn']
							workerB_ARN = workerB['activityArn']
							activity_ARNs.extend([workerA_ARN,workerB_ARN])
										
							# state machine definition 
							json_content ={
										"Comment": "Will track and sync transitions of iperf3 client and server",
										"StartAt": "WorkerBCreateSGEntry",
										"States": {
											"WorkerBCreateSGEntry": {
												"Type": "Task",
												"Resource": workerB_ARN,
												"TimeoutSeconds": 120,
												"Next": "WorkerACreateSGEntryStartServerARunning"
											},
											"WorkerACreateSGEntryStartServerARunning": {
												"Type": "Task",
												"Resource": workerA_ARN,
												"TimeoutSeconds": 120,
												"Next": "ClientBSendTraffic"
											},
											"ClientBSendTraffic": {
												"Type": "Task",
												"Resource": workerB_ARN,
												"TimeoutSeconds": 120,
												"Next": "ClientASendTraffic"
											},
											"ClientASendTraffic": {
												"Type": "Task",
												"Resource": workerA_ARN,
												"TimeoutSeconds": 120,
												"Next": "RunMtrBothSides"
											},
											"RunMtrBothSides": {
												"Type": "Parallel",
												"End": True,
												"Branches": [{
														"StartAt": "RunMtrOnWorkerA",
														"States": {
															"RunMtrOnWorkerA": {
																"Type": "Task",
																"Resource": workerA_ARN,
																"End": True
															}
														}
													},
													{
														"StartAt": "RunMtrOnWorkerB",
														"States": {
															"RunMtrOnWorkerB": {
																"Type": "Task",
																"Resource": workerB_ARN,
																"End": True
															}
														}
													}
												]
											}
										}
									}
							json_content = json.dumps(json_content)
							
							state_machine = stepfunctions.create_state_machine(
									name='StateMachine-%s' % tag['Value'],
									definition=json_content,
									roleArn=STATES_EXECUTION_ROLE
									)
																	
							
							# Now launch worker/EC2s
							worker_selector=0
							for item in get_group_subs['Items']:
								
								# write the state machine ARN to the SDB under each item so that cleanup lambda function can monitor it
								sdb.put_attributes(
									DomainName='iperf-crawler',
									ItemName=item['Name'],
									Attributes=[
										{
											'Name': 'StateArn',
											'Value': state_machine['stateMachineArn']
										}
									]
								)
								
								# create another attribute that workerA can query and check to make sure workerB is ready to begin the step functions. This value will initially be set to 'False' until workerB sets it to 'True'.
								sdb.put_attributes(
									DomainName='iperf-crawler',
									ItemName=item['Name'],
									Attributes=[
										{
											'Name': 'ReadyCheck',
											'Value': 'False'
										}
									]
								)
								
								subnet_attributes = sdb.get_attributes(
														DomainName='iperf-crawler',
														ItemName=item['Name'],
														AttributeNames=['Region']
														)
								
								EC2_REGION = subnet_attributes['Attributes'][0]['Value']
								ec2 = boto3.client('ec2',region_name = EC2_REGION)
								
								describe_subnets = ec2.describe_subnets(
													SubnetIds=[item['Name']]
													)
								
								vpc_id = describe_subnets['Subnets'][0]['VpcId']
								az_id = describe_subnets['Subnets'][0]['AvailabilityZone']
								
								create_sg = ec2.create_security_group(
												Description='SG for %s worker in iperf-crawler' % item['Name'],
												GroupName='iperf-crawler-%s' % item['Name'],
												VpcId=vpc_id
												)
													
								sg_id = create_sg['GroupId'] 					
								
								launch_instance = ec2.run_instances(
													ImageId = ami[subnet_attributes['Attributes'][0]['Value']],
													InstanceType = TYPE,
													NetworkInterfaces = [{
														'AssociatePublicIpAddress': True, 
														'DeviceIndex' : 0, 
														'SubnetId' : item['Name'], 
														'Groups' : [sg_id] 
														}],
													TagSpecifications = [{
														'ResourceType' : 'instance',
														'Tags' : [{ 
															'Key' : 'Name',
															'Value' : 'iperf-worker-%s' % tag['Value'].lower()
															}]
														}],
													UserData = ('#!/bin/bash\n'
															   'yum-config-manager --enable epel\n'
															   'yum update -y\n'
															   'yum install gcc -y\n'
															   'wget http://downloads.es.net/pub/iperf/iperf-3-current.tar.gz\n'
															   'tar xvzf iperf-3-current.tar.gz\n'
															   'cd iperf-*/\n'
															   './configure && make && make install\n'
															   'export PATH=/sbin:/bin:/usr/sbin:/usr/bin:/opt/aws/bin:/usr/local/bin\n'
															   'yum install mtr -y\n'
															   'pip install ec2-metadata\n'
															   'pip install boto3\n'
															   'wget -P /home/ec2-user/ %s\n' 
															   'chmod 755 /home/ec2-user/worker*\n'
															   'echo STATE_MACHINE_ARN=\"\'%s\'\" >> /home/ec2-user/vars.py\n'
															   'echo REGION=\"\'%s\'\" >> /home/ec2-user/vars.py\n'
															   'echo EC2_REGION=\"\'%s\'\" >> /home/ec2-user/vars.py\n'
															   'echo A_ACTIVITY_ARN=\"\'%s\'\" >> /home/ec2-user/vars.py\n'
															   'echo B_ACTIVITY_ARN=\"\'%s\'\" >> /home/ec2-user/vars.py\n'
															   'echo SG_ID=\"\'%s\'\" >> /home/ec2-user/vars.py\n'
															   'echo IPERF_FLAGS=\"\'%s\'\" >> /home/ec2-user/vars.py\n'
															   'echo MTR_FLAGS=\"\'%s\'\" >> /home/ec2-user/vars.py\n'
															   'echo SUBNETS=\"\'%s\'\" >> /home/ec2-user/vars.py\n'
															   'echo GROUP="\'%s\'\" >> /home/ec2-user/vars.py\n'
															   'python /home/ec2-user/worker*') % 
															   (URLS[worker_selector], state_machine['stateMachineArn'], REGION, EC2_REGION, workerA_ARN, workerB_ARN, sg_id, IPERF_FLAGS, MTR_FLAGS, ','.join(subnets),tag['Value']),
															   
															   
													
													IamInstanceProfile = {'Arn':INSTANCE_PROFILE},
													MinCount=1,
													MaxCount=1
													)
								
								# Update SDB for this subnet since a worker has been deployed in it
								update_db = sdb.put_attributes(
												DomainName='iperf-crawler',
												ItemName=item['Name'],
												Attributes=[
													{
														'Name': 'WorkerLaunched',
														'Value': 'True',
														'Replace': True
													},
													{
														'Name': 'InstanceId',
														'Value': launch_instance['Instances'][0]['InstanceId']
													},
													{
														'Name': 'ActivityArn',
														'Value': activity_ARNs[worker_selector]
													},
													{
														'Name': 'SgId',
														'Value': sg_id
													},
													{
														'Name': 'TerminateInProgress',
														'Value':'False'
													},
													{
														'Name': 'AvailabilityZone',
														'Value': az_id
													}
													]
												)
								worker_selector += 1

					except KeyError:
						# Two subnets without workers have yet to be tagged. Pass this iteration and check again later
						pass


	# Check for any finished iperf3 tests between serverA <--> serverB pairs, cleanup their envinroment, delete their worker EC2s
	def delete_items(a,b):				
		for item in a:
			# Update the termination status
			sdb.put_attributes(
				DomainName='iperf-crawler',
				ItemName=item,
				Attributes=[
					{
						'Name': 'TerminateInProgress',
						'Value':'True',
						'Replace':True
					}
					]
				)
			try:
				ec2 = boto3.client('ec2', region_name = a[item]['Region'])
				ec2.terminate_instances(
					InstanceIds=[a[item]['InstanceId']]
					)
			except:
				pass

		for item in a:
			try:
				# wait for the instance to fully terminate before deleting its security group
				ec2 = boto3.client('ec2', region_name = a[item]['Region'])

				while True:
					status = ec2.describe_instances(
								InstanceIds=[a[item]['InstanceId']]
								)
					if status['Reservations'][0]['Instances'][0]['State']['Name'] == 'terminated':
						break
					else:
						time.sleep(2)
			except Exception as e:
				print e
				pass
					
		for item in a:
			try:
				# delete its security group
				ec2 = boto3.client('ec2', region_name = a[item]['Region'])
				ec2.delete_security_group(
					GroupId=a[item]['SgId']
				)
			except:
				pass
			
			try:
				# Remove the tags from workers previous tagged subnets
				ec2.delete_tags(
					Resources=[
						item
					],
					Tags=[
						{
							'Key': 'iperf',
							'Value': a[item]['Group']
						}
					]
				)
			except Exception as e:
				print e
				pass
				
			# if this is a cleanup due to a statemachine failure, log error in Cloudwatch
			try:
				if b == 'log':
						epoch = datetime.datetime.utcfromtimestamp(0)
						
						def unix_time_millis(dt):
							return (dt - epoch).total_seconds() * 1000.0


						SEQ_TOKEN = logs.describe_log_streams(
							logGroupName='Iperf-Crawler',
							logStreamNamePrefix=a[item]['Group']
						)
						
					
						SEQ_TOKEN = SEQ_TOKEN['logStreams'][0]['uploadSequenceToken']
						logs.put_log_events(
							logGroupName='Iperf-Crawler',
							logStreamName=a[item]['Group'],
							logEvents=[
								{
									'timestamp': int(unix_time_millis(datetime.datetime.now())),
									'message': 'There was an internal issue running your iperf/mtr commands'
								},
							],
							sequenceToken=SEQ_TOKEN
						)

						logs.put_log_events(
							logGroupName='Iperf-Crawler',
							logStreamName=a[item]['Group'],
							logEvents=[
								{
									'timestamp': int(unix_time_millis(datetime.datetime.now())),
									'message': 'There was an internal issue running your iperf/mtr commands'
								},
							],
						)
			except:
				print e
				pass
				
			for item in a:
				try:
					# Remove the stepfunction activityArn
					stepfunctions.delete_activity(
						activityArn=a[item]['ActivityArn']
							)

					# Stop any potentially hung execution
					stepfunctions.stop_execution(
						executionArn=a[item]['ExecutionArn']
					)
				except:
					continue
				try:
					# Remove statemachine if it still exists
					stepfunctions.delete_state_machine(
							stateMachineArn=a[item]['StateArn']
						)
				except:
					pass
					
			for item in a:
				try:
					# Remove their entry from SDB
					sdb.delete_attributes(
						DomainName='iperf-crawler',
						ItemName=item
					)
				except:
					pass
					
	try:
		# Get all items that have completed their testing	
		get_group_subs = sdb.select(
							SelectExpression='select * from `iperf-crawler` where `FinishStatus` = "%s" AND `TerminateInProgress` = "%s"' % ('Completed','False') 
							)
		
		# Map out the attributes of each item needing to be deleted	
		items_to_delete={}						
		for item in get_group_subs['Items']:
				atts_per_item_to_delete={}
				for attribute in item['Attributes']:
					atts_per_item_to_delete[attribute['Name']] = attribute['Value']
				items_to_delete[item['Name']]=atts_per_item_to_delete
		
		delete_items(items_to_delete,'null')
	except KeyError:
		pass
	
	try:
		# Check and see if a groups' state execution is stuck in failed or time_out status. If so delete the group of resources
		get_group_subs = sdb.select(
					SelectExpression='select * from `iperf-crawler`'
					)
		items_to_check={}						
		for item in get_group_subs['Items']:
				atts_per_item={}
				for attribute in item['Attributes']:
					atts_per_item[attribute['Name']] = attribute['Value']
				items_to_check[item['Name']]=atts_per_item
				
		items_to_delete={}	
		for item in items_to_check:
			try:
				exec_status= stepfunctions.describe_execution(
						executionArn=items_to_check[item]['ExecutionArn']
					)
				if exec_status['status'] == 'FAILED' or exec_status['status'] == 'TIMED_OUT' or exec_status['status'] == 'ABORTED':
					items_to_delete[item]=items_to_check[item]
			except:
				pass

		delete_items(items_to_delete,'log')
	except KeyError:
			pass
	
	sdb.put_attributes(
		DomainName='iperf-crawler',
		ItemName='ActiveCrawler',
		Attributes=[
			{
				'Name':'Status',
				'Value':'Stopped',
				'Replace':True
			}
			]
		)