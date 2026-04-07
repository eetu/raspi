#!/bin/sh
# Updates the router ip6tables FORWARD rule to allow WireGuard (UDP 51820) to
# the Pi's current global IPv6 address. Called by the Pi's DDNS script via SSH
# whenever the IPv6 prefix changes, and on router boot via firewall-start.
#
# Setup:
#   1. Set PI_MAC to the Pi's Ethernet MAC (ip link show eth0 | grep ether)
#   2. Copy to router:
#        scp files/router-update-wg-firewall.sh USER@ROUTER:/jffs/scripts/update-wg-firewall.sh
#        ssh USER@ROUTER chmod +x /jffs/scripts/update-wg-firewall.sh
#   3. Persist across reboots — add to firewall-start on the router:
#        echo "/jffs/scripts/update-wg-firewall.sh" >> /jffs/scripts/firewall-start
#        chmod +x /jffs/scripts/firewall-start
#   4. Add to authorized_keys on the router for the Pi's DDNS SSH key:
#        command="/jffs/scripts/update-wg-firewall.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding ssh-ed25519 AAAA... raspi-ddns

PI_MAC="xx:xx:xx:xx:xx:xx"  # Pi's Ethernet MAC — find with: ip link show eth0 | grep ether

NEW_IP=$(ip -6 neigh show | awk "/${PI_MAC}/ && /^2/ {print \$1}" | head -1)
if [ -z "$NEW_IP" ]; then
    logger "update-wg-firewall: Pi global IPv6 not found in neighbor table"
    exit 1
fi

ip6tables -L FORWARD --line-numbers -n | awk '/dpt:51820/{print $1}' | sort -rn | while read n; do
    ip6tables -D FORWARD "$n"
done
ip6tables -I FORWARD -p udp --dport 51820 -d "$NEW_IP" -j ACCEPT
logger "update-wg-firewall: set WireGuard to $NEW_IP"
