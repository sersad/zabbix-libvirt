"""
This file holds the class that creates a connection to libvirt and provides
various methods to get useful information
"""
import sys
import time
from xml.etree import ElementTree
import libvirt


class LibvirtConnectionError(Exception):
    """Error to indicate something went wrong with the LibvirtConnection class"""
    pass


class DomainNotFoundError(Exception):
    """Error to indicate something went wrong with the LibvirtConnection class"""
    pass


class LibvirtConnection(object):
    """This class opens a connection to libvirt and provides with methods
    to get useuful information about domains.
    """

    @staticmethod
    def libvirt_callback(userdata, err):
        """Error handler"""
        pass

    def __init__(self, uri=None):
        """Creates a read only connection to libvirt"""
        try:
            self.conn = libvirt.openReadOnly(uri)
        except libvirt.libvirtError as error:
            raise LibvirtConnectionError(error)
        if self.conn is None:
            raise LibvirtConnectionError(
                "Failed to open connection to the hypervisor: " + str(uri))

        # We set this because when libvirt errors are raised, they are still
        # printed to console (stderr) even if you catch them.
        # This is a problem with libvirt API.
        # See https://stackoverflow.com/questions/45541725/avoiding-console-prints-by-libvirt-qemu-python-apis
        libvirt.registerErrorHandler(f=self.libvirt_callback, ctx=None)

    def _get_domain_by_uuid(self, domain_uuid_string):
        """Find the domain by uuid and return domain object"""
        try:
            domain = self.conn.lookupByUUIDString(domain_uuid_string)
        except libvirt.libvirtError:
            raise DomainNotFoundError(
                "Failed to find domain: " + domain_uuid_string)
        return domain

    def discover_domains(self):
        """Return all domains"""
        domains = self.conn.listAllDomains()
        return [{"{#DOMAINUUID}": domain.UUIDString(), "{#DOMAINNAME}": domain.name()} for domain in domains]

    def _get_domain_xmldump(self, domain_uuid_string):
        """Return domain xml dump"""
        domain = self._get_domain_by_uuid(domain_uuid_string)
        return ElementTree.fromstring(domain.XMLDesc())

    def _get_instance_attributes(self, domain_uuid_string):
        """Returns openstack specific instance attributes"""
        tree = self._get_domain_xmldump(domain_uuid_string)

        namespaces = {"nova": "http://openstack.org/xmlns/libvirt/nova/1.0"}
        element = tree.find("metadata/nova:instance/nova:owner", namespaces)

        if element is None:
            return {"user_uuid": "non-openstack-instance",
                    "project_uuid": "non-openstack-instance",
                    "user_name": "non-openstack-instance",
                    "project_name": "non-openstack-instance"}

        user_uuid = element.find("nova:user", namespaces).get("uuid")
        project_uuid = element.find("nova:project", namespaces).get("uuid")
        user_name = element.find("nova:user", namespaces).text
        project_name = element.find("nova:project", namespaces).text

        return {"user_uuid": user_uuid,
                "project_uuid": project_uuid,
                "user_name": user_name,
                "project_name": project_name}

    def discover_vnics(self, domain_uuid_string):
        """Discover all virtual NICs on a domain.

        Returns a list of dictionary with "{#VNIC}"s name and domain's uuid"""
        tree = self._get_domain_xmldump(domain_uuid_string)
        elements = tree.findall('devices/interface/target')
        return [{"{#VNIC}": element.get('dev')} for element in elements]

    def discover_vdisks(self, domain_uuid_string):
        """Discover all virtual disk drives on a domain.

        Returns a list of dictionary with "{#VDISK}"s name and domain's uuid"""
        tree = self._get_domain_xmldump(domain_uuid_string)
        elements = tree.findall('devices/disk/target')
        return [{"{#VDISK}": element.get('dev')} for element in elements]

    def get_memory(self, domain_uuid_string):
        """Get memorystats for domain.

        Here's a mapping of what the output from
        virsh / libvirt means to what is displayed by linux's `free` command.

        available = total
        unused = free
        usable = available
        actual = Current memory allocated to the VM(it's not the same as total in `free` command).

        The API returns the output in KiB, so we multiply by 1024 to return bytes for zabbix.
        """
        domain = self._get_domain_by_uuid(domain_uuid_string)

        try:
            stats = domain.memoryStats()
        except libvirt.libvirtError:
            # If the domain is not running, then the memory usage is 0.
            # If the error is due to other reasons, then re-raise the error.
            if domain.isActive():
                raise
            else:
                return {"free": 0, "available": 0, "current_allocation": 0}

        return {"free": stats.get("unused", 0) * 1024,
                "available": stats.get("usable", 0) * 1024,
                "current_allocation": stats.get("actual", 0) * 1024}

    def get_misc_attributes(self, domain_uuid_string):
        """Get virtualization host's hostname and combine it with openstack
        specific instance attributes"""

        domain = self._get_domain_by_uuid(domain_uuid_string)
        instance_attributes = self._get_instance_attributes(domain_uuid_string)

        instance_attributes["virt_host"] = self.conn.getHostname()
        instance_attributes["name"] = domain.name()
        instance_attributes["active"] = self.is_active(domain_uuid_string)

        return instance_attributes

    def get_cpu(self, domain_uuid_string):
        """Get CPU statistics. Libvirt returns the stats in nanoseconds.

        Returns the cpu time in nanoseconds.
        Caller has to do the math to calculate percentage utilization.

        See the stack overflow article to understand what it means.
        https://stackoverflow.com/questions/40468370/what-does-cpu-time-represent-exactly-in-libvirt
        """
        domain = self._get_domain_by_uuid(domain_uuid_string)

        info = domain.info()
        timestamp = time.time()

        return {"cpu_time": int(info[4] / info[3]),
                "core_count": info[3],
                "timestamp": timestamp}

    def get_ifaceio(self, domain_uuid_string, iface):
        """Get Network I / O"""
        domain = self._get_domain_by_uuid(domain_uuid_string)

        try:
            stats = domain.interfaceStats(iface)
        except libvirt.libvirtError:
            if domain.isActive():
                raise
            else:
                return {"read": 0, "write": 0}

        return {"read": str(stats[0]), "write": str(stats[4])}

    def get_diskio(self, domain_uuid_string, disk):
        """Get Disk I / O"""
        domain = self._get_domain_by_uuid(domain_uuid_string)

        try:
            stats = domain.blockStatsFlags(disk)
        except libvirt.libvirtError:
            if domain.isActive():
                raise
            else:
                return {'wr_total_times': 0, 'rd_operations': 0,
                        'flush_total_times': 0, 'rd_total_times': 0,
                        'rd_bytes': 0, 'flush_operations': 0,
                        'wr_operations': 0, 'wr_bytes': 0}

        return stats

    def is_active(self, domain_uuid_string):
        """Returns 1 if domain is active, 0 otherwise."""
        domain = self._get_domain_by_uuid(domain_uuid_string)
        return domain.isActive()


if __name__ == "__main__":
    print("Main called")
