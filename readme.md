### EBS Attacher, used with docker cloud(tutum)

The goal is mount EBS volume for container use without rebuilding original container  
Also, mount should be done automatically and if service restarted on another node - automatically remounted  
Last host that requests mount get's the priority and detaches/force detaches from previous instance  

One EBS can be used as data source for multiple containers  

### Implementation

`attacher` container starts prior to main container and temporary stops it, then it mounts EBS to the host,   
defers mount to host itself by injecting cron task and restarts services(s) via docker cloud api  

For this to work all services must start on same node, for this purpose another service created - "placeholder"  
All other services must include volumes from placeholder, this will ensure their placement  

### How to use

a) Node must have proper EC2 credentials via IAM profile 
`TODO: Specify exact credentials, meanwhile start with EC2 full access`
b) Begin with example stackfile provided in examples/  
* Replace vol-xxx with volume id
* Replace post-attacher services with services you want to use
* Replace `- RESTART_SERVICES=influxdb.influxdb, influxdb.grafana` with services you just specified, pre-dot notation is name of stack
* Deploy stack, enjoy.
