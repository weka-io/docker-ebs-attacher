#!/usr/bin/python
import logging
import os
import string
from logging import getLogger

import sys
from boto3.session import Session
from plumbum import local
from requests import get
from retrying import retry
from plumbum.cmd import sh, touch, mount, umount

logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p')
logger = getLogger()
logger.setLevel(logging.DEBUG)

DEVICES = string.lowercase[1:27]

def find_free_device():
    pass


class AttachedToInstanceException(Exception):
    pass

def wait_detach(volume):
    def wait_disconnect(wait_seconds):
        @retry(wait_fixed=2000, stop_max_attempt_number=int(wait_seconds/2),
               retry_on_exception=lambda e: isinstance(e, AttachedToInstanceException))
        def _wait_disconnect():
            logger.info("Waiting for detach by itself...")
            volume.load()
            if volume.attachments:
                raise AttachedToInstanceException()
            return True
        return _wait_disconnect()

    try:
        wait_disconnect(15)
    except AttachedToInstanceException:
        attachment = volume.attachments[0]
        volume.detach_from_instance(InstanceId=attachment['InstanceId'], Device=attachment['Device'])
        wait_disconnect(30)


def mount_to_instance(volume, instance):
    instance.load()
    available_devices = set(DEVICES)
    for device in instance.block_device_mapping:
        if device['DeviceName'].startswith('xvd'):
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

def ensure_cron_mount(device, volume_id):
    logger.info("Ensuring cron mount")
    filename = "/volumes/automount/%s" % volume_id
    sh('-c', """
    if [ ! -d /host_root/volumes/automount ]; then
        mkdir -p /host_root/volumes/automount
        echo '* *    * * *   root    cd / && run-parts --report /volumes/automount' >> /host_root/etc/crontab
    fi
    mkdir -p /host_root/volumes/{volume_id}
    echo "mount {device} /volumes/{volume_id} && rm {filename}" > /host_root{filename}
    chmod +x /host_root{filename}
    """.format(**locals()))

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
        ensure_cron_mount('%s' % device, volume.id)

    return
    # @retry(wait_fixed=2000, stop_max_attempt_number=120)
    # def wait_for_host_mount():
    #     logger.info("Waiting for host-side mount")
    #     if not local.path('/host_root/volumes/%s/.mounted' % volume_id).exists():
    #         raise Exception("Not mounted on host yet")
    # wait_for_host_mount()

def ensure_mount(instance, volume):
    if not volume.attachments:
        mount_to_instance(volume, instance.id)
    else:
        attached_to = volume.attachments[0]['InstanceId']
        if attached_to != instance.id:
            wait_detach(volume)
            mount_to_instance(resource, volume)
            mount_fs(volume)
        else:
            mount_fs(volume)


if __name__ == '__main__':
    volume_id = os.environ['VOLUME_ID']
    volume_name = os.environ['VOLUME_NAME']
    assert volume_id != 'NONE'
    assert volume_name != 'NONE'

    instance_id = get('http://169.254.169.254/latest/meta-data/instance-id').text
    region = get('http://169.254.169.254/latest/meta-data/placement/availability-zone').text[:-1]
    session = Session(region_name=region)
    ec2 = session.client("ec2")
    resource = session.resource("ec2")
    volume = resource.Volume(volume_id)
    instance = resource.Instance(instance_id)
    ensure_mount(instance, volume)

