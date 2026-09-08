from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel


def create_network():

    net = Mininet(
        controller=RemoteController,
        link=TCLink,
        autoSetMacs=True
    )

    c0 = net.addController(
        'c0',
        controller=RemoteController,
        ip='127.0.0.1',
        port=6653
    )

    # Hosts
    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')

    # Switches
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')
    s3 = net.addSwitch('s3', protocols='OpenFlow13')
    s4 = net.addSwitch('s4', protocols='OpenFlow13')

    # Host links
    net.addLink(h1, s1)
    net.addLink(h2, s4)

        # Path 1
    net.addLink(s1, s2)
    net.addLink(s2, s4)

    # Path 2 - temporarily disabled
    # net.addLink(s1, s3)
    # net.addLink(s3, s4)
    net.start()

    print("\nNetwork started.")
    print("Try: pingall\n")

    CLI(net)

    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    create_network()
