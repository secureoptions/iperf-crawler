# iperf-crawler

### Copyrights & Contributions
This tool uses source code from the following contributors:<br/>
iperf3 https://github.com/esnet/iperf<br/>
mtr https://github.com/traviscross/mtr<br/>
<br/>
### What is Iperf Crawler
Iperf Crawler (IC) is an iperf3 and mtr automation tool deployed through Cloudformation. It can greatly speed up the process of benchmarking or troubleshooting network throughput issues in an AWS environment. It's also a great tool for building quick side-by-side comparisons of different network paths in just a few minutes with no manual set up.<br/>
<br/>
For example, you may want to compare throughput and latency between identical EC2 types in different Availability Zones to see if there is any significant difference between network paths in these AZs. Such as:<br/>
<br/>
us-west-2a <---> us-west-2b<br/>
us-west-2a <---> us-west-2c<br/>
us-west-2b <---> us-west-2c<br/>
<br/>
Iperf Crawler can very quickly gather these iperf and mtr test results and export them to Cloudwatch a Log Group for further side-by-side analysis, or to build Cloudwatch metrics and alarms.<br/>
<br/>
### The benefits of using Iperf Crawler vs. manual setup

There are several major benefits to using this tool:
- Environment prep is automated (necessary security group entries, gathering iperf3 server/client metadata)
- The live status of the iperf3 tests can monitored easily in one place through AWS Step Functions to ensure tests are successful ( more info on that service here https://aws.amazon.com/step-functions/ )
- Environment cleanup is automated once the iperf3 tests have finished so users can deploy the tool and forget about it. Cleanup terminates running EC2s, un-tags subnets that have completed testing, removes security group entries, etc
- Results of iperf are sent to Cloudwatch for further programmatic handling by applications or to build Cloudwatch metrics and alarms
<br/>

### Usage Instructions

![alt text](https://s3.amazonaws.com/secure-options/UserExperience.PNG)

1. Launch Cloudformation stack from here <a href="https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/new?stackName=IperfCrawler&templateURL=https://s3.amazonaws.com/secure-options/iperf_crawler.yml"><img src="https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png"/></a>
2. Specify optional, client-side mtr and iperf3 flags in the Cloudformation parameters.
3. Tag any two subnets to a group with a Key of iperf and value of groupN where N is a number. Key and Value are lowercase-sensitive. If any characters are uppercase, the tagged subnet will be ignored
4. Monitor the AWS State Machine execution of your groups to watch their progress through the iperf/mtr tests in the console.  Make sure that you are looking in the region that your Cloudformation was deployed in
5. Once your iperf3/mtr tests have completed, you will find the results in the Cloudwatch Log Group named Iperf-Crawler. The results are identified by Log Streams labeled by group number
 	
