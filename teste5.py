import time
import random
import argparse
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel, info
from mininet.link import TCLink
from mininet.cli import CLI

def apply_bottleneck_limit(net, interface='s1-eth6', bandwidth_mbit=100):
    """Cria um gargalo artificial no link s1-eth6 usando HTB."""
    s1 = net.get('s1')
    info(f"*** Aplicando limite de {bandwidth_mbit}Mbit em {interface} via tc-htb\n")

    # Remove qdisc anterior
    s1.cmd(f'tc qdisc del dev {interface} root 2>/dev/null')

    # HTB básico com uma única classe
    s1.cmd(f'tc qdisc add dev {interface} root handle 1: htb default 10')
    s1.cmd(f'tc class add dev {interface} parent 1: classid 1:1 htb '
           f'rate {bandwidth_mbit}mbit ceil {bandwidth_mbit}mbit')

    # Classe que realmente transporta o tráfego
    s1.cmd(f'tc class add dev {interface} parent 1:1 classid 1:10 htb '
           f'rate {bandwidth_mbit}mbit ceil {bandwidth_mbit}mbit')

    info("*** Limite de banda aplicado\n")


def configure_fifo(net):
    """Configura uma FIFO simples no gargalo."""
    s1 = net.get('s1')
    interface = 's1-eth6'
    info(f"*** Aplicando FIFO em {interface}\n")

    # FIFO anexada à classe 1:10 (da HTB criada anteriormente)
    s1.cmd(f'ttc qdisc add dev {interface} parent 1:10 handle 100: pfifo limit 100')

    info("*** FIFO concluído\n")


def configure_htb(net):
    """Árvore HTB completa: VoIP (alta), vídeo (média) e resto (baixa)."""
    s1 = net.get('s1')
    interface = 's1-eth6'
    info(f"*** Aplicando hierarquia HTB em {interface}\n")

    # Remove qualquer qdisc antigo
    s1.cmd(f'tc qdisc del dev {interface} root 2>/dev/null')

    # HTB raiz
    s1.cmd(f'tc qdisc add dev {interface} root handle 1: htb default 30')
    s1.cmd(f'tc class add dev {interface} parent 1: classid 1:1 htb '
           f'rate 100mbit ceil 100mbit')

    # Classe 1:10 = VoIP (alta prioridade)
    s1.cmd(f'tc class add dev {interface} parent 1:1 classid 1:10 htb '
           f'rate 1mbit ceil 100mbit prio 1')

    # Classe 1:20 = Vídeo (média)
    s1.cmd(f'tc class add dev {interface} parent 1:1 classid 1:20 htb '
           f'rate 10mbit ceil 100mbit prio 2')

    # Classe 1:30 = Tráfego restante (baixa)
    s1.cmd(f'tc class add dev {interface} parent 1:1 classid 1:30 htb '
           f'rate 89mbit ceil 100mbit prio 3')

    # FQ-CODEL interno para fairness
    s1.cmd(f'tc qdisc add dev {interface} parent 1:10 handle 10: fq_codel')
    s1.cmd(f'tc qdisc add dev {interface} parent 1:20 handle 20: fq_codel')
    s1.cmd(f'tc qdisc add dev {interface} parent 1:30 handle 30: fq_codel')

    # Filtros por porta para separar VoIP e Vídeo
    s1.cmd(f'tc filter add dev {interface} protocol ip parent 1:0 prio 1 '
           f'u32 match ip src 10.0.0.3/32 match ip dport 5003 0xffff flowid 1:10')

    s1.cmd(f'tc filter add dev {interface} protocol ip parent 1:0 prio 2 '
           f'u32 match ip src 10.0.0.3/32 match ip dport 5004 0xffff flowid 1:20')

    info("*** HTB concluído\n")


def configure_fq_codel(net):
    """Configura uma fila única FQ-CODEL no gargalo."""
    s1 = net.get('s1')
    interface = 's1-eth6'
    info(f"*** Aplicando FQ-CODEL em {interface}\n")

    s1.cmd(f'tc qdisc add dev {interface} parent 1:10 handle 100: fq_codel')

    info("*** FQ-CODEL concluído\n")


def disable_offloading(net):
    """Desliga offloading para garantir medições consistentes."""
    info("*** Desativando offloading em hosts e switches...\n")

    for node in net.hosts + net.switches:
        for intf in node.intfList():
            if 'lo' not in intf.name:
                node.cmd(f'ethtool -K {intf.name} tso off gso off gro off')

    info("*** Offloading desativado\n")


