#!/usr/bin/python
import logging
import os
import string
from collections import defaultdict
from logging import getLogger

import sys
from time import sleep

from boto3.session import Session
from bunch import bunchify
from plumbum import local
from requests import get, request
from retrying import retry
from plumbum.cmd import sh, touch, mount, umount, mkdir, chmod

logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p')
logger = getLogger()
logger.setLevel(logging.INFO)

DEVICES = string.lowercase[1:27]

auth=dict(Authorization=os.environ.get('DOCKERCLOUD_AUTH'))
volume_id = os.environ['VOLUME_ID']
assert volume_id != 'NONE'

def find_free_device():
    pass

class NotMountedException(Exception):
    pass

class AttachedToInstanceException(Exception):
    pass

def force_detach(volume):
    def wait_disconnect(wait_seconds):
        @retry(wait_fixed=2000, stop_max_attempt_number=int(wait_seconds/2),
               retry_on_exception=lambda e: isinstance(e, AttachedToInstanceException))
        def _wait_disconnect():
            volume.load()
            if volume.attachments:
                raise AttachedToInstanceException()
            return True
        return _wait_disconnect()

    try:
        logger.info("Waiting for detach by itself...")
        wait_disconnect(15)
        return
    except AttachedToInstanceException:
        pass

    attachment = volume.attachments[0]
    try:
        logger.info("Waiting for detach by request")
        volume.detach_from_instance(InstanceId=attachment['InstanceId'], Device=attachment['Device'])
        wait_disconnect(30)
        return
    except AttachedToInstanceException:
        pass

    try:
        logger.info("Waiting for detach by force request")
        volume.detach_from_instance(InstanceId=attachment['InstanceId'], Device=attachment['Device'], Force=True)
        wait_disconnect(60)
        return
    except AttachedToInstanceException:
        pass


def mount_to_instance(volume, instance):
    instance.load()
    available_devices = set(DEVICES)
    for device in instance.block_device_mappings:
        if device['DeviceName'].startswith('/dev/xvd'):
            available_devices.remove(device['DeviceName'][-1])
    logger.info("Attaching to instance")
    volume.attach_to_instance(InstanceId=instance.id, Device='/dev/xvd%s' % available_devices.pop())

    @retry(wait_fixed=2000, stop_max_attempt_number=30)
    def wait_for_attach():
        volume.load()
        if not volume.attachments:
            raise Exception("Still not attached")
        if volume.state != 'in-use':
            raise Exception("Wrong state: %s" % volume.state)
        return True
    wait_for_attach()

def mount_via_cron(device, volume_id):
    logger.info("Ensuring cron mount")
    mount_script = "/volumes/automount/%s" % volume_id
    mount_script_inner = "/host_root%s" % mount_script
    mounted_mark = "/volumes/automount/.mounted-%s" % volume_id
    mounted_mark_inner = "/host_root%s" % mounted_mark
    if not local.path('/host_root/volumes/automount').exists():
        mkdir('-p', "/host_root/volumes/automount")
        sh('-c', "echo '* *    * * *   root    cd / && run-parts --report /volumes/automount' >> /host_root/etc/crontab")
    mkdir('-p', "/host_root/volumes/%s" % volume_id)
    local.path(mount_script_inner).write(
        """#!/usr/bin/env sh
        set -e
        mount {device} /volumes/{volume_id}
        touch {mounted_mark}
        rm {mount_script}
        """.format(**locals())
    )
    chmod('+x', "/host_root%s" % mount_script)
    @retry(wait_fixed=2000, stop_max_attempt_number=60)
    def wait_for_mount():
        logger.info("Waiting for mount")
        if local.path(mounted_mark_inner).exists():
            local.path(mounted_mark_inner).delete()
            return True
        else:
            raise NotMountedException()
    wait_for_mount()

def mount_fs(volume):
    device = volume.attachments[0]['Device']
    container_device = '/host_root%s' % device
    fs_type = sh('-c', "file -sL %s" % container_device)
    if 'ext4' in fs_type:
        pass
    elif 'data' in fs_type:
        logger.info("Creating file system")
        sh('-c', 'mkfs.ext4 %s' % container_device)
        sh('-c', 'mkdir -p /tmp/mounted')
        mount(container_device, '/tmp/mounted')
        touch('/tmp/mounted/.mounted')
        umount('/tmp/mounted')

    if not device in mount():
        logger.info("Deferring mount via cron")
        mount_via_cron('%s' % device, volume.id)


def ensure_mount(instance, volume):
    logger.info("Ensuring mount")
    if not volume.attachments:
        mount_to_instance(volume, instance)
    else:
        attached_to = volume.attachments[0]['InstanceId']
        if attached_to != instance.id:
            force_detach(volume)
            mount_to_instance(volume, instance)
    mount_fs(volume)
    logger.info("Mounted!")


def make_request(api, method='get', fullpath=False):
    if fullpath:
        path = api
    else:
        path = "/api/app/v1/%s" % api
    return bunchify(request(method, "https://cloud.docker.com%s" % path, headers=dict(**auth)).json())

def get_target_services():
    envservices = os.environ.get("RESTART_SERVICES", '')
    logger.info("Services to restart: %s" % envservices)
    servicepairs = [stackservice.strip().split('.') for stackservice in envservices.split(',') if stackservice]
    if not servicepairs:
        logger.info("Nothing to restart")
        return []

    stacks = make_request('stack/')
    ret_services = []
    stacked_services = defaultdict(list)
    for stack, service in servicepairs:
        stacked_services[stack].append(service)

    for target_stack, target_services in stacked_services.items():
        for stack in stacks.objects:
            if stack.name == target_stack:
                services = stack.services
                for service in services:
                    s = make_request(service, fullpath=True)
                    logger.info("Cloud service: %s" % s)
                    if s.name in target_services:
                        ret_services.append(s)
    logger.info("Found services: %s" % [s.name for s in ret_services])
    return ret_services

def stop_if_running(services):
    for service in services:
        logger.info("Stopping service %s" % service.name)
        if service.state != 'Stopped':
            make_request("%sstop/" % service.resource_uri, 'post', fullpath=True)

def redeploy_service(services):
    if services:
        sleep(3) # no good explanation, but otherwise wrong mount was used once...(non-ebs, but rootfs)
    for service in services:
        logger.info("Redeploying service %s" % service.name)
        make_request("%sredeploy/" % service.resource_uri, 'post', fullpath=True)


if __name__ == '__main__':
    instance_id = get('http://169.254.169.254/latest/meta-data/instance-id').text
    region = get('http://169.254.169.254/latest/meta-data/placement/availability-zone').text[:-1]
    session = Session(region_name=region)
    ec2 = session.client("ec2")
    resource = session.resource("ec2")
    volume = resource.Volume(volume_id)
    instance = resource.Instance(instance_id)
    if local.path('/host_root/volumes/%s/.mounted' % volume.id).exists():
        logger.info("Already mounted, nothing to do")
    else:
        services = get_target_services()
        stop_if_running(services)
        ensure_mount(instance, volume)
        redeploy_service(services)


