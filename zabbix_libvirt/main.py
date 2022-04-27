#!/usr/bin/env python2
# coding=utf-8
"""The main module that orchestrates everything"""

import json
import logging
import sys
import argparse

from libvirt_checks import LibvirtConnection


VNICS_KEY = "libvirt.nic.discover"
VDISKS_KEY = "libvirt.disk.discover"

# Setup logger
logger = logging.getLogger('logger')
logger.setLevel(logging.WARNING)
handler = logging.StreamHandler(stream=sys.stdout)
handler.setFormatter(logging.Formatter(fmt='[%(asctime)s: %(levelname)s] %(message)s'))
logger.addHandler(handler)

uri = "qemu:///system"


def parse_args():

    parser = argparse.ArgumentParser(description='Return QEMU information for Zabbix parsing')

    parser.add_argument('-U', '--uri',
                        help="Connection URI",
                        metavar='URI',
                        dest='uri',
                        type=str,
                        default='qemu:///system')

    parser.add_argument('-a', '--action',
                        metavar='ACTION',
                        dest='action',
                        help='The name of the action to be performed',
                        type=str,
                        default='vnic')

    parser.add_argument('-d', '--domain',
                        metavar='DOMAIN',
                        dest='domain',
                        help='The name of the domain to be queried',
                        type=str,
                        # default='3f6c5c3e-dad6-45b6-bfb2-86a7d564b383'
                        )

    parser.add_argument('-p', '--params',
                        metavar='PARAMS',
                        dest='params',
                        help='The name of the disc or nic',
                        type=str,
                        # default='vnet3'
                        )

    parser.add_argument('-m', '--method',
                        metavar='METHOD',
                        dest='method',
                        help='The name of the method to be queried',
                        type=str,
                        # default='read'
                        )

    parser.add_argument('-o',
                        dest='out',
                        action='store_true'
                        )

    args = parser.parse_args()
    return args


def get_instance_metrics(domain_uuid_string, libvirt_connection):
    """Gather instance attributes for domain with `domain_uuid_string` using
    `libvirt_connection` and then send the zabbix metrics using `zabbix_sender`
    """
    # 1. Discover nics and disks, and send the discovery packet
    metrics = []
    vnics = libvirt_connection.discover_vnics(domain_uuid_string)
    vdisks = libvirt_connection.discover_vdisks(domain_uuid_string)

    metrics.append((domain_uuid_string,
                    VNICS_KEY,
                    json.dumps(vnics)))
    metrics.append((domain_uuid_string,
                    VDISKS_KEY,
                    json.dumps(vdisks)))

    cpu_stats = libvirt_connection.get_cpu(domain_uuid_string)
    timestamp = cpu_stats.pop("timestamp")

    def _create_metric(stats, item_type, item_subtype=None):
        """Helper function to create and append to the metrics list"""
        for stat, value in stats.iteritems():

            if item_subtype is not None:
                stat = "{},{}".format(item_subtype, stat)

            key = "libvirt.{}[{}]".format(item_type, stat)
            metrics.append((domain_uuid_string,
                            key,
                            value,
                            timestamp))

    _create_metric(cpu_stats, "cpu")
    _create_metric(libvirt_connection.get_memory(domain_uuid_string), "memory")
    _create_metric(libvirt_connection.get_misc_attributes(
        domain_uuid_string), "instance")

    for vdisk in vdisks:
        stats = libvirt_connection.get_diskio(
            domain_uuid_string, vdisk["{#VDISK}"])
        _create_metric(stats, "disk", vdisk["{#VDISK}"])

    # 3. Gather metrics for all nics
    for vnic in vnics:
        stats = libvirt_connection.get_ifaceio(
            domain_uuid_string, vnic["{#VNIC}"])
        _create_metric(stats, "nic", vnic["{#VNIC}"])

    return metrics


def get_vnic_metrics(conn, domain_uuid_string, ifname, method):
    """
    возвращает счетчики интерфейсов
    """
    stats = conn.get_ifaceio(domain_uuid_string, ifname)
    logger.info(stats)
    return stats[method]


def get_vdisk_metrics(conn, domain_uuid_string, name, method):
    """
    возвращает счетчики интерфейсов
    """
    stats = conn.get_diskio(domain_uuid_string, name)
    logger.info(stats)
    return stats[method]


def get_cpu_metrics(conn, domain_uuid_string, method):
    """
    {'cpu_time': 1133335355000000, 'core_count': 2}
    """
    cpu_stats = conn.get_cpu(domain_uuid_string)
    logger.info(cpu_stats)
    return cpu_stats[method]


def get_memory_metrics(conn, domain_uuid_string, method):
    """
    возвращает счетчики интерфейсов
    """
    stats = conn.get_memory(domain_uuid_string)
    logger.info(stats)
    return stats[method]


def list_to_zbx(data):
    if not data:
        data = []
    return json.dumps({"data": data}, indent=2)


def main():
    args = parse_args()
    if args.out:
        logger.setLevel(logging.INFO)
    try:
        conn = LibvirtConnection(args.uri)
    except LibvirtConnectionError as error:
        # Log the failure to connect to a host, but continue processing
        logger.exception(error)
        return

    # domain discovery
    if args.action == "list":
        print(list_to_zbx(conn.discover_domains()))
    elif args.action == 'vnics':
        print(list_to_zbx(conn.discover_vnics(args.domain)))
    elif args.action == 'vdisks':
        print(list_to_zbx(conn.discover_vdisks(args.domain)))
    elif args.action == 'vnic':
        metrics = get_vnic_metrics(conn, args.domain, args.params, args.method)
        print(metrics)
    elif args.action == 'vdisk':
        metrics = get_vdisk_metrics(conn, args.domain, args.params, args.method)
        print(metrics)
    elif args.action == 'cpu':
        metrics = get_cpu_metrics(conn, args.domain, args.method)
        print(metrics)
    elif args.action == 'memory':
        metrics = get_memory_metrics(conn, args.domain, args.method)
        print(metrics)
    # elif args.action == 'misc':
    #     print(list_to_zbx(conn.get_misc_attributes(args.domain)))
    # elif args.action == 'metrics':
    #     metrics = get_instance_metrics(args.domain, conn)
    #     logger.info(metrics)
    #     print(json.dumps(metrics, indent=2))

    # for domain in domains:
    #     try:
    #         instance_attributes = libvirt_connection.get_misc_attributes(domain)
    #
    #         metrics = get_instance_metrics(domain, libvirt_connection)
    #         logger.info(metrics)
    #
    #     except DomainNotFoundError as error:
    #         # This may happen if a domain is deleted after we discover
    #         # it. In that case we log the error and move on.
    #         logger.error("Domain %s not found", domain)
    #         logger.exception(error)
    #
    # return domains


if __name__ == "__main__":
    main()
# usermod -aG libvirt zabbix