def run_test(mode):
    """Cria a topologia, ativa o QoS e executa todos os fluxos."""
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch,
                  autoSetMacs=True, link=TCLink)

    info('*** Adicionando controlador\n')
    c0 = net.addController('c0', controller=RemoteController,
                           ip='127.0.0.1', port=6653)

    info('*** Adicionando hosts e switches\n')
    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')
    h3 = net.addHost('h3', ip='10.0.0.3/24')
    h4 = net.addHost('h4', ip='10.0.0.4/24')
    h5 = net.addHost('h5', ip='10.0.0.5/24')
    h6 = net.addHost('h6', ip='10.0.0.6/24')

    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')

    info('*** Criando links com as larguras de banda definidas\n')
    net.addLink(h1, s1, port2=1, bw=100)
    net.addLink(h2, s1, port2=2, bw=100)
    net.addLink(h3, s1, port2=3, bw=100)
    net.addLink(h4, s1, port2=4, bw=100)
    net.addLink(h5, s1, port2=5, bw=100)

    net.addLink(s1, s2, port1=6, port2=1, bw=100)   # gargalo
    net.addLink(s2, h6, port1=2, bw=1000)           # link rápido

    info('*** Iniciando rede\n')
    net.build()
    net.start()
    time.sleep(2)

    disable_offloading(net)

    bottleneck = 's1-eth6'

    # FIFO e FQ-CODEL precisam da classe HTB anterior
    if mode != 'htb':
        apply_bottleneck_limit(net, interface=bottleneck, bandwidth_mbit=100)

    # Seleção do algoritmo
    if mode == 'htb':
        configure_htb(net)
    elif mode == 'fq_codel':
        configure_fq_codel(net)
    else:
        configure_fifo(net)

    # Captura de pacotes no gargalo
    info(f'*** Iniciando tcpdump em {bottleneck}...\n')
    s1.cmd(f'tcpdump -i {bottleneck} -w /tmp/bottleneck_capture_{mode}.pcap &')
    tcpdump_pid = s1.cmd('echo $!')

    info('*** Iniciando servidores iperf em H6\n')

    # Cada porta corresponde a um tipo de tráfego
    h6.cmd('iperf -s -p 5001 -i 1 &')  # H1
    h6.cmd('iperf -s -p 5002 -i 1 &')  # H2
    h6.cmd('iperf -s -u -p 5003 -i 1 &')  # VoIP
    h6.cmd('iperf -s -u -p 5004 -i 1 &')  # Vídeo
    h6.cmd('iperf -s -p 5005 -i 1 &')  # H4
    h6.cmd('iperf -s -p 5006 -i 1 &')  # H5

    TEST_DURATION = 60
    script_start = time.time()

    # H1 - TCP bulk
    info("[T=0s] Iniciando H1 (TCP Bulk)\n")
    h1.cmd(f'iperf -c {h6.IP()} -p 5001 -t {TEST_DURATION} &')
    time.sleep(2)

    # H2 - TCP persistente
    info("[T=2s] Iniciando H2 (TCP Persistente)\n")
    h2.cmd(f'iperf -c {h6.IP()} -p 5002 -t {TEST_DURATION - 5} &')
    time.sleep(2)

    # H3 - VoIP + Vídeo
    info("[T=4s] Iniciando H3 (UDP VoIP + Vídeo)\n")
    h3.cmd(f'iperf -c {h6.IP()} -p 5003 -u -b 128k -l 160 -t {TEST_DURATION - 4} &')
    h3.cmd(f'iperf -c {h6.IP()} -p 5004 -u -b 5M -t {TEST_DURATION - 4} &')
    time.sleep(2)

    # H4 - outro TCP bulk
    info("[T=6s] Iniciando H4 (TCP Bulk)\n")
    h4.cmd(f'iperf -c {h6.IP()} -p 5005 -t {TEST_DURATION - 6} &')
    time.sleep(2)

    # Gerador aleatório de fluxos curtos (mice flows)
    info("[T=8s] Iniciando gerador de mice flows\n")
    mice_start = time.time()
    while time.time() - mice_start < (TEST_DURATION - 10):

        # H2 mice
        size = random.randint(10, 1000) * 1024
        info(f'*** [T={time.time() - script_start:.1f}s] H2 mice: {size/1024:.0f} KB\n')
        h2.cmd(f'iperf -c {h6.IP()} -p 5002 -n {size} &')

        # H5 mice
        size2 = random.randint(10, 1000) * 1024
        info(f'*** [T={time.time() - script_start:.1f}s] H5 mice: {size2/1024:.0f} KB\n')
        h5.cmd(f'iperf -c {h6.IP()} -p 5006 -n {size2} &')

        time.sleep(random.uniform(1, 3))

    info(f'*** Aguardando {TEST_DURATION}s para finalizar...\n')
    time.sleep(TEST_DURATION + 5)

    # Para tcpdump
    info('*** Parando tcpdump...\n')
    s1.cmd(f'kill {tcpdump_pid.strip()}')

    # Limpeza
    info('*** Encerrando iperf\n')
    h6.cmd('killall iperf')

    CLI(net)
    net.stop()
    info('*** Rede parada ***\n')


if __name__ == '__main__':
    setLogLevel('info')
    parser = argparse.ArgumentParser(description="Simulação de Rede Corporativa com QoS")
    parser.add_argument('--mode', choices=['fifo','htb','fq_codel'], default='fifo',
                        help='Algoritmo de enfileiramento usado no gargalo.')
    args = parser.parse_args()
    run_test(args.mode)
