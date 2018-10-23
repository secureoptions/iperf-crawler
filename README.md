# iperf-crawler

## Table of Contents
- [Copyrights & Contributions](#copy)
- [What is Iperf Crawler](#what)
- [The benefits of using Iperf Crawler vs. manual setup](#benefits)
- [Pre-Deployment Requirements](#requirements)
- [Usage Instructions](#usage)
  + [Deployment Steps for the main AWS account](#primary)
  + [Deployment Steps for secondary AWS accounts](#secondary)
- [Supported AWS Regions](#supported)
- [Deployment workflow diagram](#workflow)
- [Known Limitations](#limits)
- [Error Handling](#errors)
- [Limits](#limits)
- [FAQs](#faqs)

<br/>
<a name="copy"></a>

### Copyrights & Contributions

This tool uses source code from the following authors:<br/>
iperf3 https://github.com/esnet/iperf<br/>
mtr https://github.com/traviscross/mtr<br/>
<br/>
<a name="what"></a>

### What is Iperf Crawler

Iperf Crawler (IC) is an iperf3 and mtr automation tool deployed through Cloudformation. It will launch iperf3 client/server EC2s in any two subnets that you specify, and automate the tests between these subnets. It can greatly speed up the process of benchmarking or troubleshooting network throughput issues in an AWS environment. It's also a great tool for building quick side-by-side comparisons of different network paths in just a few minutes with no manual set up.<br/>
<br/>
For example, you may want to compare throughput and latency between identical EC2 types in different Availability Zones to see if there is any significant difference between network paths in these AZs. Such as:<br/>
<br/>
us-west-2a <---> us-west-2b<br/>
us-west-2a <---> us-west-2c<br/>
us-west-2b <---> us-west-2c<br/>
<br/>
Iperf Crawler can very quickly gather these iperf and mtr test results and export them to Cloudwatch a Log Group for further side-by-side analysis, or to build Cloudwatch metrics and alarms.<br/>
<br/>
<a name="benefits"></a>

### The benefits of using Iperf Crawler vs. manual setup

There are several major benefits to using this tool:
- Environment prep is automated (necessary security group entries, gathering iperf3 server/client metadata)
- The live status of the iperf3 tests can monitored easily in one place through AWS Step Functions to ensure tests are successful ( more info on that service here https://aws.amazon.com/step-functions/ )
- Environment cleanup is automated once the iperf3 tests have finished so users can deploy the tool and forget about it. Cleanup terminates running EC2s, un-tags subnets that have completed testing, removes security group entries, etc
- Results of iperf are sent to Cloudwatch for further programmatic handling by applications or to build Cloudwatch metrics and alarms
- Can run tests between _multiple_ subnets simultaneously. For example you may want to run tests between 20 pairs of subnets at once. This tool is capable of doing this.

<br/>
<a name="requirements"></a>

### Pre-Deployment Requirements

- The AWS Network ACLs associated with tagged subnets must be open to all inbound & outbound traffic (Iperf Crawler will automatically create the necessary security groups and entries to allow tests between subnets)
- There must be *either* a public or private network available between the two subnets

<br/>
<a name="usage"></a>

### Usage Instructions

![user experience](https://s3.amazonaws.com/secure-options/UserExperience.PNG)

<br/>
<a name="primary"></a>

#### Deployment Steps for the main AWS account 

The main AWS account will manage all the state machines and hold the final iperf3 results of all tests in its Cloudwatch logs, regardless of which other AWS accounts the tests were ran in. Below are the steps to deploy Iperf Crawler in the main account.
<br/>
1. Launch the Primary Account Cloudformation stack <a href="https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/new?stackName=IperfCrawler&templateURL=https://s3.amazonaws.com/secure-options/primary_account.yml"><img src="https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png"/></a>
2. Specify optional, client-side mtr and iperf3 flags in the Cloudformation parameters.
3. If you have any additional, secondary AWS accounts that you want to run Iperf3 tests from, specify a comma separated list of these accounts in the *Secondary AWS Accounts* field. If you do not have any other accounts, leave the field at its default value of '000000000000'
4. Tag any two subnets to a group with a Key of **iperf** and value of **groupN** where **N** is a number. Key and Value are lowercase-sensitive. If any characters are uppercase, the tagged subnet will be ignored . If subnets are in a different AWS account follow the steps further below for cross-account support)
5. Monitor the AWS State Machine execution of your groups to watch their progress through the iperf/mtr tests in the console.  Make sure that you are looking in the region that your Cloudformation was deployed in
6. Once your iperf3/mtr tests have completed, you will find the results in the Cloudwatch Log Group named Iperf-Crawler. The results are identified by Log Streams labeled by group number

</br>
<a name="supported"></a>

### Supported Regions

The Cloudformation stacks can only be deployed in the following regions:
<br/>

<p align="left">
  <img width="300" height="300" src="https://s3.amazonaws.com/secure-options/SupportedRegions.png">
</p>
<br/>

... *__HOWEVER__*, you can tag subnets in *__any__* commercial AWS region. All iperf3 and MTR results can be seen in the Cloudwatch logs in AWS region which you deployed the Cloudformation template.

</br>
<a name="workflow"></a>

### Deployment Workflow

![workflow](https://s3.amazonaws.com/secure-options/IperfCrawler.PNG)

</br>
<a name="limits"></a>

### Known Limitations ###

- Iperf Crawler allows a user to select the instance type they would like to deploy for testing, but they should take note of the EC2 limits applied to their particular account. Exceeding these limits will cause IC to fail
- You cannot tag a subnet to more than one group at a time.
- Tagging more than two subnets to a group may result in unexpected behavior

</br>
<a name="errors"></a>

###  Error Handling

Iperf Crawler will detect whether a state machine has failed or timed-out, and then remove its group/resources accordingly. If a user specifies a invalid MTR or Iperf3 client command in Cloudformation, this can cause the state machine to fail. If this happens the worker EC2s will send errors rather than test results to their respective group log in Cloudwatch

### Limits

Iperf Crawler itself does not have many inherent limitations, however, you should be mindful of your AWS account's EC2 limits. For example if you intend to run a bunch of Iperf Crawler between M4.4xlarge EC2s, you will want to check your current limit first. Iperf Crawler will not throw an error if it hits some limits

</br>
<a name="faqs"></a>

### FAQs

<a name="questionone"></a>

#### Can I run tests between subnets in multiple groups simultaneously?
Yes, you can tag as many subnets to groups as you want, and IC will deploy EC2s in those subnets as long as you are under your EC2 limits
</br>
<a name="questiontwo"></a>

#### Is cross-account supported?
Not yet, but will soon be.
</br>
<a name="questionthree"></a>

#### Can I tag subnets in multiple AWS regions?
Yes, however the AWS State Machines and Cloudwatch results will all be located in the region that you deployed the IC Cloudformation stack in
</br>
<a name="questionfour"></a>

#### Are there any known limitations with the iperf3 or mtr client commands that I can run?
Not all variations of iperf3 and mtr tests have been tested, since there is a limitless combination of options you could use. That said, IC should be handle most commands that you run as long as they take no more than 55 seconds to complete. IC is particularly sensitive to long-running tests over this time.
</br>
<a name="questionfive"></a>

#### Can I run both TCP and UDP iperf3 tests?
Yes
</br>
<a name="questionsix"></a>

#### Can I run parallel streams?
Yes, you can specify -P such as iperf3 -P 30 -c in Cloudformation to run parallel streams.
</br>
<a name="questionseven"></a>

#### Can I run multiple iperf3 client processes in a for loop?
At the moment this is not supported
