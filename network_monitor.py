import time
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, RemoteController
from mininet.link import TCLink
from mininet.log import setLogLevel

def run():

    net = Mininet(
        switch=OVSKernelSwitch,
        link=TCLink,
        controller=None
    )

    net.addController(
        "c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6653
    )

    h1 = net.addHost("h1", ip="10.0.0.1")
    h2 = net.addHost("h2", ip="10.0.0.2")

    s1 = net.addSwitch("s1")
    s2 = net.addSwitch("s2")
    s3 = net.addSwitch("s3")
    s4 = net.addSwitch("s4")

    net.addLink(h1, s1)
    net.addLink(s1, s2)
    net.addLink(s2, s4)
    net.addLink(s1, s3)
    net.addLink(s3, s4)
    net.addLink(s4, h2)

    net.start()
    time.sleep(5)
    print("============================")
    print("MedRoute Network Monitoring")
    print("============================")

    print("\nPing Test:")
    print(h1.cmd("ping -c 4 10.0.0.2"))

    print("\nNetwork Interface Statistics:")
    print(h1.cmd("ifconfig"))
    print(h2.cmd("ifconfig"))
    input("\npress enter to stop mininet")
    net.stop()

if __name__ == "__main__":
    setLogLevel("info")
    run()
