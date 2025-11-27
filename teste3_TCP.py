import time
import argparse
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel, info
from mininet.link import TCLink

def configure_single_queue(net):
    """Configura uma fila única de 10Mbps, tratando todo o tráfego igualmente."""
    s1 = net.get('s1')
    interface = 's1-eth4'  # Interface onde a fila será aplicada

    info(f"*** (FAIR TEST) Criando Fila Única de 10Mbps em {interface}...\n")

    # Remove qualquer qdisc existente
    s1.cmd(f'tc qdisc del dev {interface} root')

    # Cria um qdisc HTB com classe padrão 10
    s1.cmd(f'tc qdisc add dev {interface} root handle 1: htb default 10')

    # Classe raiz da árvore HTB
    s1.cmd(f'tc class add dev {interface} parent 1: classid 1:1 htb rate 10mbit')

    # Classe única que usa toda a banda de 10 Mbps
    s1.cmd(f'tc class add dev {interface} parent 1:1 classid 1:10 htb rate 10mbit ceil 10mbit')

    info("*** Configuração de Fila Única concluída.\n")

def configure_qos_priority(net):
    """Configura QoS dando prioridade e banda mínima garantida a H2."""
    s1 = net.get('s1')
    interface = 's1-eth4'

    info(f"*** (QOS TEST) Aplicando QoS com prioridade para H2 em {interface}...\n")

    # Remove qdisc existente na interface
    s1.cmd(f'tc qdisc del dev {interface} root')

    # Cria HTB com classe padrão 20 (classe de baixa prioridade)
    s1.cmd(f'tc qdisc add dev {interface} root handle 1: htb default 20')

    # Classe pai com 10 Mbps totais
    s1.cmd(f'tc class add dev {interface} parent 1: classid 1:1 htb rate 10mbit')

    # Classe de ALTA prioridade (H2)
    s1.cmd(f'tc class add dev {interface} parent 1:1 classid 1:10 htb rate 6mbit ceil 10mbit prio 1')

    # Classe de BAIXA prioridade (H1 e H3)
    s1.cmd(f'tc class add dev {interface} parent 1:1 classid 1:20 htb rate 4mbit ceil 10mbit prio 2')

    # Filtros para direcionar cada IP para sua classe correspondente
    info("*** Aplicando filtros de IP de origem para cada classe...\n")

    # H2 → Alta prioridade
    s1.cmd(f'tc filter add dev {interface} protocol ip parent 1:0 prio 1 u32 match ip src 10.0.0.2 flowid 1:10')

    # H1 e H3 → Baixa prioridade
    s1.cmd(f'tc filter add dev {interface} protocol ip parent 1:0 prio 2 u32 match ip src 10.0.0.1 flowid 1:20')
    s1.cmd(f'tc filter add dev {interface} protocol ip parent 1:0 prio 2 u32 match ip src 10.0.0.3 flowid 1:20')

    info("*** Configuração de QoS com prioridade concluída.\n")

def disable_tcp_offloading(net):
    """Desativa offloading TCP nos hosts para evitar distorções de medição."""
    info("*** Desativando TCP Offloading nos hosts...\n")

    for host in net.hosts:
        host.cmd(f'ethtool -K {host.name}-eth0 tso off gso off')

def run_testTCP(test_type):
    # Define o controlador remoto Ryu/Opendaylight
    c0 = RemoteController('c0', ip='127.0.0.1', port=6653)

    # Cria rede Mininet com switches OVS e links TC
    net = Mininet(controller=c0, switch=OVSKernelSwitch, link=TCLink, autoSetMacs=True)
    net.addController(c0)

    info('*** Adicionando hosts e switches\n')

    # Hosts
    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')
    h3 = net.addHost('h3', ip='10.0.0.3/24')
    h4 = net.addHost('h4', ip='10.0.0.4/24')

    # Switches
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')

    info('*** Criando links\n')

    # Conexões hosts → s1
    net.addLink(h1, s1)
    net.addLink(h2, s1)
    net.addLink(h3, s1)

    # Link gargalo s1 ↔ s2
    net.addLink(s1, s2, port1=4, port2=1)

    # s2 → h4 (destino dos fluxos)
    net.addLink(s2, h4)

    info('*** Iniciando a rede\n')
    net.build()
    net.start()
    
    # Remove offloading para precisão dos resultados
    disable_tcp_offloading(net)

    # Aguarda switches reconectarem ao controlador
    info('*** Aguardando switches se conectarem ao controlador...\n')
    time.sleep(5)

    # Aplica configuração de QoS ou Fairness
    if test_type == 'qos':
        configure_qos_priority(net)
    else:
        configure_single_queue(net)

    info('*** Preparando o teste...\n')

    # Servidores iperf no H4 (1 porta para cada fluxo concorrente)
    h4.cmd('iperf -s -p 5001 -w 128K -i 1 > /tmp/iperf_h1_server.log &')
    h4.cmd('iperf -s -p 5002 -w 128K -i 1 > /tmp/iperf_h2_server.log &')
    h4.cmd('iperf -s -p 5003 -w 128K -i 1 > /tmp/iperf_h3_server.log &')
    
    info(f'*** INICIANDO TESTE COM COMPETIÇÃO TRIPLA (MODO: {test_type.upper()}) ***\n')

    # Fluxo 1 (H1) começa sozinho
    info("--> [Tempo 0s] Iniciando fluxo H1 -> H4 (1 competidor)\n")
    h1.cmd('iperf -c 10.0.0.4 -p 5001 -w 128K -t 50 &')
    
    # Fluxo 2 (H2) entra após 10s
    time.sleep(10)
    info("--> [Tempo 10s] Fluxo H2 ENTRA (2 competidores)\n")
    h2.cmd('iperf -c 10.0.0.4 -p 5002 -w 128K -t 30 &')
    
    # Fluxo 3 (H3) entra após 20s
    time.sleep(10)
    info("--> [Tempo 20s] Fluxo H3 ENTRA (3 competidores)\n")
    h3.cmd('iperf -c 10.0.0.4 -p 5003 -w 128K -t 20 &')
    
    # Tempo final de sincronização
    info("--> Testes em andamento... Aguardando 35 segundos para a conclusão final.\n")
    time.sleep(35)
    
    # Exibe resultados
    info('*** TESTE CONCLUÍDO ***\n\n')

    print('--- Resultados do Fluxo H1 -> H4 (Baixa Prioridade / Competidor) ---')
    print(h4.cmd('cat /tmp/iperf_h1_server.log'))

    print('--- Resultados do Fluxo H2 -> H4 (Alta Prioridade / Competidor) ---')
    print(h4.cmd('cat /tmp/iperf_h2_server.log'))

    print('--- Resultados do Fluxo H3 -> H4 (Baixa Prioridade / Competidor) ---')
    print(h4.cmd('cat /tmp/iperf_h3_server.log'))

    # Limpeza final
    h4.cmd('killall iperf')
    h4.cmd('rm /tmp/iperf_*.log')

    info('*** Parando a rede\n')
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')

    parser = argparse.ArgumentParser(description='Executa testes de competição TCP com e sem QoS.')
    parser.add_argument('--test', type=str, default='fair', choices=['fair', 'qos'],
                        help="Tipo de teste a ser executado: 'fair' para divisão justa, 'qos' para prioridade.")
    args = parser.parse_args()
    
    run_testTCP(args.test)
