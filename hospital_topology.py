from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel


def run():

    net = Mininet(
        controller=None,
        link=TCLink,
        autoSetMacs=True
    )

    # Ryu controller
    net.addController(
        'c0',
        controller=RemoteController,
        ip='127.0.0.1',
        port=6653
    )

    # Hosts
    h1 = net.addHost(
        'h1',
        ip='10.0.0.1/24'
    )

    h2 = net.addHost(
        'h2',
        ip='10.0.0.2/24'
    )

    # OpenFlow switches
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')
    s3 = net.addSwitch('s3', protocols='OpenFlow13')
    s4 = net.addSwitch('s4', protocols='OpenFlow13')

    # Host connections
    net.addLink(h1, s1, bw=100, delay='1ms')
    net.addLink(h2, s4, bw=100, delay='1ms')

    # Route A
    net.addLink(s1, s2, bw=100, delay='2ms')
    net.addLink(s2, s4, bw=100, delay='2ms')

    # Route B
    net.addLink(s1, s3, bw=100, delay='3ms')
    net.addLink(s3, s4, bw=100, delay='3ms')

    net.start()

    print("\n========== MedRoute ==========")
    print("Topology:")
    print("")
    print("        s2")
    print("       /  \\")
    print("h1 -- s1   s4 -- h2")
    print("       \\  /")
    print("        s3")
    print("")
    print("Path A: s1 -> s2 -> s4")
    print("Path B: s1 -> s3 -> s4")
    print("==============================\n")

    CLI(net)

    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    run()
