# Infrastructure & Rules
**Cluster Nodes:** pve0 (10.0.30.10/24), pve1 (10.0.30.11/24) | GW: 10.0.30.254 | DNS: 172.16.12.213, .214
**Storage:** ZFS RAID1 boot (sda,sdb); ZFS RAID10 'pve-data' (3 mirrors, write-through). *Rule: Always use 'pve-data' for new disks.*
**HA/Replication:** 2-node ZFS replication. *Rule: Replicate new VMs every 15m (`*/15`). If 1 node fails, run `pvecm expected 1` manually.*

## Networking
- **vmbr0 (Mgt):** Attach ONLY mgt/core components.
- **vmbr1 (Project):** Isolated test net. *Rule: All new project/test VMs MUST connect to `vmbr1`.*
- **vtnet1 (WAN Trunk):** VLAN 18 for firewall WAN.

## Core VMs
**OPNsense (VMID 100):**
- WAN: `vtnet1` (VLAN 18, DHCP).
- LAN: `vtnet0` (Bridge `vmbr1`, IP 192.168.1.1/24).
- Firewall: Pass DNS, Block RFC1918 (isolate from 10.x, 172.16.x, 192.168.x), Pass Internet. *Rule: NEVER bypass RFC1918 block.*
- Services: DHCP (192.168.1.100-199), Unbound DNS, Auto NAT.

## Rules
- **VMIDs:** Always call `get_next_id` right before creation. NEVER hardcode/reuse VMIDs.
- **Proxmox Rules:** The `ip6=slaac` format is invalid and must not be used. For rootfs/storage, always use the format `pve-data:4` (e.g., 4GB on pve-data pool) as instructed in the infra rules.
