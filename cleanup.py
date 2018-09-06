import json
import boto3
import os
import time
from botocore.vendored import requests
from botocore.exceptions import ClientError


def lambda_handler(event, context):
	try:
		events = boto3.client('events')
		sdb = boto3.client('sdb')
		stepfunctions = boto3.client('stepfunctions')
		sts = boto3.client('sts')
		
		if event['RequestType'] == 'Delete':
			# Disable 1 minute CW rule that triggers the lambda crawler`
			try:
				events.disable_rule(
							Name='Parallax1Minute'
						)
			except:
				pass
				
			# Check if there is already an active crawler running. If so wait till its done before cleanup
			while True:
				response = sdb.get_attributes(
					DomainName='iperf-crawler',
					ItemName='ActiveCrawler',
					AttributeNames=[
						'Status'
						]	
					)
				if response['Attributes'][0]['Value'] == 'Running':
					time.sleep(5)
				else:
					break
					
			def set_creds(x,y):
				if x[y]['Account'] == 'parent':
					ec2 = boto3.client('ec2', region_name = x[y]['Region'])
				else:
					assume_role = sts.assume_role(
						RoleArn='arn:aws:iam::%s:role/IperfCrawlerLambdaRole' % x[y]['Account'],
						RoleSessionName='deploy_iperf_worker'
						)
					ec2 = boto3.client('ec2', 
							aws_access_key_id=assume_role['Credentials']['AccessKeyId'],
							aws_secret_access_key=assume_role['Credentials']['SecretAccessKey'],
							aws_session_token=assume_role['Credentials']['SessionToken'],	
							region_name = x[y]['Region']
							)
				return ec2	
			
			try:
				# Get all items in DB and delete their resources
				get_group_subs = sdb.select(
									SelectExpression='select * from `iperf-crawler`'
									)
				items_to_delete={}
				for item in get_group_subs['Items']:
						atts_per_item_to_delete={}
						for attribute in item['Attributes']:
							atts_per_item_to_delete[attribute['Name']] = attribute['Value']
						items_to_delete[item['Name']]=atts_per_item_to_delete
			except:
				pass
			
			for item in items_to_delete:
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
				# Delete all EC2s currently running
				try:
					ec2 = set_creds(items_to_delete,item)
					ec2.terminate_instances(
						InstanceIds=[items_to_delete[item]['InstanceId']]
						)
				except:
					continue
					
			for item in items_to_delete:
				try:
					# wait for the instance to fully terminate before deleting its security group
					ec2 = set_creds(items_to_delete,item)
					
					while True:
						status = ec2.describe_instances(
									InstanceIds=[items_to_delete[item]['InstanceId']]
									)
						if status['Reservations'][0]['Instances'][0]['State']['Name'] == 'terminated':
							break
						else:
							time.sleep(2)
				except:
					continue
				
				try:
					# delete its security group
					ec2.delete_security_group(
						GroupId=items_to_delete[item]['SgId']
					)
				except:
					continue
				
				try:
					# Remove the tags from workers previous tagged subnets
					ec2.delete_tags(
						Resources=[
							item
						],
						Tags=[
							{
								'Key': 'iperf',
								'Value': items_to_delete[item]['Group']
							}
						]
					)
				except:
					continue
				
				try:
					# Remove the stepfunction activityArn
					stepfunctions.delete_activity(
						activityArn=items_to_delete[item]['ActivityArn']
							)
					
				except:
					continue
					
				
				try:
					# Remove statemachine if it still exists
					stepfunctions.delete_state_machine(
							stateMachineArn=items_to_delete[item]['StateArn']
						)
				except:
					try:
						# Stop any potentially hung execution
						response = stepfunctions.stop_execution(
							executionArn=items_to_delete[item]['ExecutionArn']
						)
						
						# Remove statemachine if it still exists
						stepfunctions.delete_state_machine(
								stateMachineArn=items_to_delete[item]['StateArn']
							)
						
					except:
						continue
							
		# Finally delete the SDB itself
		sdb.delete_domain(
			DomainName='iperf-crawler',
		)

			
		sendResponseCfn(event, context, "SUCCESS")
	except Exception as e:
		print(e)
		sendResponseCfn(event, context, "SUCCESS")
		


def sendResponseCfn(event, context, responseStatus):
	response_body = {'Status': responseStatus,
					'Reason': 'Log stream name: ' + context.log_stream_name,
					'PhysicalResourceId': context.log_stream_name,
					'StackId': event['StackId'],
					'RequestId': event['RequestId'],
					'LogicalResourceId': event['LogicalResourceId'],
					'Data': json.loads("{}")}
	requests.put(event['ResponseURL'], data=json.dumps(response_body))
