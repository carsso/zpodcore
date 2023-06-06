import json
import subprocess
from pathlib import Path

from jinja2 import Template
from sqlmodel import select

from zpodcommon import models as M
from zpodcommon.lib.network import INSTANCE_PUBLIC_SUB_NETWORKS_PREFIXLEN, MgmtIp
from zpodengine import settings
from zpodengine.lib import database


def get_json_from_file(filename: str):
    if not Path(filename).is_file():
        raise ValueError(f"The provided {filename} does not exist")
    with open(filename, "r") as f:
        return json.load(f)


def ovf_deployer(instance_component: M.InstanceComponent):
    c = instance_component.component
    i = instance_component.instance

    if "hostname" in instance_component.data:
        zpod_hostname = instance_component.data["hostname"]
    elif "last_octet" in instance_component.data:
        zpod_hostname = f"{c.component_name}{instance_component.data['last_octet']}"
    else:
        zpod_hostname = c.component_name

    # Open Component JSON file
    f = open(c.jsonfile)

    # Load component JSON
    cjson = json.load(f)

    # Load govc deploy spec
    govc_spec = cjson["component_deploy_govc_spec"]

    # Fetch component IP address from instance
    component_ipaddress = MgmtIp.instance_component(instance_component).ip
    # With kelby new network.py fix, i might not need this anymore.
    # if component_ipaddress is None:
    # "esxi" component type is the only one to have this specific config
    # as we deploy multiples component of it per instance
    # component_ipaddress = f"{subnet} + {instance_component.extra_id}"
    zpod_netmask = MgmtIp.instance_component(instance_component).netmask

    # Fetch component default gw from instance
    component_gateway = MgmtIp.instance(i, "gw").ip

    with database.get_session_ctx() as session:
        setting_zpodfactory_host = session.exec(
            select(M.Setting).where(M.Setting.name == "zpodfactory_host")
        ).one()
        zpodfactory_host = setting_zpodfactory_host.value

        if c.component_name in ["zbox", "vyos"]:
            # zpodfactory is the main DNS server for every instance and links to zbox/vyos
            # as DNS servers for their respective subdomain.
            #
            # For those 2 components, the DNS Server must be the zpodfactory_host.
            zpod_dns = zpodfactory_host
        else:
            # all other components rely on zbox/vyos as their DNS server.
            zpod_dns = MgmtIp.instance(i, "zbox").ip

        setting_zpodfactory_ssh_key = session.exec(
            select(M.Setting).where(M.Setting.name == "zpodfactory_ssh_key")
        ).one()
        zpodfactory_ssh_key = setting_zpodfactory_ssh_key.value

    print(f"Component Nested: {cjson['component_isnested']}")

    if cjson["component_isnested"] is False:
        print(f"[L1] Deployment for {c.component_name}")
        # This means we deploy on the physical endpoint vSphere env
        hostname = i.endpoint.endpoints["compute"]["hostname"]
        username = i.endpoint.endpoints["compute"]["username"]
        password = i.endpoint.endpoints["compute"]["password"]
        datastore = i.endpoint.endpoints["compute"]["storage_datastore"]
        site_id = settings.SITE_ID
        resource_pool = f"{site_id}-{i.name}"
        zpod_portgroup = f"{site_id}-{i.name}-segment"

    else:
        print(f"[L2] Deployment for {c.component_name}")
        # This means we deploy the component as a nested L2 VM from the instance
        # vSphere env
        hostname = f"vcsa.{i.domain}"
        username = f"administrator@{i.domain}"
        password = i.password

        # For now this is hardcoded unless anything changes
        resource_pool = "Cluster"
        # For now this is hardcoded unless anything changes
        # (maybe vSAN OSA/ESA support in the future instead of NFS-01)
        datastore = "NFS-01"
        zpod_portgroup = "VM Network"

    url = f"https://{username}:{password}@{hostname}/sdk"
    print(f"Deploying to [https://{username}:XXXXXXXX@{hostname}/sdk]...")

    t = Template(json.dumps(govc_spec))
    govc_spec_render = t.render(
        zpod_hostname=zpod_hostname,
        zpod_ipaddress=component_ipaddress,
        zpod_netmask=zpod_netmask,
        zpod_netprefix=INSTANCE_PUBLIC_SUB_NETWORKS_PREFIXLEN,
        zpod_gateway=component_gateway,
        zpod_dns=zpod_dns,
        zpod_ntp=zpodfactory_host,
        zpod_domain=i.domain,
        zpod_password=i.password,
        zpod_sshkey=zpodfactory_ssh_key,
        zpod_portgroup=zpod_portgroup,
    )

    print("govc ovf property options generated file")
    print(govc_spec_render)

    vm_name = f"{zpod_hostname}.{i.domain}"

    options_filename = f"/tmp/{vm_name}.json"
    with open(options_filename, "w") as f:
        f.write(govc_spec_render)

    cmd = (
        "govc import.ova"
        " -k"
        f" -name={vm_name}"
        f" -u='{url}'"
        f" -pool={resource_pool}"
        f" -ds={datastore}"
        " -json=true"  # this avoids prefect crashing on the live output
        f" -options={options_filename}"
        f" /products/{c.component_name}/{c.component_version}/{c.filename}"
    )
    print("govc deploy command")
    print(cmd)

    try:
        h = subprocess.run(
            args=cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            check=False,
        )
        print(h)
        if h.returncode != 0:
            return RuntimeError(message=f"govc error: {h.stderr}")

    except subprocess.CalledProcessError as e:
        print(e.output)
