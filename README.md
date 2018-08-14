# iperf-crawler

### Copyrights & Contributions
This tool uses source code from the following contributors:<br/>
iperf3 https://github.com/esnet/iperf<br/>
mtr https://github.com/traviscross/mtr<br/>
<br/>
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
