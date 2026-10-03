SIMA PCIe Setup memo

[HOST]
#PCIe Hardware 
 https://developer.sima.ai/hardware/getting-started/pcie-mode

#Driver install
sima-cli install drivers/linux

#Verify and test
sima-cli device discover

#Virtual Network
shinko@simapc:~$ ip addr show dev veth-simaai
7: veth-simaai: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 65536 qdisc fq_codel state UNKNOWN group default qlen 1000
    link/ether 00:53:49:4d:41:30 brd ff:ff:ff:ff:ff:ff
    altname enp1s0
    inet 10.0.0.1/24 brd 10.0.0.255 scope global veth-simaai
       valid_lft forever preferred_lft forever
    inet6 fe80::253:49ff:fe4d:4130/64 scope link 
       valid_lft forever preferred_lft forever


#Software 

[HOST]
#neat install
network install or offline
sima-cli neat install sdk@release-2.1

#DevSync
sima-cli sdk setup --devkit {devkit-ip}

#pcie co-processing
 https://developer.sima.ai/software/tutorials/before-you-run
 
3. Set up PCIe tutorials
